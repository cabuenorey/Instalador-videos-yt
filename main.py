import os
import sys
import json
import yt_dlp

# Aseguramos que los binarios de FFmpeg estén disponibles automáticamente
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except ImportError:
    pass

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def get_default_downloads_dir() -> str:
    """
    Obtiene la ruta por defecto: carpeta 'Descargas' junto al script.
    Si no existe en el disco, la crea.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    downloads_dir = os.path.join(script_dir, "Descargas")
    if not os.path.isdir(downloads_dir):
        alt_dir = os.path.join(script_dir, "descargas")
        if os.path.isdir(alt_dir):
            downloads_dir = alt_dir
        else:
            os.makedirs(downloads_dir, exist_ok=True)
    return os.path.abspath(downloads_dir)


def load_config() -> dict:
    """Carga el archivo de configuración persistente config.json."""
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def save_config(config: dict):
    """Guarda los cambios en config.json."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[!] No se pudo guardar la configuración: {e}")


def get_current_download_dir() -> str:
    """
    Obtiene la carpeta de descargas activa.
    Si ya había una guardada previamente, la utiliza.
    Si no existe o no es válida, usa la carpeta por defecto.
    """
    config = load_config()
    saved_path = config.get("download_dir")
    if saved_path and os.path.isdir(saved_path):
        return os.path.abspath(saved_path)

    default_dir = get_default_downloads_dir()
    set_current_download_dir(default_dir)
    return default_dir


def set_current_download_dir(path: str):
    """Establece y guarda de forma persistente la carpeta de descargas."""
    abs_path = os.path.abspath(path)
    config = load_config()
    config["download_dir"] = abs_path
    save_config(config)


def reset_to_default_download_dir() -> str:
    """Restablece la carpeta de descargas a la carpeta 'Descargas' por defecto."""
    default_dir = get_default_downloads_dir()
    set_current_download_dir(default_dir)
    return default_dir


def pick_folder_gui() -> str | None:
    """Abre un diálogo visual de Windows para seleccionar una carpeta."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="Selecciona la carpeta para guardar las descargas")
        root.destroy()
        return selected if selected else None
    except Exception as e:
        print(f"[!] No se pudo abrir el explorador gráfico: {e}")
        return None


def format_duration(seconds) -> str:
    """Convierte segundos a formato legible mm:ss o hh:mm:ss."""
    if not seconds:
        return "Desconocida"
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def format_size(bytes_val) -> str:
    """Convierte bytes a formato legible (MB / GB)."""
    if not bytes_val:
        return "Tamaño variable"
    mb = bytes_val / (1024 * 1024)
    if mb >= 1024:
        return f"~{mb / 1024:.2f} GB"
    return f"~{mb:.1f} MB"


def extract_video_qualities(info: dict) -> list:
    """
    Extrae las resoluciones de video disponibles sin duplicados molestos,
    ordenadas de mayor a menor calidad.
    """
    formats = info.get("formats", [])
    duration = info.get("duration") or 0

    best_audio_size = 0
    for f in formats:
        if f.get("vcodec") == "none" and f.get("acodec") != "none":
            sz = f.get("filesize") or f.get("filesize_approx")
            if sz and sz > best_audio_size:
                best_audio_size = sz
    if not best_audio_size and duration:
        best_audio_size = int((128 * 1000 / 8) * duration)

    heights = {}
    for f in formats:
        h = f.get("height")
        if not h or f.get("vcodec") == "none":
            continue

        is_https = 2 if f.get("protocol") == "https" else 1
        vcodec = f.get("vcodec") or ""
        is_avc = 2 if vcodec.startswith("avc") else 1
        fps = f.get("fps") or 0
        tbr = f.get("tbr") or 0
        score = (is_https, fps, is_avc, tbr)

        if h not in heights or score > heights[h]["score"]:
            heights[h] = {"format": f, "score": score}

    qualities = []
    for h in sorted(heights.keys(), reverse=True):
        f = heights[h]["format"]
        fps = f.get("fps")
        fps_str = f"{round(fps)} fps" if fps else ""

        v_size = f.get("filesize") or f.get("filesize_approx")
        if not v_size and f.get("tbr") and duration:
            v_size = int((f.get("tbr") * 1000 / 8) * duration)

        qualities.append({
            "height": h,
            "fps": fps_str,
            "video_size": v_size,
            "total_size": (v_size + best_audio_size) if v_size else None
        })
    return qualities


def ask_user_choice(prompt: str, valid_options: list) -> str:
    """Solicita una opción al usuario y valida que sea una de las permitidas."""
    while True:
        choice = input(prompt).strip()
        if choice in valid_options:
            return choice
        print(f"[!] Opción inválida. Opciones disponibles: {', '.join(valid_options)}")


def process_download(url: str, downloads_dir: str):
    """
    Obtiene los datos del video, pregunta el tipo de descarga, lista las calidades
    y ejecuta la descarga en la carpeta destino seleccionada.
    """
    os.makedirs(downloads_dir, exist_ok=True)
    output_template = os.path.join(downloads_dir, "%(title)s.%(ext)s")

    print("\n[+] Analizando información del video en YouTube...")
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        print(f"[x] Error al obtener información del video: {e}\n")
        return

    title = info.get("title", "Video")
    duration = format_duration(info.get("duration"))
    uploader = info.get("uploader", "Desconocido")

    print("\n" + "=" * 60)
    print(f"Título:   {title}")
    print(f"Canal:    {uploader}")
    print(f"Duración: {duration}")
    print(f"Destino:  {downloads_dir}")
    print("=" * 60)

    # 1. Menú de tipo de descarga
    print("\n¿Qué deseas descargar?")
    print(" [1] Video completo (Video + Audio) [MP4]")
    print(" [2] Solo video (Sin audio) [MP4]")
    print(" [3] Solo audio (Sin video)")

    download_type = ask_user_choice("\nSelecciona una opción (1, 2 o 3): ", ["1", "2", "3"])

    # Caso 1 y Caso 2: Video completo o Solo video
    if download_type in ["1", "2"]:
        qualities = extract_video_qualities(info)
        if not qualities:
            print("[!] No se encontraron calidades específicas de video. Se descargará la mejor calidad por defecto.")
            selected_height = None
        else:
            is_full_video = (download_type == "1")
            mode_name = "Video completo (Video + Audio)" if is_full_video else "Solo video (Sin audio)"

            print(f"\nCalidades disponibles para {mode_name}:")
            print(" [0] Mejor calidad disponible (Automático)")

            valid_opts = ["0"]
            for idx, q in enumerate(qualities, 1):
                valid_opts.append(str(idx))
                size_val = q["total_size"] if is_full_video else q["video_size"]
                size_str = format_size(size_val)
                fps_part = f"({q['fps']})" if q["fps"] else ""
                print(f" [{idx}] {q['height']}p {fps_part:<10} - {size_str}")

            choice = ask_user_choice("\nElige la calidad que deseas descargar: ", valid_opts)
            if choice == "0":
                selected_height = None
            else:
                selected_height = qualities[int(choice) - 1]["height"]

        if download_type == "1":
            # Video completo (MP4)
            if selected_height:
                fmt = (
                    f"bestvideo[height={selected_height}][vcodec^=avc]+bestaudio[ext=m4a]/"
                    f"bestvideo[height={selected_height}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo[height={selected_height}]+bestaudio/"
                    f"bestvideo[height<={selected_height}]+bestaudio/best"
                )
            else:
                fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"

            ydl_opts = {
                "format": fmt,
                "merge_output_format": "mp4",
                "outtmpl": output_template,
                "noplaylist": True,
            }
        else:
            # Solo video sin audio (MP4)
            if selected_height:
                fmt = (
                    f"bestvideo[height={selected_height}][vcodec^=avc][ext=mp4]/"
                    f"bestvideo[height={selected_height}][ext=mp4]/"
                    f"bestvideo[height={selected_height}]/best[height={selected_height}]"
                )
            else:
                fmt = "bestvideo[ext=mp4]/bestvideo/best"

            ydl_opts = {
                "format": fmt,
                "outtmpl": output_template,
                "noplaylist": True,
                "postprocessors": [{
                    "key": "FFmpegVideoRemuxer",
                    "preferedformat": "mp4",
                }],
            }

    # Caso 3: Solo audio
    else:
        dur_secs = info.get("duration") or 0

        def est_audio_size(kbps):
            if not dur_secs:
                return "Tamaño variable"
            bytes_est = int((kbps * 1000 / 8) * dur_secs)
            return format_size(bytes_est)

        print("\nCalidades de audio disponibles:")
        print(f" [1] MP3 - 320 kbps (Máxima fidelidad)           - {est_audio_size(320)}")
        print(f" [2] MP3 - 256 kbps (Muy alta calidad)          - {est_audio_size(256)}")
        print(f" [3] MP3 - 192 kbps (Alta calidad - Recomendado) - {est_audio_size(192)}")
        print(f" [4] MP3 - 128 kbps (Calidad estándar)          - {est_audio_size(128)}")
        print(f" [5] MP3 - 64 kbps  (Archivo ligero)            - {est_audio_size(64)}")
        print(" [6] M4A / AAC - Original nativo de YouTube      - Mejor fuente directa")

        audio_choice = ask_user_choice("\nElige la calidad de audio: ", ["1", "2", "3", "4", "5", "6"])

        if audio_choice in ["1", "2", "3", "4", "5"]:
            bitrates_map = {
                "1": "320",
                "2": "256",
                "3": "192",
                "4": "128",
                "5": "64",
            }
            chosen_bitrate = bitrates_map[audio_choice]
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": output_template,
                "noplaylist": True,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": chosen_bitrate,
                }],
            }
        else:
            # M4A nativo sin reconversión
            ydl_opts = {
                "format": "bestaudio[ext=m4a]/bestaudio/best",
                "outtmpl": output_template,
                "noplaylist": True,
            }

    # Ejecución de la descarga
    print("\nIniciando descarga...")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        print("\n" + "=" * 60)
        print("¡DESCARGA COMPLETADA CON ÉXITO!")
        print(f"Archivo guardado en: {downloads_dir}")
        print("=" * 60 + "\n")
    except Exception as e:
        print(f"\n[x] Ocurrió un error durante la descarga: {e}\n")


def download_flow():
    """Pantalla para ingresar enlace y procesar descargas."""
    downloads_dir = get_current_download_dir()
    print("\n" + "-" * 60)
    print(f"MODO DESCARGA | Carpeta activa: {downloads_dir}")
    print("-" * 60)

    while True:
        url = input("Ingresa el enlace del video de YouTube (o 'm' para volver al menú): ").strip()
        if not url:
            print("[!] No ingresaste ningún enlace.")
            continue
        if url.lower() in ["m", "menu", "volver", "c", "cancelar"]:
            print("")
            break

        process_download(url, downloads_dir)

        repetir = input("¿Deseas descargar otro video en esta misma carpeta? (s/n): ").strip().lower()
        if repetir != "s":
            print("")
            break


def configure_custom_folder():
    """Permite al usuario ingresar o elegir una carpeta personalizada."""
    current_dir = get_current_download_dir()
    print("\n" + "-" * 60)
    print("CONFIGURAR CARPETA PERSONALIZADA")
    print(f"Carpeta actual: {current_dir}")
    print("-" * 60)
    print("Opciones:")
    print(" - Escribe o pega la ruta completa de la carpeta deseada.")
    print(" - Escribe 'v' para abrir una ventana y seleccionarla con el explorador.")
    print(" - Escribe 'c' o presiona Enter para cancelar y volver al menú.")

    entry = input("\nIngresa tu opción o ruta: ").strip()
    if not entry or entry.lower() in ["c", "cancelar", "m", "volver"]:
        print("[*] Operación cancelada. Se mantiene la carpeta actual.\n")
        return

    if entry.lower() == "v":
        print("[+] Abriendo ventana del explorador para seleccionar carpeta...")
        chosen = pick_folder_gui()
        if not chosen:
            print("[*] No se seleccionó ninguna carpeta en la ventana.\n")
            return
        entry = chosen

    # Limpiar comillas comunes al copiar rutas en Windows
    clean_path = entry.strip(' "\'')
    if not clean_path:
        print("[!] Ruta vacía. Operación cancelada.\n")
        return

    clean_path = os.path.abspath(clean_path)

    # Si la carpeta no existe, consultar si se desea crear
    if not os.path.isdir(clean_path):
        crear = input(f"[?] La carpeta '{clean_path}' no existe. ¿Deseas crearla ahora? (s/n): ").strip().lower()
        if crear == "s":
            try:
                os.makedirs(clean_path, exist_ok=True)
                print("[+] Carpeta creada exitosamente.")
            except Exception as e:
                print(f"[x] No se pudo crear la carpeta: {e}\n")
                return
        else:
            print("[*] Operación cancelada.\n")
            return

    set_current_download_dir(clean_path)
    print(f"\n[+] Carpeta de descargas actualizada y guardada:")
    print(f"    -> {clean_path}\n")


def reset_folder_to_default():
    """Restablece la carpeta al directorio 'Descargas' por defecto dentro del proyecto."""
    default_dir = reset_to_default_download_dir()
    print("\n" + "-" * 60)
    print("[+] Se restableció la carpeta de descargas a la predeterminada:")
    print(f"    -> {default_dir}")
    print("-" * 60 + "\n")


def main():
    while True:
        current_dir = get_current_download_dir()

        print("=" * 60)
        print("          DESCARGADOR DE YOUTUBE PERSONALIZADO")
        print("=" * 60)
        print(" Carpeta actual de guardado:")
        print(f"   {current_dir}")
        print("=" * 60)
        print("\nMenú principal:")
        print(" [1] Descargar video")
        print(" [2] Carpeta personalizada")
        print(" [3] Reiniciar carpeta (carpeta por defecto)")
        print(" [4] Salir")

        opcion = ask_user_choice("\nElige una opción (1, 2, 3 o 4): ", ["1", "2", "3", "4"])

        if opcion == "1":
            download_flow()
        elif opcion == "2":
            configure_custom_folder()
        elif opcion == "3":
            reset_folder_to_default()
        elif opcion == "4":
            print("\n¡Hasta luego!")
            break


if __name__ == "__main__":
    main()
