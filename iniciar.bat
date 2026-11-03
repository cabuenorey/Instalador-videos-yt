@echo off
title Descargador de YouTube
cd /d "%~dp0"

:: Verificar si existe el entorno virtual
if not exist "venv\Scripts\activate.bat" (
    echo ============================================================
    echo [!] No se encontro el entorno virtual en la carpeta 'venv'.
    echo ============================================================
    echo Por favor, crea el entorno virtual e instala las dependencias.
    pause
    exit /b 1
)

:: Activar el entorno virtual y ejecutar el script
call "venv\Scripts\activate.bat"
python main.py

:: Si ocurrio un error inesperado al ejecutarse, pausar para ver el mensaje
if errorlevel 1 (
    echo [!] La aplicacion se cerro con un codigo de error.
    pause
)

:: Cerrar la terminal por completo al terminar
exit
