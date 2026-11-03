@echo off
title Instalador - Descargador de YouTube
cd /d "%~dp0"

echo ============================================================
echo       INSTALADOR AUTOMATICO - DESCARGADOR DE YOUTUBE
echo ============================================================
echo.

:: 1. Verificar si Python esta en PATH
set "PYTHON_CMD="
where python >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=python"
    goto :python_listo
)

where py >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=py"
    goto :python_listo
)

:: 2. Buscar en carpetas de usuario comunes
if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
    set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    goto :python_listo
)
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
    set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    goto :python_listo
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    goto :python_listo
)
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    goto :python_listo
)

:: 3. Si no existe, ofrecer instalarlo
echo [!] No se detecto ninguna instalacion de Python en este equipo.
echo.
echo Para que el descargador funcione, es necesario contar con Python.
echo Podemos instalarlo de forma automatica y segura a nivel de usuario.
echo No requiere permisos de administrador.
echo.
set /p "INSTALAR=Deseas instalar Python automaticamente ahora? (s/n): "
if /i not "%INSTALAR%"=="s" goto :cancelar_instalacion

echo.
echo [*] Iniciando instalacion de Python 3.12...
where winget >nul 2>nul
if %errorlevel% equ 0 goto :instalar_winget

:: Fallback si winget no esta disponible
echo [*] Descargando instalador oficial de python.org...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object System.Net.WebClient).DownloadFile('https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe', '%TEMP%\python_setup.exe')"
echo [*] Instalando en segundo plano a nivel de usuario...
"%TEMP%\python_setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
del "%TEMP%\python_setup.exe" >nul 2>nul
goto :post_instalacion_py

:instalar_winget
echo [*] Descargando e instalando a traves de Windows Package Manager winget...
winget install --id Python.Python.3.12 --scope user --silent --accept-source-agreements --accept-package-agreements

:post_instalacion_py
for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USER_PATH=%%b"
if defined USER_PATH set "PATH=%USER_PATH%;%PATH%"

if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    goto :python_listo
)

where python >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=python"
    goto :python_listo
)

echo.
echo [!] La instalacion de Python finalizo, pero requiere reiniciar esta ventana.
echo Por favor cierra esta ventana y vuelve a ejecutar instalar.bat.
echo.
pause
exit /b 1

:cancelar_instalacion
echo.
echo [*] Operacion cancelada por el usuario.
echo Puedes instalar Python manualmente descargandolo desde: https://www.python.org
echo Asegurate de marcar la casilla 'Add python.exe to PATH' durante la instalacion.
echo.
pause
exit /b 1

:python_listo
echo [+] Python detectado correctamente:
"%PYTHON_CMD%" --version
echo.

:: 4. Crear el entorno virtual si no existe
if exist "venv\Scripts\activate.bat" goto :venv_existe

echo [*] Creando el entorno virtual en la carpeta venv...
"%PYTHON_CMD%" -m venv venv
if errorlevel 1 (
    echo [x] Hubo un error al crear el entorno virtual.
    pause
    exit /b 1
)
echo [+] Entorno virtual creado exitosamente.
goto :instalar_deps

:venv_existe
echo [+] El entorno virtual ya existe.

:instalar_deps
:: 5. Instalar dependencias requeridas
echo.
echo [*] Instalando dependencias desde requirements.txt...
call "venv\Scripts\activate.bat"
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [x] Error al instalar las dependencias con pip.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo         [+] INSTALACION FINALIZADA CON EXITO
echo ============================================================
echo.
echo Ya puedes usar el programa haciendo doble clic en:
echo    iniciar.bat
echo.
pause
exit /b 0
