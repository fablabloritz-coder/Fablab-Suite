@echo off
chcp 65001 >nul
echo ============================================
echo   INVENTAIRE DU POSTE v3 - Lancement
echo ============================================
echo.
echo   Clic droit ^> Executer en tant qu'admin
echo   pour detecter tous les logiciels (AppX)
echo.

powershell -ExecutionPolicy Bypass -File "%~dp0inventaire.ps1"

echo.
echo ============================================
echo   INVENTAIRE TERMINE !
echo   Ouvrir le fichier HTML dans un navigateur
echo ============================================
echo.
pause
