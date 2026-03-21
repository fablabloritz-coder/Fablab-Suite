@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PS1_FILE=%SCRIPT_DIR%inventaire_master.ps1"

if not exist "%PS1_FILE%" (
  echo [ERREUR] Script introuvable: "%PS1_FILE%"
  echo Assurez-vous que le .bat est dans le meme dossier que inventaire_master.ps1
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1_FILE%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo [ERREUR] L'inventaire a echoue (code %EXIT_CODE%).
  pause
)

exit /b %EXIT_CODE%
