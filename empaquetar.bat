@echo off
title Empaquetador - Descargador YT Studio
cd /d "%~dp0"

echo ============================================================
echo     EMPAQUETADOR DE INSTALADOR (.EXE) - DESCARGADOR YT
echo ============================================================
echo.

if not exist "venv\Scripts\activate.bat" (
    echo [!] No se encontro el entorno virtual 'venv'. Ejecuta primero instalar.bat
    pause
    exit /b 1
)

call "venv\Scripts\activate.bat"

echo [*] 1/3 Verificando herramientas de compilacion (flet-cli, pyinstaller)...
python -m pip install -q "flet[cli]" pyinstaller
python -c "import static_ffmpeg; static_ffmpeg.add_paths()"

echo.
echo [*] 2/3 Compilando aplicacion de escritorio con Flet + PyInstaller...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

flet pack main.py ^
    --onedir ^
    --yes ^
    --name "Descargador YT" ^
    --icon "img\icono.ico" ^
    --product-name "Descargador YT Studio" ^
    --file-description "Descargador de YouTube MP4 y MP3" ^
    --product-version "1.0.0" ^
    --file-version "1.0.0.0" ^
    --add-data "img:img" ^
    --add-binary "venv\Lib\site-packages\static_ffmpeg\bin\win32\ffmpeg.exe:ffmpeg_bin" ^
    --add-binary "venv\Lib\site-packages\static_ffmpeg\bin\win32\ffprobe.exe:ffmpeg_bin"

if errorlevel 1 (
    echo [x] Error al compilar el ejecutable con flet pack.
    pause
    exit /b 1
)

echo.
echo [*] 3/3 Generando instalador DescargadorYT_Setup.exe con Inno Setup...
set "ISCC_CMD="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_CMD=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC_CMD=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

if not defined ISCC_CMD (
    echo [*] Instalando Inno Setup 6 a nivel de usuario con winget...
    winget install --id JRSoftware.InnoSetup --scope user --silent --accept-source-agreements --accept-package-agreements
    if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_CMD=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
)

if not defined ISCC_CMD (
    echo [x] No se encontro Inno Setup (ISCC.exe).
    pause
    exit /b 1
)

"%ISCC_CMD%" "setup.iss"
if errorlevel 1 (
    echo [x] Error al generar el instalador con Inno Setup.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   [+] INSTALADOR GENERADO CON EXITO PARA GITHUB RELEASES
echo ============================================================
echo   Archivo listo en:
echo   %~dp0release\DescargadorYT_Setup.exe
echo ============================================================
echo.
pause
