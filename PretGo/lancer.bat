@echo off
setlocal
title PretGo

set "PYTHON_EXE=%~dp0python\python.exe"

echo.
echo ==================================================
echo                    PretGo
echo ==================================================
echo.

REM Verifier que Python embarque est present
if not exist "%PYTHON_EXE%" (
    echo [ERREUR] Python embarque introuvable.
    echo Lancez d'abord installer.bat
    echo.
    pause
    exit /b 1
)

echo Demarrage du serveur...
echo L'application va s'ouvrir dans votre navigateur.
echo Ne fermez pas cette fenetre pendant l'utilisation.
echo Pour arreter: fermez cette fenetre ou Ctrl+C.
echo.

REM Ouvrir le navigateur apres un court delai
start "" /min cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:5000"

REM Lancer l'application avec le Python embarque
cd /d "%~dp0"
"%PYTHON_EXE%" app.py

echo.
echo Le serveur s'est arrete.
pause
