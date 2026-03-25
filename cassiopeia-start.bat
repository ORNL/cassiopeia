@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: Ensure Docker is running, start it if not
docker info >nul 2>&1
if errorlevel 1 (
    echo Starting Docker Desktop...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    echo Waiting for Docker to be ready...
    set /a TRIES=0
    :WAIT_DOCKER
    timeout /t 5 /nobreak >nul
    docker info >nul 2>&1
    if not errorlevel 1 goto DOCKER_READY
    set /a TRIES=!TRIES!+1
    if !TRIES! geq 12 (
        echo ERROR: Docker did not start in time. Start Docker Desktop manually and retry.
        pause & exit /b 1
    )
    goto WAIT_DOCKER
    :DOCKER_READY
    echo Docker is ready.
)

docker compose up -d
if errorlevel 1 (
    echo.
    echo ERROR: failed to start Cassiopeia.
    pause
    exit /b 1
)

echo.
echo Cassiopeia is starting...
echo   Chat      -^> http://localhost:8001
echo   Dashboard -^> http://localhost:5173
echo.
echo Opening dashboard in browser (waiting for services to be ready)...
timeout /t 20 /nobreak > nul
start http://localhost:5173
