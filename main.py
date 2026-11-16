import os
import re
import sys
import json
import time
import shutil
import zipfile
import tempfile
import importlib
import subprocess
import threading
import urllib.request
import flet as ft
import yt_dlp

if getattr(sys, "frozen", False):
    SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.executable))
    BUNDLE_DIR = getattr(sys, "_MEIPASS", SCRIPT_DIR)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = SCRIPT_DIR

CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
ICON_PATH = os.path.join(BUNDLE_DIR, "img", "icono.ico")
if not os.path.isfile(ICON_PATH):
    ICON_PATH = os.path.join(SCRIPT_DIR, "img", "icono.ico")
HEADER_LOGO_PATH = os.path.join(BUNDLE_DIR, "img", "icono.jpeg")
if not os.path.isfile(HEADER_LOGO_PATH):
    HEADER_LOGO_PATH = os.path.join(SCRIPT_DIR, "img", "icono.jpeg")
ANSI_REGEX = re.compile(r"\x1b\[[0-9;]*m")

# Directorio persistente para actualizaciones autónomas de yt-dlp (incluso en .exe compilado)
ENGINE_UPDATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or SCRIPT_DIR,
    "DescargadorYTStudio",
    "site_packages",
)
ENGINE_META_FILE = os.path.join(ENGINE_UPDATE_DIR, "engine_meta.json")


class DownloadCancelledError(Exception):
    """Excepción interna lanzada cuando el usuario cancela manualmente una descarga en curso."""


def _parse_version_tuple(ver_str: str) -> tuple[int, ...]:
    """Convierte una cadena de versión '2026.03.15' en una tupla numérica comparable."""
    parts = re.findall(r"\d+", str(ver_str or ""))
    return tuple(int(p) for p in parts) if parts else (0,)


def get_ytdlp_version() -> str:
    """Devuelve la versión activa del módulo yt_dlp cargado en memoria."""
    try:
        return str(yt_dlp.version.__version__)
    except Exception:
        return "desconocida"


def _reload_ytdlp_module(custom_site_dir: str | None = None):
    """Recarga yt_dlp en caliente desde el entorno actual o desde custom_site_dir."""
    global yt_dlp
    if custom_site_dir and os.path.isdir(os.path.join(custom_site_dir, "yt_dlp")):
        if custom_site_dir in sys.path:
            sys.path.remove(custom_site_dir)
        sys.path.insert(0, custom_site_dir)

    to_delete = [mod for mod in list(sys.modules.keys()) if mod == "yt_dlp" or mod.startswith("yt_dlp.")]
    for mod in to_delete:
        sys.modules.pop(mod, None)

    importlib.invalidate_caches()
    yt_dlp = importlib.import_module("yt_dlp")


def _load_external_ytdlp_if_newer():
    """Al iniciar, si existe una actualización descargada más reciente que la integrada, la activa."""
    try:
        if not os.path.isfile(ENGINE_META_FILE):
            return
        with open(ENGINE_META_FILE, "r", encoding="utf-8") as f:
            meta = json.load(f)
        ext_ver = meta.get("version", "")
        if _parse_version_tuple(ext_ver) > _parse_version_tuple(get_ytdlp_version()):
            _reload_ytdlp_module(ENGINE_UPDATE_DIR)
    except Exception:
        pass


_load_external_ytdlp_if_newer()

# Inicialización segura de los binarios de FFmpeg en segundo plano
_ffmpeg_lock = threading.Lock()
_ffmpeg_ready = False


def ensure_ffmpeg_ready():
    """Garantiza que los binarios de FFmpeg (empaquetados o de static-ffmpeg) estén en PATH."""
    global _ffmpeg_ready
    if _ffmpeg_ready:
        return
    with _ffmpeg_lock:
        if _ffmpeg_ready:
            return
        # 1. Buscar primero binarios de FFmpeg incluidos en el ejecutable empaquetado
        for candidate_dir in (
            os.path.join(BUNDLE_DIR, "ffmpeg_bin"),
            os.path.join(SCRIPT_DIR, "ffmpeg_bin"),
            SCRIPT_DIR,
            BUNDLE_DIR,
        ):
            if os.path.isfile(os.path.join(candidate_dir, "ffmpeg.exe")):
                current_path = os.environ.get("PATH", "")
                if candidate_dir not in current_path:
                    os.environ["PATH"] = candidate_dir + os.pathsep + current_path
                _ffmpeg_ready = True
                return
        # 2. Respaldo con static_ffmpeg si se ejecuta desde código fuente
        try:
            import static_ffmpeg
            static_ffmpeg.add_paths()
        except Exception:
            pass
        _ffmpeg_ready = True


# =====================================================================
# GESTIÓN DE CONFIGURACIÓN Y CARPETAS
# =====================================================================

def get_default_downloads_dir() -> str:
    """
    Obtiene la ruta por defecto: carpeta 'Descargas' junto al script/ejecutable.
    Si no existe en el disco, la crea.
    """
    downloads_dir = os.path.join(SCRIPT_DIR, "Descargas")
    if not os.path.isdir(downloads_dir):
        alt_dir = os.path.join(SCRIPT_DIR, "descargas")
        if os.path.isdir(alt_dir):
            downloads_dir = alt_dir
        else:
            try:
                os.makedirs(downloads_dir, exist_ok=True)
            except Exception:
                downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads", "Descargas YT")
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
    os.makedirs(abs_path, exist_ok=True)
    config = load_config()
    config["download_dir"] = abs_path
    save_config(config)


def reset_to_default_download_dir() -> str:
    """Restablece la carpeta de descargas a la carpeta 'Descargas' por defecto."""
    default_dir = get_default_downloads_dir()
    set_current_download_dir(default_dir)
    return default_dir


def pick_folder_tk_fallback(initial_dir: str | None = None) -> str | None:
    """Fallback con tkinter para seleccionar carpeta si FilePicker no responde."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            title="Selecciona la carpeta para guardar las descargas",
            initialdir=initial_dir or SCRIPT_DIR,
        )
        root.destroy()
        return selected if selected else None
    except Exception:
        return None


def get_clipboard_tk_fallback() -> str | None:
    """Fallback con tkinter para leer el portapapeles."""
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        text = root.clipboard_get()
        root.destroy()
        return text
    except Exception:
        return None


# =====================================================================
# UTILIDADES DE FORMATO Y EXTRACCIÓN DE METADATOS (yt-dlp)
# =====================================================================

def clean_ansi(text: str) -> str:
    """Elimina secuencias ANSI de color de mensajes de yt-dlp."""
    return ANSI_REGEX.sub("", str(text or "")).strip()


def format_duration(seconds) -> str:
    """Convierte segundos a formato legible mm:ss o hh:mm:ss."""
    if not seconds:
        return "00:00"
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def format_size(bytes_val) -> str:
    """Convierte bytes a formato legible (MB / GB)."""
    if not bytes_val or bytes_val <= 0:
        return "Peso variable"
    mb = bytes_val / (1024 * 1024)
    if mb >= 1024:
        return f"~{mb / 1024:.2f} GB"
    if mb < 1:
        kb = bytes_val / 1024
        return f"~{kb:.0f} KB"
    return f"~{mb:.1f} MB"


def format_speed(bytes_per_sec) -> str:
    """Convierte bytes/seg a formato legible (KB/s o MB/s)."""
    if not bytes_per_sec or bytes_per_sec <= 0:
        return "Calculando..."
    mb_s = bytes_per_sec / (1024 * 1024)
    if mb_s >= 1:
        return f"{mb_s:.2f} MB/s"
    kb_s = bytes_per_sec / 1024
    return f"{kb_s:.0f} KB/s"


def get_best_thumbnail(info: dict) -> str:
    """Obtiene la URL de miniatura de mayor calidad disponible."""
    thumb = info.get("thumbnail")
    thumbnails = info.get("thumbnails") or []
    best_url = thumb or ""
    best_score = -1
    for t in thumbnails:
        url = t.get("url")
        if not url:
            continue
        w = t.get("width") or 0
        h = t.get("height") or 0
        pref = t.get("preference") or 0
        # Preferir imágenes estándar jpg/webp sobre avif si es posible
        score = (w * h) + (pref * 1000)
        if score > best_score:
            best_score = score
            best_url = url
    return best_url


def extract_video_qualities(info: dict) -> list[dict]:
    """
    Extrae las resoluciones de video disponibles sin duplicados,
    ordenadas de mayor a menor calidad, guardando los format_id exactos
    para que el peso estimado coincida con el archivo descargado.
    """
    formats = info.get("formats", [])
    duration = info.get("duration") or 0

    # Seleccionar la mejor pista de audio M4A (o mejor audio general como respaldo)
    best_audio_fmt = None
    best_audio_score = (-1, -1, -1, -1)
    for f in formats:
        if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none"):
            is_https = 2 if f.get("protocol") == "https" else 1
            is_m4a = 2 if f.get("ext") == "m4a" else 1
            has_size = 2 if (f.get("filesize") or f.get("filesize_approx")) else 1
            abr = f.get("abr") or f.get("tbr") or 0
            score = (is_m4a, is_https, has_size, abr)
            if score > best_audio_score:
                best_audio_score = score
                best_audio_fmt = f

    best_audio_size = 0
    best_audio_id = None
    if best_audio_fmt:
        best_audio_id = best_audio_fmt.get("format_id")
        best_audio_size = best_audio_fmt.get("filesize") or best_audio_fmt.get("filesize_approx") or 0
        if not best_audio_size and (best_audio_fmt.get("abr") or best_audio_fmt.get("tbr")) and duration:
            rate = best_audio_fmt.get("abr") or best_audio_fmt.get("tbr")
            best_audio_size = int((rate * 1000 / 8) * duration)
    if not best_audio_size and duration:
        best_audio_size = int((128 * 1000 / 8) * duration)

    heights = {}
    for f in formats:
        h = f.get("height")
        if not h or f.get("vcodec") in (None, "none"):
            continue

        is_video_only = 2 if f.get("acodec") in (None, "none") else 1
        is_https = 2 if f.get("protocol") == "https" else 1
        has_exact_size = 2 if (f.get("filesize") or f.get("filesize_approx")) else 1
        vcodec = f.get("vcodec") or ""
        is_avc = 2 if vcodec.startswith("avc") else 1
        fps = f.get("fps") or 0
        tbr = f.get("tbr") or 0
        # Priorizamos el mismo orden que usa la descarga (HTTPS + AVC + tamaño conocido + FPS + bitrate)
        score = (is_video_only, is_https, is_avc, has_exact_size, fps, tbr)

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

        has_own_audio = f.get("acodec") not in (None, "none")
        total_sz = v_size if has_own_audio else ((v_size + best_audio_size) if v_size else None)

        badge = ""
        if h >= 2160:
            badge = "4K UHD"
        elif h >= 1440:
            badge = "2K QHD"
        elif h >= 1080:
            badge = "Full HD"
        elif h >= 720:
            badge = "HD"

        qualities.append({
            "height": h,
            "fps": fps_str,
            "badge": badge,
            "video_format_id": f.get("format_id"),
            "audio_format_id": None if has_own_audio else best_audio_id,
            "video_size": v_size,
            "audio_size": 0 if has_own_audio else best_audio_size,
            "total_size": total_sz,
        })
    return qualities


def extract_audio_options(info: dict) -> list[dict]:
    """
    Genera la lista de opciones de audio con su peso estimado.
    """
    duration = info.get("duration") or 0
    formats = info.get("formats", [])

    # Buscar el tamaño real del mejor M4A nativo
    native_m4a_size = None
    for f in formats:
        if f.get("vcodec") == "none" and f.get("ext") == "m4a":
            sz = f.get("filesize") or f.get("filesize_approx")
            if not sz and f.get("abr") and duration:
                sz = int((f.get("abr") * 1000 / 8) * duration)
            if sz and (native_m4a_size is None or sz > native_m4a_size):
                native_m4a_size = sz
    if not native_m4a_size and duration:
        native_m4a_size = int((128 * 1000 / 8) * duration)

    def est_bytes(kbps: int) -> int | None:
        if not duration:
            return None
        return int((kbps * 1000 / 8) * duration)

    return [
        {
            "key": "mp3_320",
            "label": "MP3 • 320 kbps (Máxima fidelidad)",
            "codec": "mp3",
            "bitrate": "320",
            "size_bytes": est_bytes(320),
            "size_str": format_size(est_bytes(320)),
        },
        {
            "key": "mp3_256",
            "label": "MP3 • 256 kbps (Muy alta calidad)",
            "codec": "mp3",
            "bitrate": "256",
            "size_bytes": est_bytes(256),
            "size_str": format_size(est_bytes(256)),
        },
        {
            "key": "mp3_192",
            "label": "MP3 • 192 kbps (Recomendado)",
            "codec": "mp3",
            "bitrate": "192",
            "size_bytes": est_bytes(192),
            "size_str": format_size(est_bytes(192)),
        },
        {
            "key": "mp3_128",
            "label": "MP3 • 128 kbps (Calidad estándar)",
            "codec": "mp3",
            "bitrate": "128",
            "size_bytes": est_bytes(128),
            "size_str": format_size(est_bytes(128)),
        },
        {
            "key": "mp3_64",
            "label": "MP3 • 64 kbps (Archivo ligero)",
            "codec": "mp3",
            "bitrate": "64",
            "size_bytes": est_bytes(64),
            "size_str": format_size(est_bytes(64)),
        },
        {
            "key": "m4a_native",
            "label": "M4A / AAC • Original nativo de YouTube",
            "codec": "m4a",
            "bitrate": "native",
            "size_bytes": native_m4a_size,
            "size_str": format_size(native_m4a_size),
        },
    ]


# =====================================================================
# PALETA DE COLORES (ESTÉTICA ROSA & GRADIENTES MODERNOS)
# =====================================================================

BG_MAIN = "#0B0D12"            # Negro obsidiana profundo
BG_CARD = "#131722"            # Superficie de tarjeta flotante
BG_ELEVATED = "#1A1F2E"        # Superficie secundaria / inputs
BG_HOVER = "#232A3D"           # Estado hover sutil
BORDER_SUBTLE = "#262D40"      # Borde fino de tarjetas
BORDER_FOCUS = "#E1306C"       # Borde activo rosado

ACCENT_IG_PINK = "#E1306C"     # Rosa / Magenta principal
ACCENT_PINK_LIGHT = "#FF5C93"  # Rosa brillante para textos/acentos claros
ACCENT_IG_PURPLE = "#833AB4"   # Violeta para gradiente
ACCENT_IG_ORANGE = "#F77737"   # Coral/Rosa cálido para gradiente

PINK_GRADIENT_COLORS = [ACCENT_IG_PURPLE, ACCENT_IG_PINK, ACCENT_IG_ORANGE]

TEXT_PRIMARY = "#F8FAFC"       # Blanco limpio
TEXT_SECONDARY = "#94A3B8"     # Gris azulado suave
TEXT_MUTED = "#64748B"         # Gris apagado
STATUS_SUCCESS_GREEN = "#1DB954"  # Exclusivo para la lucecita de éxito en la esquina superior derecha
STATUS_WARN = "#F59E0B"        # Ámbar
STATUS_ERR = "#EF4444"         # Rojo coral


# =====================================================================
# APLICACIÓN PRINCIPAL EN FLET
# =====================================================================

class DescargadorYTApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.downloads_dir = get_current_download_dir()

        # Estado de análisis y descarga
        self.video_info: dict | None = None
        self.video_qualities: list[dict] = []
        self.audio_options: list[dict] = []
        self.current_mode: str = "video_audio"  # "video_audio" | "audio_only" | "video_only"
        self.is_analyzing: bool = False
        self.is_downloading: bool = False
        self.cancel_requested: bool = False
        self.is_updating_engine: bool = False
        self.last_saved_file: str | None = None

        # Servicios nativos de Flet
        self.clipboard = ft.Clipboard()
        self.file_picker = ft.FilePicker()
        self.page.services.extend([self.clipboard, self.file_picker])

        self._configure_window()
        self._build_ui()
        threading.Thread(target=ensure_ffmpeg_ready, daemon=True).start()

    def _configure_window(self):
        self.page.title = "Descargador de YouTube • Studio GUI"
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.bgcolor = BG_MAIN
        self.page.padding = 0
        self.page.spacing = 0

        # Dimensiones de ventana de escritorio
        self.page.window.width = 920
        self.page.window.height = 860
        self.page.window.min_width = 760
        self.page.window.min_height = 700
        if os.path.isfile(ICON_PATH):
            self.page.window.icon = ICON_PATH

        # Tema tipográfico y colores base
        self.page.theme = ft.Theme(
            color_scheme_seed=ACCENT_IG_PINK,
            scaffold_bgcolor=BG_MAIN,
        )

    # -----------------------------------------------------------------
    # CONSTRUCCIÓN DE INTERFAZ
    # -----------------------------------------------------------------

    def _build_ui(self):
        # 1. HEADER / BARRA SUPERIOR
        self.status_dot = ft.Container(
            width=10,
            height=10,
            border_radius=ft.BorderRadius.all(5),
            bgcolor=ACCENT_IG_PINK,
            shadow=ft.BoxShadow(
                spread_radius=1,
                blur_radius=8,
                color=ACCENT_IG_PINK,
            ),
        )
        self.status_text = ft.Text(
            "Listo para analizar",
            size=12,
            weight=ft.FontWeight.W_600,
            color=TEXT_PRIMARY,
        )
        self.status_pill = ft.Container(
            content=ft.Row(
                controls=[self.status_dot, self.status_text],
                spacing=8,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=BG_ELEVATED,
            padding=ft.Padding.symmetric(horizontal=14, vertical=8),
            border_radius=ft.BorderRadius.all(30),
            border=ft.Border.all(1, BORDER_SUBTLE),
        )

        # Botón / Pastilla para ver versión y actualizar yt-dlp (funciona en código fuente y en .exe)
        self.engine_update_spinner = ft.ProgressRing(
            width=13,
            height=13,
            stroke_width=2,
            color=ACCENT_PINK_LIGHT,
            visible=False,
        )
        self.engine_update_icon = ft.Icon(
            ft.Icons.SYSTEM_UPDATE_ALT_ROUNDED,
            size=15,
            color=ACCENT_PINK_LIGHT,
        )
        self.engine_version_text = ft.Text(
            f"yt-dlp {get_ytdlp_version()}",
            size=11,
            weight=ft.FontWeight.W_600,
            color=TEXT_SECONDARY,
        )
        self.engine_update_btn = ft.Container(
            content=ft.Row(
                controls=[
                    self.engine_update_spinner,
                    self.engine_update_icon,
                    self.engine_version_text,
                ],
                spacing=6,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=BG_ELEVATED,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            border_radius=ft.BorderRadius.all(30),
            border=ft.Border.all(1, BORDER_SUBTLE),
            ink=True,
            tooltip="Buscar e instalar actualizaciones de yt-dlp",
            on_click=self._on_update_engine_click,
        )

        header_bar = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Container(
                                content=(
                                    ft.Image(
                                        src=HEADER_LOGO_PATH,
                                        width=46,
                                        height=46,
                                        fit=ft.BoxFit.COVER,
                                        border_radius=ft.BorderRadius.all(14),
                                    )
                                    if os.path.isfile(HEADER_LOGO_PATH)
                                    else ft.Icon(
                                        ft.Icons.PLAY_ARROW_ROUNDED,
                                        color=TEXT_PRIMARY,
                                        size=26,
                                    )
                                ),
                                width=46,
                                height=46,
                                border_radius=ft.BorderRadius.all(14),
                                bgcolor=BG_ELEVATED,
                                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                                alignment=ft.Alignment.CENTER,
                                shadow=ft.BoxShadow(
                                    spread_radius=0,
                                    blur_radius=14,
                                    color="#55000000",
                                    offset=(0, 4),
                                ),
                            ),
                            ft.Column(
                                controls=[
                                    ft.Row(
                                        controls=[
                                            ft.Text(
                                                "Descargador YT",
                                                size=21,
                                                weight=ft.FontWeight.BOLD,
                                                color=TEXT_PRIMARY,
                                            ),
                                            ft.Container(
                                                content=ft.Text(
                                                    "STUDIO",
                                                    size=10,
                                                    weight=ft.FontWeight.BOLD,
                                                    color=ACCENT_PINK_LIGHT,
                                                ),
                                                bgcolor="#22E1306C",
                                                padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                                                border_radius=ft.BorderRadius.all(8),
                                                border=ft.Border.all(1, "#45E1306C"),
                                            ),
                                        ],
                                        spacing=8,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                    ft.Text(
                                        "Descarga videos y audio en máxima calidad con FFmpeg integrado",
                                        size=12,
                                        color=TEXT_SECONDARY,
                                    ),
                                ],
                                spacing=2,
                            ),
                        ],
                        spacing=14,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        controls=[self.engine_update_btn, self.status_pill],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.only(bottom=6),
        )

        # BARRA DE CARPETA DE DESTINO (Persistencia config.json)
        self.folder_path_text = ft.Text(
            self.downloads_dir,
            size=12,
            color=TEXT_PRIMARY,
            weight=ft.FontWeight.W_500,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            expand=True,
        )

        folder_card = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        content=ft.Icon(ft.Icons.FOLDER_ROUNDED, color=ACCENT_IG_PINK, size=18),
                        width=34,
                        height=34,
                        bgcolor="#1FE1306C",
                        border_radius=ft.BorderRadius.all(10),
                        alignment=ft.Alignment.CENTER,
                    ),
                    ft.Column(
                        controls=[
                            ft.Text("CARPETA DE DESTINO", size=10, weight=ft.FontWeight.BOLD, color=TEXT_MUTED),
                            self.folder_path_text,
                        ],
                        spacing=1,
                        expand=True,
                    ),
                    ft.Row(
                        controls=[
                            ft.Button(
                                content="Cambiar",
                                icon=ft.Icons.CREATE_NEW_FOLDER_OUTLINED,
                                bgcolor=BG_ELEVATED,
                                color=TEXT_PRIMARY,
                                elevation=0,
                                height=34,
                                tooltip="Seleccionar carpeta personalizada",
                                on_click=self._on_pick_folder,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.RESTORE_ROUNDED,
                                icon_color=TEXT_SECONDARY,
                                icon_size=18,
                                bgcolor=BG_ELEVATED,
                                width=34,
                                height=34,
                                tooltip="Restablecer a carpeta 'Descargas' por defecto",
                                on_click=self._on_reset_folder,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                                icon_color=ACCENT_IG_PINK,
                                icon_size=18,
                                bgcolor=BG_ELEVATED,
                                width=34,
                                height=34,
                                tooltip="Abrir carpeta en el Explorador de Windows",
                                on_click=self._on_open_folder,
                            ),
                        ],
                        spacing=6,
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=BG_CARD,
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            border_radius=ft.BorderRadius.all(18),
            border=ft.Border.all(1, BORDER_SUBTLE),
        )

        # 2. ENTRADA DE URL Y ANÁLISIS
        self.url_input = ft.TextField(
            value="",
            hint_text="Pega aquí el enlace de YouTube (ej. https://www.youtube.com/watch?v=...)",
            hint_style=ft.TextStyle(color=TEXT_MUTED, size=14),
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=14, weight=ft.FontWeight.W_500),
            border=ft.NoInputBorder(),
            cursor_color=ACCENT_IG_PINK,
            content_padding=ft.Padding.symmetric(horizontal=8, vertical=12),
            expand=True,
            on_submit=self._on_analyze_click,
            on_change=self._on_url_change,
        )

        self.clear_url_btn = ft.IconButton(
            icon=ft.Icons.CLOSE_ROUNDED,
            icon_color=TEXT_MUTED,
            icon_size=18,
            tooltip="Limpiar enlace",
            visible=False,
            on_click=self._on_clear_url,
        )

        self.paste_btn = ft.Button(
            content="Pegar",
            icon=ft.Icons.CONTENT_PASTE_ROUNDED,
            bgcolor=BG_ELEVATED,
            color=TEXT_PRIMARY,
            elevation=0,
            height=44,
            tooltip="Pegar enlace automáticamente desde el portapapeles",
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=14),
                padding=ft.Padding.symmetric(horizontal=16),
            ),
            on_click=self._on_paste_click,
        )

        self.analyze_spinner = ft.ProgressRing(
            width=16,
            height=16,
            stroke_width=2.2,
            color=TEXT_PRIMARY,
            visible=False,
        )
        self.analyze_icon = ft.Icon(ft.Icons.AUTO_AWESOME_ROUNDED, color=TEXT_PRIMARY, size=18)
        self.analyze_label = ft.Text(
            "Analizar",
            size=14,
            weight=ft.FontWeight.BOLD,
            color=TEXT_PRIMARY,
        )

        self.analyze_btn = ft.Container(
            content=ft.Row(
                controls=[self.analyze_spinner, self.analyze_icon, self.analyze_label],
                spacing=8,
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            gradient=ft.LinearGradient(
                begin=ft.Alignment.CENTER_LEFT,
                end=ft.Alignment.CENTER_RIGHT,
                colors=PINK_GRADIENT_COLORS,
            ),
            height=44,
            padding=ft.Padding.symmetric(horizontal=22),
            border_radius=ft.BorderRadius.all(14),
            ink=True,
            on_click=self._on_analyze_click,
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=14,
                color="#44E1306C",
                offset=(0, 3),
            ),
        )

        url_search_card = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        content=ft.Icon(ft.Icons.LINK_ROUNDED, color=ACCENT_IG_PINK, size=22),
                        padding=ft.Padding.only(left=8),
                    ),
                    self.url_input,
                    self.clear_url_btn,
                    self.paste_btn,
                    self.analyze_btn,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=BG_CARD,
            padding=ft.Padding.all(10),
            border_radius=ft.BorderRadius.all(22),
            border=ft.Border.all(1.2, BORDER_SUBTLE),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=24,
                color="#55000000",
                offset=(0, 8),
            ),
        )

        # 3. PANEL DE PREVISUALIZACIÓN (CARD FLOTANTE)
        self.preview_thumbnail = ft.Image(
            src="https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
            width=230,
            height=130,
            fit=ft.BoxFit.COVER,
            border_radius=ft.BorderRadius.all(16),
        )
        self.preview_duration_badge = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.SCHEDULE_ROUNDED, size=12, color=TEXT_PRIMARY),
                    ft.Text("00:00", size=11, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                ],
                spacing=4,
                tight=True,
            ),
            bgcolor="#CC0B0D12",
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            border_radius=ft.BorderRadius.all(8),
            right=8,
            bottom=8,
        )

        thumbnail_stack = ft.Stack(
            controls=[
                ft.Container(
                    content=self.preview_thumbnail,
                    width=230,
                    height=130,
                    border_radius=ft.BorderRadius.all(16),
                    bgcolor=BG_ELEVATED,
                    shadow=ft.BoxShadow(
                        spread_radius=0,
                        blur_radius=14,
                        color="#66000000",
                        offset=(0, 4),
                    ),
                ),
                self.preview_duration_badge,
            ],
            width=230,
            height=130,
        )

        self.preview_title = ft.Text(
            "Título del video",
            size=17,
            weight=ft.FontWeight.BOLD,
            color=TEXT_PRIMARY,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.preview_channel = ft.Text(
            "Nombre del canal",
            size=13,
            weight=ft.FontWeight.W_600,
            color=ACCENT_PINK_LIGHT,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.preview_duration_text = ft.Text(
            "Duración: 00:00",
            size=12,
            color=TEXT_SECONDARY,
        )
        self.preview_max_res_text = ft.Text(
            "Full HD",
            size=11,
            weight=ft.FontWeight.BOLD,
            color=TEXT_PRIMARY,
        )

        self.preview_card = ft.Container(
            content=ft.Row(
                controls=[
                    thumbnail_stack,
                    ft.Column(
                        controls=[
                            ft.Row(
                                controls=[
                                    ft.Container(
                                        content=ft.Text(
                                            "VIDEO DETECTADO",
                                            size=10,
                                            weight=ft.FontWeight.BOLD,
                                            color=ACCENT_PINK_LIGHT,
                                        ),
                                        bgcolor="#22E1306C",
                                        padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                                        border_radius=ft.BorderRadius.all(6),
                                    ),
                                    ft.Container(
                                        content=self.preview_max_res_text,
                                        bgcolor=BG_ELEVATED,
                                        padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                                        border_radius=ft.BorderRadius.all(6),
                                        border=ft.Border.all(1, BORDER_SUBTLE),
                                    ),
                                ],
                                spacing=8,
                            ),
                            self.preview_title,
                            ft.Row(
                                controls=[
                                    ft.Icon(ft.Icons.VERIFIED_USER_ROUNDED, size=15, color=ACCENT_IG_PINK),
                                    self.preview_channel,
                                ],
                                spacing=6,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Row(
                                controls=[
                                    ft.Icon(ft.Icons.TIMER_OUTLINED, size=14, color=TEXT_SECONDARY),
                                    self.preview_duration_text,
                                ],
                                spacing=6,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                        ],
                        spacing=8,
                        alignment=ft.MainAxisAlignment.CENTER,
                        expand=True,
                    ),
                ],
                spacing=20,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=BG_CARD,
            padding=ft.Padding.all(18),
            border_radius=ft.BorderRadius.all(22),
            border=ft.Border.all(1, BORDER_SUBTLE),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=22,
                color="#55000000",
                offset=(0, 8),
            ),
            visible=False,
        )

        # Placeholder elegante antes de analizar el primer video
        self.empty_state_card = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Icon(ft.Icons.ONDEMAND_VIDEO_ROUNDED, size=38, color=ACCENT_IG_PINK),
                        width=72,
                        height=72,
                        border_radius=ft.BorderRadius.all(24),
                        bgcolor="#18E1306C",
                        alignment=ft.Alignment.CENTER,
                    ),
                    ft.Text(
                        "Pega un enlace de YouTube y presiona «Analizar»",
                        size=15,
                        weight=ft.FontWeight.W_600,
                        color=TEXT_PRIMARY,
                    ),
                    ft.Text(
                        "Obtendrás la miniatura, duración, todas las resoluciones reales y el peso estimado antes de descargar.",
                        size=12,
                        color=TEXT_SECONDARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            bgcolor=BG_CARD,
            padding=ft.Padding.symmetric(horizontal=24, vertical=30),
            border_radius=ft.BorderRadius.all(22),
            border=ft.Border.all(1, BORDER_SUBTLE),
            alignment=ft.Alignment.CENTER,
            visible=True,
        )

        # 4. OPCIONES Y CONFIGURACIONES DE DESCARGA
        self.mode_btn_video_audio = self._create_mode_tab(
            mode_key="video_audio",
            title="Video + Audio",
            subtitle="MP4 Combinado",
            icon=ft.Icons.MOVIE_FILTER_ROUNDED,
            is_active=True,
        )
        self.mode_btn_audio_only = self._create_mode_tab(
            mode_key="audio_only",
            title="Solo Audio",
            subtitle="MP3 / M4A Original",
            icon=ft.Icons.HEADPHONES_ROUNDED,
            is_active=False,
        )
        self.mode_btn_video_only = self._create_mode_tab(
            mode_key="video_only",
            title="Solo Video",
            subtitle="MP4 Sin Audio",
            icon=ft.Icons.VIDEOCAM_OUTLINED,
            is_active=False,
        )

        mode_tabs_row = ft.Row(
            controls=[
                self.mode_btn_video_audio,
                self.mode_btn_audio_only,
                self.mode_btn_video_only,
            ],
            spacing=12,
        )

        self.quality_dropdown = ft.Dropdown(
            value=None,
            options=[],
            label="Calidad y formato de salida",
            label_style=ft.TextStyle(color=TEXT_SECONDARY, size=12),
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=14, weight=ft.FontWeight.W_500),
            filled=True,
            fill_color=BG_ELEVATED,
            bgcolor=BG_ELEVATED,
            border=ft.OutlineInputBorder(
                border_radius=ft.BorderRadius.all(14),
                side=ft.BorderSide(1, BORDER_SUBTLE),
            ),
            expand=True,
            on_select=self._on_quality_change,
        )

        self.estimated_size_value = ft.Text(
            "—",
            size=18,
            weight=ft.FontWeight.BOLD,
            color=ACCENT_PINK_LIGHT,
        )
        self.estimated_format_tag = ft.Text(
            "Analiza un video primero",
            size=11,
            color=TEXT_SECONDARY,
        )

        estimated_size_box = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        content=ft.Icon(ft.Icons.SD_STORAGE_ROUNDED, color=ACCENT_IG_PINK, size=22),
                        width=42,
                        height=42,
                        bgcolor="#1FE1306C",
                        border_radius=ft.BorderRadius.all(12),
                        alignment=ft.Alignment.CENTER,
                    ),
                    ft.Column(
                        controls=[
                            ft.Text("PESO ESTIMADO", size=10, weight=ft.FontWeight.BOLD, color=TEXT_MUTED),
                            self.estimated_size_value,
                            self.estimated_format_tag,
                        ],
                        spacing=1,
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=BG_ELEVATED,
            padding=ft.Padding.symmetric(horizontal=18, vertical=10),
            border_radius=ft.BorderRadius.all(16),
            border=ft.Border.all(1, BORDER_SUBTLE),
            width=250,
        )

        self.options_card = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.TUNE_ROUNDED, size=18, color=ACCENT_IG_PINK),
                            ft.Text(
                                "Configuración de Descarga",
                                size=15,
                                weight=ft.FontWeight.BOLD,
                                color=TEXT_PRIMARY,
                            ),
                        ],
                        spacing=8,
                    ),
                    mode_tabs_row,
                    ft.Row(
                        controls=[
                            self.quality_dropdown,
                            estimated_size_box,
                        ],
                        spacing=14,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=14,
            ),
            bgcolor=BG_CARD,
            padding=ft.Padding.all(18),
            border_radius=ft.BorderRadius.all(22),
            border=ft.Border.all(1, BORDER_SUBTLE),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=20,
                color="#44000000",
                offset=(0, 6),
            ),
            disabled=True,
            opacity=0.55,
        )

        # 5. CONTROLES DE ACCIÓN Y PROGRESO
        self.download_spinner = ft.ProgressRing(
            width=20,
            height=20,
            stroke_width=2.5,
            color=TEXT_PRIMARY,
            visible=False,
        )
        self.download_icon = ft.Icon(ft.Icons.DOWNLOAD_ROUNDED, color=TEXT_PRIMARY, size=22)
        self.download_btn_text = ft.Text(
            "Descargar ahora",
            size=16,
            weight=ft.FontWeight.BOLD,
            color=TEXT_PRIMARY,
        )

        self.download_btn = ft.Container(
            content=ft.Row(
                controls=[self.download_spinner, self.download_icon, self.download_btn_text],
                spacing=10,
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            height=54,
            border_radius=ft.BorderRadius.all(18),
            gradient=ft.LinearGradient(
                begin=ft.Alignment.CENTER_LEFT,
                end=ft.Alignment.CENTER_RIGHT,
                colors=PINK_GRADIENT_COLORS,
            ),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=20,
                color="#55E1306C",
                offset=(0, 6),
            ),
            expand=True,
            ink=True,
            disabled=True,
            opacity=0.5,
            on_click=self._on_download_click,
        )

        self.cancel_download_btn = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.STOP_CIRCLE_OUTLINED, color=STATUS_ERR, size=20),
                    ft.Text(
                        "Cancelar",
                        size=15,
                        weight=ft.FontWeight.BOLD,
                        color=TEXT_PRIMARY,
                    ),
                ],
                spacing=8,
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            height=54,
            padding=ft.Padding.symmetric(horizontal=22),
            border_radius=ft.BorderRadius.all(18),
            bgcolor="#26EF4444",
            border=ft.Border.all(1.5, STATUS_ERR),
            ink=True,
            visible=False,
            tooltip="Cancelar descarga en curso y limpiar archivos temporales",
            on_click=self._on_cancel_download_click,
        )

        action_buttons_row = ft.Row(
            controls=[self.download_btn, self.cancel_download_btn],
            spacing=12,
        )

        # Panel de barra de progreso con el mismo gradiente rosa que el botón 'Descargar ahora'
        self.progress_percent_text = ft.Text(
            "0%",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=ACCENT_PINK_LIGHT,
        )
        self.progress_phase_text = ft.Text(
            "Esperando orden de descarga...",
            size=13,
            weight=ft.FontWeight.W_600,
            color=TEXT_PRIMARY,
        )
        self.progress_speed_text = ft.Text(
            "Velocidad: —",
            size=12,
            color=TEXT_SECONDARY,
        )
        self.progress_bytes_text = ft.Text(
            "0.0 MB / 0.0 MB",
            size=12,
            color=TEXT_SECONDARY,
        )

        self.progress_gradient_fill = ft.Container(
            height=12,
            border_radius=ft.BorderRadius.all(8),
            gradient=ft.LinearGradient(
                begin=ft.Alignment.CENTER_LEFT,
                end=ft.Alignment.CENTER_RIGHT,
                colors=PINK_GRADIENT_COLORS,
            ),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=10,
                color="#66E1306C",
            ),
            expand=0,
            visible=False,
        )
        self.progress_empty_spacer = ft.Container(
            height=12,
            expand=1000,
            visible=True,
        )
        self.progress_bar = ft.Container(
            content=ft.Row(
                controls=[
                    self.progress_gradient_fill,
                    self.progress_empty_spacer,
                ],
                spacing=0,
            ),
            height=12,
            border_radius=ft.BorderRadius.all(8),
            bgcolor=BG_ELEVATED,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

        self.saved_file_text = ft.Text(
            "",
            size=12,
            color=ACCENT_PINK_LIGHT,
            weight=ft.FontWeight.W_500,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            expand=True,
        )
        self.open_file_btn = ft.Button(
            content="Abrir archivo",
            icon=ft.Icons.PLAY_CIRCLE_FILL_ROUNDED,
            bgcolor="#26E1306C",
            color=ACCENT_PINK_LIGHT,
            elevation=0,
            height=32,
            visible=False,
            on_click=self._on_open_saved_file,
        )
        self.saved_file_row = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, color=ACCENT_IG_PINK, size=18),
                    self.saved_file_text,
                    self.open_file_btn,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor="#16E1306C",
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            border_radius=ft.BorderRadius.all(12),
            border=ft.Border.all(1, "#38E1306C"),
            visible=False,
        )

        self.progress_card = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Column(
                                controls=[
                                    self.progress_phase_text,
                                    ft.Row(
                                        controls=[
                                            self.progress_speed_text,
                                            ft.Text("•", color=TEXT_MUTED, size=12),
                                            self.progress_bytes_text,
                                        ],
                                        spacing=8,
                                    ),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                            self.progress_percent_text,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self.progress_bar,
                    self.saved_file_row,
                ],
                spacing=10,
            ),
            bgcolor=BG_CARD,
            padding=ft.Padding.all(18),
            border_radius=ft.BorderRadius.all(22),
            border=ft.Border.all(1, BORDER_SUBTLE),
        )

        # Layout principal con scroll suave
        main_column = ft.Column(
            controls=[
                header_bar,
                folder_card,
                url_search_card,
                self.empty_state_card,
                self.preview_card,
                self.options_card,
                action_buttons_row,
                self.progress_card,
            ],
            spacing=14,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        root_container = ft.Container(
            content=main_column,
            padding=ft.Padding.symmetric(horizontal=28, vertical=22),
            expand=True,
            bgcolor=BG_MAIN,
        )

        self.page.add(root_container)

    def _create_mode_tab(
        self,
        mode_key: str,
        title: str,
        subtitle: str,
        icon: ft.IconData,
        is_active: bool,
    ) -> ft.Container:
        """Crea una tarjeta interactiva para seleccionar el modo de descarga."""
        icon_ctrl = ft.Icon(
            icon,
            size=20,
            color=ACCENT_IG_PINK if is_active else TEXT_SECONDARY,
        )
        title_ctrl = ft.Text(
            title,
            size=13,
            weight=ft.FontWeight.BOLD,
            color=TEXT_PRIMARY if is_active else TEXT_SECONDARY,
        )
        sub_ctrl = ft.Text(
            subtitle,
            size=11,
            color=ACCENT_PINK_LIGHT if is_active else TEXT_MUTED,
        )

        tab = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        content=icon_ctrl,
                        width=38,
                        height=38,
                        border_radius=ft.BorderRadius.all(12),
                        bgcolor="#24E1306C" if is_active else BG_MAIN,
                        alignment=ft.Alignment.CENTER,
                    ),
                    ft.Column(
                        controls=[title_ctrl, sub_ctrl],
                        spacing=1,
                    ),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            data={
                "mode_key": mode_key,
                "icon_ctrl": icon_ctrl,
                "title_ctrl": title_ctrl,
                "sub_ctrl": sub_ctrl,
            },
            bgcolor=BG_ELEVATED if is_active else BG_MAIN,
            padding=ft.Padding.symmetric(horizontal=14, vertical=12),
            border_radius=ft.BorderRadius.all(16),
            border=ft.Border.all(
                1.5 if is_active else 1,
                ACCENT_IG_PINK if is_active else BORDER_SUBTLE,
            ),
            expand=True,
            ink=True,
            on_click=self._on_mode_tab_click,
        )
        return tab

    def _update_mode_tabs_visuals(self):
        """Actualiza los colores de las pestañas de modo según la selección activa."""
        tabs = [
            self.mode_btn_video_audio,
            self.mode_btn_audio_only,
            self.mode_btn_video_only,
        ]
        for tab in tabs:
            meta = tab.data
            active = (meta["mode_key"] == self.current_mode)
            tab.bgcolor = BG_ELEVATED if active else BG_MAIN
            tab.border = ft.Border.all(
                1.5 if active else 1,
                ACCENT_IG_PINK if active else BORDER_SUBTLE,
            )
            meta["icon_ctrl"].color = ACCENT_IG_PINK if active else TEXT_SECONDARY
            meta["title_ctrl"].color = TEXT_PRIMARY if active else TEXT_SECONDARY
            meta["sub_ctrl"].color = ACCENT_PINK_LIGHT if active else TEXT_MUTED
            tab.content.controls[0].bgcolor = "#24E1306C" if active else BG_MAIN

    def _set_progress_bar(self, ratio: float | None):
        """Actualiza la barra de progreso con gradiente rosa (0.0 a 1.0, o lleno en post-procesado)."""
        if ratio is None:
            fill_flex = 1000
        else:
            clamped = min(max(float(ratio), 0.0), 1.0)
            fill_flex = int(round(clamped * 1000))

        empty_flex = 1000 - fill_flex
        self.progress_gradient_fill.expand = fill_flex
        self.progress_gradient_fill.visible = fill_flex > 0
        self.progress_empty_spacer.expand = empty_flex
        self.progress_empty_spacer.visible = empty_flex > 0

    # -----------------------------------------------------------------
    # GESTIÓN DE ESTADO VISUAL Y NOTIFICACIONES (THREAD-SAFE)
    # -----------------------------------------------------------------

    def _safe_update(self):
        """Despierta el event loop de Flet para enviar los cambios a la ventana en tiempo real."""
        try:
            loop = self.page.loop
            if loop and loop.is_running():
                loop.call_soon_threadsafe(self.page.update)
            else:
                self.page.update()
        except Exception:
            try:
                self.page.update()
            except Exception:
                pass

    def _set_status(self, message: str, color: str = ACCENT_IG_PINK):
        self.status_text.value = message
        self.status_dot.bgcolor = color
        self.status_dot.shadow = ft.BoxShadow(
            spread_radius=1,
            blur_radius=8,
            color=color,
        )
        self._safe_update()

    def _notify(self, message: str, is_error: bool = False):
        def _show():
            snack = ft.SnackBar(
                content=ft.Row(
                    controls=[
                        ft.Icon(
                            ft.Icons.ERROR_OUTLINE_ROUNDED if is_error else ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED,
                            color=TEXT_PRIMARY,
                            size=20,
                        ),
                        ft.Text(message, color=TEXT_PRIMARY, size=13, weight=ft.FontWeight.W_500, expand=True),
                    ],
                    spacing=10,
                ),
                bgcolor=STATUS_ERR if is_error else "#1E293B",
                duration=3500,
            )
            self.page.show_dialog(snack)

        try:
            loop = self.page.loop
            if loop and loop.is_running():
                loop.call_soon_threadsafe(_show)
            else:
                _show()
        except Exception:
            _show()

    # -----------------------------------------------------------------
    # EVENTOS DE CARPETA Y PORTAPAPELES
    # -----------------------------------------------------------------

    async def _on_pick_folder(self, e):
        selected = None
        try:
            selected = await self.file_picker.get_directory_path(
                dialog_title="Selecciona la carpeta para guardar tus descargas",
                initial_directory=self.downloads_dir,
            )
        except Exception:
            selected = pick_folder_tk_fallback(self.downloads_dir)

        if selected:
            set_current_download_dir(selected)
            self.downloads_dir = get_current_download_dir()
            self.folder_path_text.value = self.downloads_dir
            self.page.update()
            self._notify(f"Carpeta actualizada: {self.downloads_dir}")

    def _on_reset_folder(self, e):
        self.downloads_dir = reset_to_default_download_dir()
        self.folder_path_text.value = self.downloads_dir
        self.page.update()
        self._notify(f"Carpeta restablecida a predeterminada: {self.downloads_dir}")

    def _on_open_folder(self, e):
        os.makedirs(self.downloads_dir, exist_ok=True)
        try:
            os.startfile(self.downloads_dir)
        except Exception as ex:
            self._notify(f"No se pudo abrir la carpeta: {ex}", is_error=True)

    async def _on_paste_click(self, e):
        text = None
        try:
            text = await self.clipboard.get()
        except Exception:
            text = get_clipboard_tk_fallback()

        if text and text.strip():
            self.url_input.value = text.strip()
            self.clear_url_btn.visible = True
            self.page.update()
            # Si parece un enlace de YouTube, iniciamos el análisis automáticamente
            if any(k in text.lower() for k in ("youtube.com/", "youtu.be/")):
                self._on_analyze_click(None)
        else:
            self._notify("El portapapeles está vacío o no contiene texto.", is_error=True)

    def _on_clear_url(self, e):
        self.url_input.value = ""
        self.clear_url_btn.visible = False
        self.page.update()

    def _on_url_change(self, e):
        has_text = bool((self.url_input.value or "").strip())
        if self.clear_url_btn.visible != has_text:
            self.clear_url_btn.visible = has_text
            self.page.update()

    # -----------------------------------------------------------------
    # ANÁLISIS DE URL CON YT-DLP (EN HILO SECUNDARIO)
    # -----------------------------------------------------------------

    def _on_analyze_click(self, e):
        if self.is_analyzing or self.is_downloading:
            return

        url = (self.url_input.value or "").strip()
        if not url:
            self._notify("Por favor, ingresa o pega un enlace de YouTube primero.", is_error=True)
            return

        self.is_analyzing = True
        self.analyze_spinner.visible = True
        self.analyze_icon.visible = False
        self.analyze_label.value = "Analizando..."
        self.analyze_btn.opacity = 0.75
        self._set_status("Analizando metadatos del video...", STATUS_WARN)

        thread = threading.Thread(target=self._analyze_worker, args=(url,), daemon=True)
        thread.start()

    def _analyze_worker(self, url: str):
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            qualities = extract_video_qualities(info)
            audio_opts = extract_audio_options(info)

            self.video_info = info
            self.video_qualities = qualities
            self.audio_options = audio_opts

            # Actualizar tarjeta de previsualización
            title = info.get("title") or "Video de YouTube"
            uploader = info.get("uploader") or info.get("channel") or "Canal desconocido"
            dur_str = format_duration(info.get("duration"))
            thumb_url = get_best_thumbnail(info)

            if thumb_url:
                self.preview_thumbnail.src = thumb_url
            self.preview_title.value = title
            self.preview_channel.value = uploader
            self.preview_duration_text.value = f"Duración: {dur_str}"
            self.preview_duration_badge.content.controls[1].value = dur_str

            if qualities:
                top_h = qualities[0]["height"]
                top_badge = qualities[0]["badge"] or f"{top_h}p"
                self.preview_max_res_text.value = f"Hasta {top_h}p {top_badge}".strip()
            else:
                self.preview_max_res_text.value = "Estándar"

            self.empty_state_card.visible = False
            self.preview_card.visible = True

            # Habilitar opciones y poblar calidades
            self.options_card.disabled = False
            self.options_card.opacity = 1.0
            self._populate_quality_dropdown()

            # Habilitar botón de descarga
            self.download_btn.disabled = False
            self.download_btn.opacity = 1.0

            self.progress_phase_text.value = "Video listo para descargar"
            self.progress_speed_text.value = "Selecciona calidad y presiona Descargar"
            self.progress_bytes_text.value = "0.0 MB"
            self.progress_percent_text.value = "0%"
            self._set_progress_bar(0.0)
            self.saved_file_row.visible = False

            self._set_status("Video analizado correctamente", STATUS_SUCCESS_GREEN)

        except Exception as ex:
            err_msg = clean_ansi(str(ex))
            self._set_status("Error al analizar el enlace", STATUS_ERR)
            self._notify(f"No se pudo analizar el video: {err_msg[:140]}", is_error=True)

        finally:
            self.is_analyzing = False
            self.analyze_spinner.visible = False
            self.analyze_icon.visible = True
            self.analyze_label.value = "Analizar"
            self.analyze_btn.opacity = 1.0
            self._safe_update()

    # -----------------------------------------------------------------
    # SELECTOR DE MODO, CALIDAD Y CÁLCULO DE PESO ESTIMADO
    # -----------------------------------------------------------------

    def _on_mode_tab_click(self, e):
        if self.is_downloading or not self.video_info:
            return
        clicked_mode = e.control.data["mode_key"]
        if clicked_mode == self.current_mode:
            return
        self.current_mode = clicked_mode
        self._update_mode_tabs_visuals()
        self._populate_quality_dropdown()
        self.page.update()

    def _populate_quality_dropdown(self):
        """Rellena el Dropdown de calidades junto con el peso estimado de cada opción."""
        options: list[ft.DropdownOption] = []

        if self.current_mode in ("video_audio", "video_only"):
            is_combined = (self.current_mode == "video_audio")
            if self.video_qualities:
                best_q = self.video_qualities[0]
                best_sz = best_q["total_size"] if is_combined else best_q["video_size"]
                best_sz_str = format_size(best_sz)
                options.append(
                    ft.DropdownOption(
                        key="auto",
                        text=f"Mejor calidad disponible ({best_q['height']}p)  •  {best_sz_str}",
                    )
                )
                for q in self.video_qualities:
                    h = q["height"]
                    fps_part = f" {q['fps']}" if q["fps"] else ""
                    badge_part = f" [{q['badge']}]" if q["badge"] else ""
                    sz_val = q["total_size"] if is_combined else q["video_size"]
                    sz_str = format_size(sz_val)
                    options.append(
                        ft.DropdownOption(
                            key=str(h),
                            text=f"{h}p{fps_part}{badge_part}  •  {sz_str}",
                        )
                    )
                default_key = str(self.video_qualities[0]["height"])
            else:
                options.append(
                    ft.DropdownOption(
                        key="auto",
                        text="Mejor calidad disponible (Automático)  •  Peso variable",
                    )
                )
                default_key = "auto"
        else:
            # Solo Audio
            for a in self.audio_options:
                options.append(
                    ft.DropdownOption(
                        key=a["key"],
                        text=f"{a['label']}  •  {a['size_str']}",
                    )
                )
            default_key = "mp3_192"

        self.quality_dropdown.options = options
        self.quality_dropdown.value = default_key
        self._update_estimated_size_display()

    def _on_quality_change(self, e):
        self._update_estimated_size_display()
        self.page.update()

    def _update_estimated_size_display(self):
        """Actualiza el recuadro de peso estimado según el modo y la calidad activa."""
        selected_key = self.quality_dropdown.value
        if not self.video_info or not selected_key:
            self.estimated_size_value.value = "—"
            self.estimated_format_tag.value = "Sin selección"
            return

        if self.current_mode in ("video_audio", "video_only"):
            is_combined = (self.current_mode == "video_audio")
            target_q = None
            if selected_key == "auto" and self.video_qualities:
                target_q = self.video_qualities[0]
            else:
                for q in self.video_qualities:
                    if str(q["height"]) == str(selected_key):
                        target_q = q
                        break

            if target_q:
                sz = target_q["total_size"] if is_combined else target_q["video_size"]
                self.estimated_size_value.value = format_size(sz)
                mode_tag = "MP4 (Video + Audio)" if is_combined else "MP4 (Solo pista visual)"
                self.estimated_format_tag.value = f"{target_q['height']}p • {mode_tag}"
            else:
                self.estimated_size_value.value = "Peso variable"
                self.estimated_format_tag.value = "MP4 Estándar"
        else:
            target_a = None
            for a in self.audio_options:
                if a["key"] == selected_key:
                    target_a = a
                    break
            if target_a:
                self.estimated_size_value.value = target_a["size_str"]
                fmt_name = "M4A / AAC Original" if target_a["codec"] == "m4a" else f"MP3 a {target_a['bitrate']} kbps"
                self.estimated_format_tag.value = fmt_name
            else:
                self.estimated_size_value.value = "Peso variable"
                self.estimated_format_tag.value = "Audio"

    # -----------------------------------------------------------------
    # ACTUALIZACIÓN DE YT-DLP (CÓDIGO FUENTE Y EJECUTABLE .EXE)
    # -----------------------------------------------------------------

    def _on_update_engine_click(self, e):
        if self.is_updating_engine or self.is_downloading or self.is_analyzing:
            return

        self.is_updating_engine = True
        self.engine_update_spinner.visible = True
        self.engine_update_icon.visible = False
        self.engine_version_text.value = "Verificando yt-dlp..."
        self._set_status("Buscando actualización de yt-dlp...", STATUS_WARN)

        thread = threading.Thread(target=self._update_engine_worker, daemon=True)
        thread.start()

    def _update_engine_worker(self):
        current_ver = get_ytdlp_version()
        try:
            req = urllib.request.Request(
                "https://pypi.org/pypi/yt-dlp/json",
                headers={"User-Agent": "DescargadorYTStudio/1.0"},
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                pypi_data = json.loads(resp.read().decode("utf-8"))

            latest_ver = str(pypi_data.get("info", {}).get("version") or "").strip()
            if not latest_ver:
                raise RuntimeError("No se pudo obtener la versión más reciente desde PyPI.")

            if _parse_version_tuple(latest_ver) <= _parse_version_tuple(current_ver):
                self._set_status("yt-dlp está al día", STATUS_SUCCESS_GREEN)
                self._notify(f"El motor yt-dlp ya está en su última versión ({current_ver}).")
                return

            self.engine_version_text.value = f"Actualizando a {latest_ver}..."
            self._set_status(f"Actualizando yt-dlp a {latest_ver}...", STATUS_WARN)

            updated_via_pip = False
            if not getattr(sys, "frozen", False):
                try:
                    proc = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if proc.returncode == 0:
                        _reload_ytdlp_module()
                        updated_via_pip = True
                except Exception:
                    updated_via_pip = False

            if not updated_via_pip:
                # Actualización autónoma para .exe compilado (o respaldo sin pip) descargando el wheel oficial
                wheel_url = None
                for item in pypi_data.get("urls", []):
                    if item.get("packagetype") == "bdist_wheel" and str(item.get("filename", "")).endswith(".whl"):
                        wheel_url = item.get("url")
                        break

                if not wheel_url:
                    raise RuntimeError("No se encontró el paquete wheel oficial de yt-dlp.")

                os.makedirs(ENGINE_UPDATE_DIR, exist_ok=True)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".whl") as tmp_whl:
                    tmp_whl_path = tmp_whl.name

                try:
                    whl_req = urllib.request.Request(
                        wheel_url,
                        headers={"User-Agent": "DescargadorYTStudio/1.0"},
                    )
                    with urllib.request.urlopen(whl_req, timeout=60) as whl_resp, open(tmp_whl_path, "wb") as out_f:
                        shutil.copyfileobj(whl_resp, out_f)

                    target_pkg_dir = os.path.join(ENGINE_UPDATE_DIR, "yt_dlp")
                    staging_dir = os.path.join(ENGINE_UPDATE_DIR, "_staging")
                    if os.path.isdir(staging_dir):
                        shutil.rmtree(staging_dir, ignore_errors=True)
                    os.makedirs(staging_dir, exist_ok=True)

                    with zipfile.ZipFile(tmp_whl_path, "r") as zf:
                        for member in zf.namelist():
                            if member.startswith("yt_dlp/"):
                                zf.extract(member, staging_dir)

                    extracted_pkg = os.path.join(staging_dir, "yt_dlp")
                    if not os.path.isdir(extracted_pkg):
                        raise RuntimeError("El paquete descargado no contiene yt_dlp.")

                    if os.path.isdir(target_pkg_dir):
                        shutil.rmtree(target_pkg_dir, ignore_errors=True)
                    shutil.move(extracted_pkg, target_pkg_dir)
                    shutil.rmtree(staging_dir, ignore_errors=True)

                    with open(ENGINE_META_FILE, "w", encoding="utf-8") as mf:
                        json.dump({"version": latest_ver, "updated_at": time.time()}, mf, indent=2)

                    _reload_ytdlp_module(ENGINE_UPDATE_DIR)
                finally:
                    if os.path.isfile(tmp_whl_path):
                        try:
                            os.remove(tmp_whl_path)
                        except Exception:
                            pass

            new_ver = get_ytdlp_version()
            self._set_status("Motor yt-dlp actualizado", STATUS_SUCCESS_GREEN)
            self._notify(f"¡Motor yt-dlp actualizado exitosamente a la versión {new_ver}!")

        except Exception as ex:
            self._set_status("Error al actualizar yt-dlp", STATUS_ERR)
            self._notify(f"No se pudo actualizar yt-dlp: {str(ex)[:120]}", is_error=True)

        finally:
            self.is_updating_engine = False
            self.engine_update_spinner.visible = False
            self.engine_update_icon.visible = True
            self.engine_version_text.value = f"yt-dlp {get_ytdlp_version()}"
            self._safe_update()

    # -----------------------------------------------------------------
    # DESCARGA EN TIEMPO REAL CON YT-DLP (EN HILO SECUNDARIO)
    # -----------------------------------------------------------------

    def _get_selected_quality_meta(self, mode: str, selected_key: str | None) -> dict | None:
        """Obtiene los metadatos exactos (format_id y pesos) de la opción elegida."""
        if mode in ("video_audio", "video_only"):
            if not self.video_qualities:
                return None
            if not selected_key or selected_key == "auto":
                return self.video_qualities[0]
            for q in self.video_qualities:
                if str(q["height"]) == str(selected_key):
                    return q
            return self.video_qualities[0]
        else:
            for a in self.audio_options:
                if a["key"] == selected_key:
                    return a
            return self.audio_options[0] if self.audio_options else None

    def _on_cancel_download_click(self, e):
        """Solicita la cancelación inmediata de la descarga activa."""
        if not self.is_downloading or self.cancel_requested:
            return
        self.cancel_requested = True
        self.cancel_download_btn.disabled = True
        self.cancel_download_btn.opacity = 0.5
        self.progress_phase_text.value = "Cancelando descarga y limpiando archivos temporales..."
        self.progress_speed_text.value = "Deteniendo transferencia..."
        self._set_status("Cancelando descarga...", STATUS_WARN)

    def _cleanup_cancelled_files(self, tracked_files: set[str]):
        """Elimina archivos .part, .ytdl y pistas parciales generadas por una descarga cancelada."""
        prefixes: set[str] = set()
        for path in tracked_files:
            if not path:
                continue
            for candidate in (path, f"{path}.part", f"{path}.ytdl", f"{path}.temp"):
                if os.path.isfile(candidate):
                    try:
                        os.remove(candidate)
                    except Exception:
                        pass
            # Obtener prefijo base del video para limpiar fragmentos .fXXX.mp4.part
            base_name = os.path.basename(path)
            stem = re.sub(r"(\.f\d+)?\.(mp4|m4a|webm|mp3|part|ytdl|temp)+$", "", base_name, flags=re.IGNORECASE)
            if stem:
                prefixes.add(stem)

        if prefixes and os.path.isdir(self.downloads_dir):
            try:
                for entry in os.listdir(self.downloads_dir):
                    for stem in prefixes:
                        if entry.startswith(stem) and (
                            entry.endswith(".part")
                            or entry.endswith(".ytdl")
                            or entry.endswith(".temp")
                            or re.search(r"\.f\d+\.(mp4|m4a|webm)$", entry, flags=re.IGNORECASE)
                        ):
                            full_p = os.path.join(self.downloads_dir, entry)
                            try:
                                os.remove(full_p)
                            except Exception:
                                pass
            except Exception:
                pass

    def _on_download_click(self, e):
        if self.is_downloading or self.is_analyzing or not self.video_info:
            return

        url = self.video_info.get("webpage_url") or (self.url_input.value or "").strip()
        if not url:
            return

        selected_key = self.quality_dropdown.value
        mode = self.current_mode
        target_meta = self._get_selected_quality_meta(mode, selected_key)

        self.is_downloading = True
        self.cancel_requested = False
        self.last_saved_file = None
        self.saved_file_row.visible = False
        self.open_file_btn.visible = False

        # Bloquear controles mientras descarga y mostrar botón Cancelar
        self.download_btn.disabled = True
        self.download_spinner.visible = True
        self.download_icon.visible = False
        self.download_btn_text.value = "Descargando..."
        self.cancel_download_btn.visible = True
        self.cancel_download_btn.disabled = False
        self.cancel_download_btn.opacity = 1.0
        self.options_card.disabled = True

        self._set_progress_bar(0.0)
        self.progress_percent_text.value = "0.0%"
        self.progress_phase_text.value = "Iniciando descarga..."
        self.progress_speed_text.value = "Conectando con el servidor..."

        # Mostrar desde el inicio el peso total esperado
        expected_total = 0
        if target_meta:
            if mode == "video_audio":
                expected_total = target_meta.get("total_size") or 0
            elif mode == "video_only":
                expected_total = target_meta.get("video_size") or 0
            else:
                expected_total = target_meta.get("size_bytes") or 0

        if expected_total > 0:
            self.progress_bytes_text.value = f"0.0 MB / {format_size(expected_total).lstrip('~')}"
        else:
            self.progress_bytes_text.value = "0.0 MB"

        self._set_status("Descargando archivo multimedia...", ACCENT_IG_PINK)

        thread = threading.Thread(
            target=self._download_worker,
            args=(url, mode, selected_key, target_meta, expected_total),
            daemon=True,
        )
        thread.start()

    def _download_worker(
        self,
        url: str,
        mode: str,
        selected_key: str | None,
        target_meta: dict | None,
        expected_total_bytes: int,
    ):
        ensure_ffmpeg_ready()
        os.makedirs(self.downloads_dir, exist_ok=True)
        output_template = os.path.join(self.downloads_dir, "%(title)s.%(ext)s")

        # Seguimiento acumulativo para cuando yt-dlp descarga múltiples pistas (Video + Audio)
        stream_downloaded: dict[str, int] = {}
        stream_totals: dict[str, int] = {}
        stream_order: list[str] = []
        tracked_files: set[str] = set()
        last_ui_ts = 0.0

        def progress_hook(d: dict):
            nonlocal last_ui_ts
            for key in ("filename", "tmpfilename"):
                val = d.get(key)
                if val:
                    tracked_files.add(val)

            if self.cancel_requested:
                raise DownloadCancelledError("Descarga cancelada por el usuario")

            status = d.get("status")
            info_dict = d.get("info_dict") or {}
            stream_id = str(info_dict.get("format_id") or d.get("filename") or "main")
            if stream_id not in stream_order:
                stream_order.append(stream_id)

            if status == "downloading":
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                speed = d.get("speed")
                eta = d.get("eta")

                stream_downloaded[stream_id] = downloaded
                if total > 0:
                    stream_totals[stream_id] = total

                cum_downloaded = sum(stream_downloaded.values())

                # Si estamos en Video + Audio y apenas va por la 1ra pista, sumamos el peso de la 2da pista
                known_sum = sum(stream_totals.values())
                if mode == "video_audio" and len(stream_order) == 1 and target_meta:
                    audio_sz = target_meta.get("audio_size") or 0
                    overall_total = max(known_sum + audio_sz, expected_total_bytes, cum_downloaded)
                else:
                    overall_total = max(known_sum, expected_total_bytes, cum_downloaded)

                if overall_total > 0:
                    ratio = min(max(cum_downloaded / overall_total, 0.0), 1.0)
                    self._set_progress_bar(ratio)
                    self.progress_percent_text.value = f"{ratio * 100:.1f}%"
                    self.progress_bytes_text.value = (
                        f"{format_size(cum_downloaded).lstrip('~')} / {format_size(overall_total).lstrip('~')}"
                    )
                else:
                    self._set_progress_bar(None)
                    self.progress_bytes_text.value = f"{format_size(cum_downloaded).lstrip('~')} descargados"

                speed_str = format_speed(speed)
                eta_str = f" • ETA {format_duration(eta)}" if eta else ""
                self.progress_speed_text.value = f"Velocidad: {speed_str}{eta_str}"

                if mode == "video_audio":
                    step_num = stream_order.index(stream_id) + 1
                    phase_label = "pista de video" if step_num == 1 else "pista de audio"
                    self.progress_phase_text.value = f"Descargando {phase_label} ({step_num}/2)..."
                else:
                    self.progress_phase_text.value = "Descargando desde YouTube..."

                fn = d.get("filename")
                if fn:
                    self.last_saved_file = fn

                now = time.monotonic()
                if now - last_ui_ts >= 0.05:
                    last_ui_ts = now
                    self._safe_update()

            elif status == "finished":
                fn = d.get("filename")
                if fn:
                    self.last_saved_file = fn
                downloaded = d.get("downloaded_bytes") or d.get("total_bytes") or stream_totals.get(stream_id, 0)
                if downloaded > 0:
                    stream_downloaded[stream_id] = downloaded
                    stream_totals[stream_id] = downloaded

                # Si aún falta la 2da pista (audio) en modo combinado, no saltar al 100% todavía
                if mode == "video_audio" and len(stream_order) == 1 and target_meta and target_meta.get("audio_format_id"):
                    cum_downloaded = sum(stream_downloaded.values())
                    overall_total = max(cum_downloaded + (target_meta.get("audio_size") or 0), expected_total_bytes)
                    if overall_total > 0:
                        ratio = min(max(cum_downloaded / overall_total, 0.0), 0.95)
                        self._set_progress_bar(ratio)
                        self.progress_percent_text.value = f"{ratio * 100:.1f}%"
                        self.progress_bytes_text.value = (
                            f"{format_size(cum_downloaded).lstrip('~')} / {format_size(overall_total).lstrip('~')}"
                        )
                    self.progress_phase_text.value = "Pista de video lista, descargando audio (2/2)..."
                else:
                    cum_downloaded = sum(stream_downloaded.values())
                    self._set_progress_bar(1.0)
                    self.progress_percent_text.value = "100%"
                    if cum_downloaded > 0:
                        sz_txt = format_size(cum_downloaded).lstrip("~")
                        self.progress_bytes_text.value = f"{sz_txt} / {sz_txt}"
                    self.progress_phase_text.value = "Combinando / procesando con FFmpeg..."
                    self.progress_speed_text.value = "Finalizando contenedor multimedia"
                self._safe_update()

        def postprocessor_hook(d: dict):
            if self.cancel_requested:
                raise DownloadCancelledError("Descarga cancelada por el usuario")
            status = d.get("status")
            info_dict = d.get("info_dict") or {}
            filepath = info_dict.get("filepath") or info_dict.get("_filename")
            if filepath:
                tracked_files.add(filepath)
                self.last_saved_file = filepath
            if status == "started":
                self._set_status("Procesando con FFmpeg...", STATUS_WARN)
            elif status == "finished":
                self._set_progress_bar(1.0)
                self._safe_update()

        # Construir opciones de yt-dlp usando bloques de 512 KB y nombres compatibles con Windows
        base_opts = {
            "outtmpl": output_template,
            "windowsfilenames": True,
            "trim_file_name": 180,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "http_chunk_size": 524288,
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [postprocessor_hook],
        }

        if mode == "video_audio":
            v_fid = target_meta.get("video_format_id") if target_meta else None
            a_fid = target_meta.get("audio_format_id") if target_meta else None
            h = target_meta.get("height") if target_meta else (
                int(selected_key) if selected_key and selected_key != "auto" else None
            )

            exact_part = ""
            if v_fid and a_fid:
                exact_part = f"{v_fid}+{a_fid}/"
            elif v_fid:
                exact_part = f"{v_fid}+bestaudio[ext=m4a]/"

            if h:
                fmt = (
                    f"{exact_part}"
                    f"bestvideo[height={h}][vcodec^=avc]+bestaudio[ext=m4a]/"
                    f"bestvideo[height={h}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo[height={h}]+bestaudio/"
                    f"bestvideo[height<={h}]+bestaudio/best"
                )
            else:
                fmt = f"{exact_part}bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"

            ydl_opts = {
                **base_opts,
                "format": fmt,
                "merge_output_format": "mp4",
            }

        elif mode == "video_only":
            v_fid = target_meta.get("video_format_id") if target_meta else None
            h = target_meta.get("height") if target_meta else (
                int(selected_key) if selected_key and selected_key != "auto" else None
            )
            exact_part = f"{v_fid}/" if v_fid else ""

            if h:
                fmt = (
                    f"{exact_part}"
                    f"bestvideo[height={h}][vcodec^=avc][ext=mp4]/"
                    f"bestvideo[height={h}][ext=mp4]/"
                    f"bestvideo[height={h}]/best[height={h}]"
                )
            else:
                fmt = f"{exact_part}bestvideo[ext=mp4]/bestvideo/best"

            ydl_opts = {
                **base_opts,
                "format": fmt,
                "postprocessors": [{
                    "key": "FFmpegVideoRemuxer",
                    "preferedformat": "mp4",
                }],
            }

        else:
            # Solo audio
            if selected_key and selected_key.startswith("mp3_"):
                bitrate = selected_key.split("_")[1]
                ydl_opts = {
                    **base_opts,
                    "format": "bestaudio/best",
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": bitrate,
                    }],
                }
            else:
                ydl_opts = {
                    **base_opts,
                    "format": "bestaudio[ext=m4a]/bestaudio/best",
                }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not self.last_saved_file and info:
                    req_dl = info.get("requested_downloads")
                    if req_dl and isinstance(req_dl, list) and req_dl[0].get("filepath"):
                        self.last_saved_file = req_dl[0]["filepath"]
                    else:
                        self.last_saved_file = ydl.prepare_filename(info)

            # Ajustar extensión si fue convertido a mp4/mp3 y el path intermedio tenía otra extensión
            if self.last_saved_file and not os.path.isfile(self.last_saved_file):
                base_no_ext = os.path.splitext(self.last_saved_file)[0]
                for ext in (".mp4", ".mp3", ".m4a", ".webm"):
                    candidate = base_no_ext + ext
                    if os.path.isfile(candidate):
                        self.last_saved_file = candidate
                        break

            saved_path = self.last_saved_file or self.downloads_dir
            saved_name = os.path.basename(saved_path) if os.path.isfile(saved_path) else saved_path

            # Mostrar el peso real final del archivo guardado en el disco
            if os.path.isfile(saved_path):
                final_disk_bytes = os.path.getsize(saved_path)
                final_sz_str = format_size(final_disk_bytes).lstrip("~")
                self.progress_bytes_text.value = f"{final_sz_str} / {final_sz_str}"

            self._set_progress_bar(1.0)
            self.progress_percent_text.value = "100%"
            self.progress_phase_text.value = "¡Descarga completada con éxito!"
            self.progress_speed_text.value = "Archivo listo en tu equipo"

            self.saved_file_text.value = f"Guardado: {saved_name}"
            self.open_file_btn.visible = os.path.isfile(saved_path)
            self.saved_file_row.visible = True

            self._set_status("Descarga completada", STATUS_SUCCESS_GREEN)
            self._notify(f"¡Descarga completada! Guardado en {self.downloads_dir}")

        except Exception as ex:
            if isinstance(ex, DownloadCancelledError) or self.cancel_requested:
                # Dar un breve margen para que yt-dlp/FFmpeg libere los descriptores de archivo y limpiar
                time.sleep(0.15)
                self._cleanup_cancelled_files(tracked_files)
                self.last_saved_file = None
                self.saved_file_row.visible = False
                self._set_progress_bar(0.0)
                self.progress_percent_text.value = "0%"
                self.progress_phase_text.value = "Descarga cancelada por el usuario"
                self.progress_speed_text.value = "Archivos temporales eliminados"
                self.progress_bytes_text.value = "0.0 MB"
                self._set_status("Descarga cancelada", STATUS_WARN)
                self._notify("Descarga cancelada y archivos temporales eliminados.")
            else:
                err_msg = clean_ansi(str(ex))
                self._set_progress_bar(0.0)
                self.progress_percent_text.value = "Error"
                self.progress_phase_text.value = "Ocurrió un problema durante la descarga"
                self.progress_speed_text.value = err_msg[:95]
                self._set_status("Error en la descarga", STATUS_ERR)
                self._notify(f"Error al descargar: {err_msg[:140]}", is_error=True)

        finally:
            self.is_downloading = False
            self.cancel_requested = False
            self.cancel_download_btn.visible = False
            self.cancel_download_btn.disabled = False
            self.cancel_download_btn.opacity = 1.0
            self.download_btn.disabled = False
            self.download_spinner.visible = False
            self.download_icon.visible = True
            self.download_btn_text.value = "Descargar ahora"
            self.options_card.disabled = False
            self._safe_update()

    def _on_open_saved_file(self, e):
        if self.last_saved_file and os.path.isfile(self.last_saved_file):
            try:
                os.startfile(self.last_saved_file)
            except Exception as ex:
                self._notify(f"No se pudo abrir el archivo: {ex}", is_error=True)


def main(page: ft.Page):
    DescargadorYTApp(page)


if __name__ == "__main__":
    ft.run(main)
