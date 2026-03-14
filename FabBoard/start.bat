@echo off
setlocal enabledelayedexpansion
color 0A
REM Script de lancement rapide pour Windows - FabBoard Phase 3
REM Lance FabBoard avec sync worker et ouvre le navigateur

echo ============================================================
echo   FabBoard - Tableau de bord Fablab (Phase 3)
echo   Sync Worker + Cache SQLite
echo ============================================================
echo.

REM Vérifier si Python est installé
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Python n'est pas installe ou pas dans le PATH
    echo Telechargez Python sur https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Aller dans le dossier fabboard
cd /d "%~dp0"

REM ========== KILL LES PROCESSUS EXISTANTS SUR PORT 5580 ==========
echo [1/4] Verification des processus existants sur port 5580...
for /f "tokens=5" %%a in ('netstat -aon ^| find ":5580" ^| find "LISTENING"') do (
    echo       Fermeture du processus PID %%a...
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

REM ========== SETUP ENVIRONNEMENT VIRTUAL ==========
echo [2/4] Configuration de l'environnement...
if not exist "venv\" (
    echo       Creation de l'environnement virtuel...
    python -m venv venv
    call venv\Scripts\activate
    echo       Installation des dependances...
    pip install -r requirements.txt >nul 2>&1
) else (
    call venv\Scripts\activate
)

REM ========== CONFIGURATION ==========
set FLASK_ENV=production
set FLASK_DEBUG=0
set FABBOARD_PORT=5580

REM ========== LANCEMENT DE L'APPLICATION ==========
echo [3/4] Demarrage de FabBoard...
echo       URL: http://localhost:5580/parametres
echo       (Vous serrez redirige automatiquement)
echo.

REM Lancer l'app en arrière-plan
start /min "" python app.py
timeout /t 3 /nobreak >nul

REM ========== OUVRIR LE NAVIGATEUR ==========
echo [4/4] Ouverture du navigateur web...
start http://localhost:5580/parametres

echo.
echo ============================================================
echo [OK] FabBoard est en cours d'execution!
echo.
echo Pour arreter:
echo   - Fermez cette fenetre (Ctrl+C)
echo   - Ou fermez la notification en haut a droite
echo   - Ou tuez le processus Python dans le Gestionnaire des taches
echo ============================================================
echo.

timeout /t 2 /nobreak
echo Dashboard: http://localhost:5580/
echo Parametres: http://localhost:5580/parametres
echo.

REM Garder la fenetre ouverte si erreur
cmd /k
