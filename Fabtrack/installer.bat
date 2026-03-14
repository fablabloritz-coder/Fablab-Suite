@echo off
chcp 65001 >nul
title FabTrack - Installation des dépendances
color 0A

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║     FabTrack - Installation             ║
echo  ╚══════════════════════════════════════════╝
echo.

:: Vérifier que Python est installé
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERREUR] Python n'est pas installe ou n'est pas dans le PATH.
    echo  Telechargez Python 3.10+ sur https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

:: Afficher la version de Python
echo  [INFO] Version de Python detectee :
python --version
echo.

:: Créer l'environnement virtuel s'il n'existe pas
if not exist ".venv" (
    echo  [1/3] Creation de l'environnement virtuel...
    python -m venv .venv
    if %ERRORLEVEL% neq 0 (
        echo  [ERREUR] Impossible de creer l'environnement virtuel.
        pause
        exit /b 1
    )
    echo  [OK] Environnement virtuel cree.
) else (
    echo  [1/3] Environnement virtuel deja present.
)
echo.

:: Activer l'environnement virtuel
echo  [2/3] Activation de l'environnement virtuel...
call .venv\Scripts\activate.bat

:: Installer les dépendances
echo  [3/3] Installation des dependances (requirements.txt)...
echo.
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo.
    echo  [ERREUR] L'installation des dependances a echoue.
    pause
    exit /b 1
)

:: Créer le dossier uploads s'il n'existe pas
if not exist "static\uploads" (
    mkdir "static\uploads"
    echo.
    echo  [OK] Dossier static\uploads cree.
)

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║   Installation terminee avec succes !    ║
echo  ║                                          ║
echo  ║   Lancez l'application avec :            ║
echo  ║       lancer.bat                         ║
echo  ╚══════════════════════════════════════════╝
echo.
pause
