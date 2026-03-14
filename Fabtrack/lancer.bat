@echo off
chcp 65001 >nul
title FabTrack - Serveur localhost:5555
color 0B

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║     FabTrack - Lancement du serveur      ║
echo  ╚══════════════════════════════════════════╝
echo.

:: Vérifier que l'environnement virtuel existe
if not exist ".venv\Scripts\activate.bat" (
    echo  [ERREUR] Environnement virtuel introuvable.
    echo  Lancez d'abord installer.bat
    echo.
    pause
    exit /b 1
)

:: Activer l'environnement virtuel
call .venv\Scripts\activate.bat

:: Vérifier que Flask est installé
python -c "import flask" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERREUR] Flask n'est pas installe.
    echo  Lancez d'abord installer.bat
    echo.
    pause
    exit /b 1
)

:: Tuer tout processus déjà en écoute sur le port 5555
echo  [INFO] Verification du port 5555...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5555 " ^| findstr "LISTENING"') do (
    echo  [WARN] Processus %%a detecte sur le port 5555, arret en cours...
    taskkill /PID %%a /F >nul 2>&1
)
:: Petit délai pour laisser le port se libérer
timeout /t 1 /nobreak >nul

echo  [OK] Port 5555 libre.
echo.
echo  ══════════════════════════════════════════
echo   FabTrack demarre sur http://localhost:5555
echo   Appuyez sur Ctrl+C pour arreter le serveur
echo  ══════════════════════════════════════════
echo.

:: Ouvrir le navigateur automatiquement après un court délai
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:5555"

:: Lancer le serveur Flask
python app.py

echo.
echo  [INFO] Serveur arrete.
pause
