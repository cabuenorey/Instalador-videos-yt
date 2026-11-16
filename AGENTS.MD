# Reglas del proyecto

- **Prohibido usar `read_file` en archivos `.ico`** (por ejemplo `img/icono.ico`). El tipo MIME `image/x-icon` no está soportado y provoca un error HTTP 400 que inutiliza el chat.
- Si necesitas verificar o inspeccionar un archivo `.ico`, usa `run_command` (PowerShell o Python con Pillow) para consultar sus propiedades o convertir una copia temporal a `.png` antes de leerla.
