@echo off
setlocal
title Installation - PretGo

echo.
echo ==================================================
echo              INSTALLATION - PretGo
echo ==================================================
echo.

REM Configuration
set "PYTHON_VERSION=3.13.9"
set "PYTHON_DIR=%~dp0python"
set "PYTHON_EXE=%PYTHON_DIR%\python.exe"
set "PYTHON_ZIP=python-%PYTHON_VERSION%-embed-amd64.zip"
set "PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/%PYTHON_ZIP%"

REM Verifier si deja installe
if exist "%PYTHON_EXE%" (
    echo [OK] Python embarque deja present.
    "%PYTHON_EXE%" -c "import flask" >nul 2>&1
    if not errorlevel 1 (
        echo [OK] Dependances deja installees.
        echo Installation deja complete.
        echo Lancez l'application avec lancer.bat
        echo.
        pause
        exit /b 0
    )
    echo Flask pas encore installe, reprise...
    echo.
    goto install_pip
)

REM Etape 1: Telechargement
echo [1/4] Telechargement de Python %PYTHON_VERSION%...
powershell -Command "& { try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%~dp0%PYTHON_ZIP%' -UseBasicParsing } catch { Write-Host '[ERREUR]' $_.Exception.Message; exit 1 } }"
if errorlevel 1 (
    echo.
    echo [ERREUR] Impossible de telecharger Python.
    echo Verifiez votre connexion Internet puis relancez installer.bat
    echo.
    pause
    exit /b 1
)
echo [OK] Python telecharge.
echo.

REM Etape 2: Extraction
echo [2/4] Extraction de Python...
if exist "%PYTHON_DIR%" rmdir /s /q "%PYTHON_DIR%"
powershell -Command "Expand-Archive -Path '%~dp0%PYTHON_ZIP%' -DestinationPath '%PYTHON_DIR%' -Force"
if errorlevel 1 (
    echo [ERREUR] Extraction echouee.
    pause
    exit /b 1
)
del "%~dp0%PYTHON_ZIP%" >nul 2>&1
echo [OK] Python extrait.
echo.

REM Etape 3: Installation de pip
:install_pip
echo [3/4] Installation de pip...

for %%f in ("%PYTHON_DIR%\python*._pth") do (
    powershell -Command "$c = Get-Content '%%f' -Raw; $c = $c -replace '#import site','import site'; if ($c -notmatch '\.\.') { $c = $c -replace '(python\d+\.zip)', \"`$1`n..\" }; Set-Content '%%f' $c"
)

powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%~dp0get-pip.py' -UseBasicParsing }"
if errorlevel 1 (
    echo [ERREUR] Impossible de telecharger pip.
    pause
    exit /b 1
)
"%PYTHON_EXE%" "%~dp0get-pip.py" --no-warn-script-location >nul 2>&1
if errorlevel 1 (
    del "%~dp0get-pip.py" >nul 2>&1
    echo [ERREUR] Installation de pip echouee.
    pause
    exit /b 1
)
del "%~dp0get-pip.py" >nul 2>&1
echo [OK] pip installe.
echo.

REM Etape 4: Installation des dependances
echo [4/4] Installation des dependances...
"%PYTHON_EXE%" -m pip install -r "%~dp0requirements.txt" --no-warn-script-location
if errorlevel 1 (
    echo [ERREUR] Installation des dependances echouee.
    pause
    exit /b 1
)
echo [OK] Dependances installees.
echo.

echo ==================================================
echo               INSTALLATION TERMINEE
echo ==================================================
echo Lancez maintenant l'application avec lancer.bat
echo.
pause
