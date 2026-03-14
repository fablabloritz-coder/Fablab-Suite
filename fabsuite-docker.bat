@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

:: ============================================================
::  FabLab Suite — Lancement Docker complet (simulation Ubuntu)
::  Usage: fabsuite-docker.bat [start|stop|restart|status|logs]
:: ============================================================

set "ROOT=%~dp0"
set "APPS=FabHome Fabtrack PretGo FabBoard"

:: Couleurs
set "GREEN=[92m"
set "RED=[91m"
set "YELLOW=[93m"
set "CYAN=[96m"
set "RESET=[0m"

:: Ports pour le health check
set "PORT_FabHome=3001"
set "PORT_Fabtrack=5555"
set "PORT_PretGo=5000"
set "PORT_FabBoard=5580"

:: Noms des conteneurs
set "CONTAINER_FabHome=fabhome"
set "CONTAINER_Fabtrack=fabtrack"
set "CONTAINER_PretGo=pretgo"
set "CONTAINER_FabBoard=fabboard"

:: Parse argument (défaut: start)
set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=start"

if /i "%ACTION%"=="start"   goto :do_start
if /i "%ACTION%"=="stop"    goto :do_stop
if /i "%ACTION%"=="restart" goto :do_restart
if /i "%ACTION%"=="status"  goto :do_status
if /i "%ACTION%"=="logs"    goto :do_logs
goto :usage

:: ============================================================
:do_start
:: ============================================================
echo.
echo %CYAN%========================================%RESET%
echo   FabLab Suite — Démarrage Docker
echo %CYAN%========================================%RESET%
echo.

:: Vérifier que Docker est accessible
docker info >nul 2>&1
if errorlevel 1 (
    echo %RED%ERREUR: Docker n'est pas démarré ou pas accessible.%RESET%
    echo Lancez Docker Desktop puis réessayez.
    exit /b 1
)

set "FAIL=0"
for %%A in (%APPS%) do (
    echo %YELLOW%[%%A]%RESET% Build ^& start...
    pushd "%ROOT%%%A"
    if exist docker-compose.yml (
        docker compose up -d --build
        if errorlevel 1 (
            echo %RED%  ✗ Échec pour %%A%RESET%
            set "FAIL=1"
        ) else (
            echo %GREEN%  ✓ %%A démarré%RESET%
        )
    ) else (
        echo %RED%  ✗ docker-compose.yml introuvable pour %%A%RESET%
        set "FAIL=1"
    )
    popd
    echo.
)

:: Attendre que les healthchecks passent
echo %CYAN%Attente des health checks (30s max)...%RESET%
timeout /t 10 /nobreak >nul

goto :do_status_internal

:: ============================================================
:do_stop
:: ============================================================
echo.
echo %CYAN%========================================%RESET%
echo   FabLab Suite — Arrêt Docker
echo %CYAN%========================================%RESET%
echo.

for %%A in (%APPS%) do (
    echo %YELLOW%[%%A]%RESET% Arrêt...
    pushd "%ROOT%%%A"
    if exist docker-compose.yml (
        docker compose down
        echo %GREEN%  ✓ %%A arrêté%RESET%
    )
    popd
)
echo.
echo %GREEN%Toutes les apps sont arrêtées.%RESET%
goto :end

:: ============================================================
:do_restart
:: ============================================================
call :do_stop
echo.
call :do_start
goto :end

:: ============================================================
:do_status
:do_status_internal
:: ============================================================
echo.
echo %CYAN%========================================%RESET%
echo   FabLab Suite — État des conteneurs
echo %CYAN%========================================%RESET%
echo.
echo  App         Port   Conteneur    État
echo  ----------  -----  -----------  ----------------

for %%A in (%APPS%) do (
    set "P=!PORT_%%A!"
    set "C=!CONTAINER_%%A!"

    :: Vérifier si le conteneur tourne
    for /f "tokens=*" %%S in ('docker inspect -f "{{.State.Status}}" !C! 2^>nul') do set "STATE=%%S"
    if "!STATE!"=="running" (
        :: Tester la connexion HTTP
        curl -s -o nul -w "" --max-time 3 http://localhost:!P!/ >nul 2>&1
        if errorlevel 1 (
            echo  %YELLOW%%%A%RESET%	  !P!   !C!	%YELLOW%démarrage...%RESET%
        ) else (
            echo  %GREEN%%%A%RESET%	  !P!   !C!	%GREEN%OK%RESET%
        )
    ) else (
        echo  %RED%%%A%RESET%	  !P!   !C!	%RED%arrêté%RESET%
    )
    set "STATE="
)

echo.
echo %CYAN%URLs d'accès :%RESET%
echo   FabHome   http://localhost:3001
echo   Fabtrack  http://localhost:5555
echo   PretGo    http://localhost:5000
echo   FabBoard  http://localhost:5580
echo.
echo %CYAN%Enregistrer les apps dans FabHome :%RESET%
echo   Réglages ^> FabLab Suite ^> ajouter :
echo     http://host.docker.internal:5555   (Fabtrack)
echo     http://host.docker.internal:5000   (PretGo)
echo     http://host.docker.internal:5580   (FabBoard)
echo.
goto :end

:: ============================================================
:do_logs
:: ============================================================
echo.
echo %CYAN%Logs des 5 dernières minutes (toutes les apps de la suite) :%RESET%
echo.
for %%A in (%APPS%) do (
    echo %YELLOW%=== %%A ===%RESET%
    docker logs --since 5m !CONTAINER_%%A! 2>&1 | findstr /V "^$" | more +0
    echo.
)
goto :end

:: ============================================================
:usage
:: ============================================================
echo.
echo Usage: %~nx0 [start^|stop^|restart^|status^|logs]
echo.
echo   start    Build ^& démarre toutes les apps (défaut)
echo   stop     Arrête toutes les apps
echo   restart  Redémarre toutes les apps
echo   status   Affiche l'état des conteneurs
echo   logs     Affiche les logs récents (5 min)
echo.
goto :end

:end
endlocal
