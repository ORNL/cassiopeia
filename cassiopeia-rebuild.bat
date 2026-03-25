@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo Rebuilding Cassiopeia images...
docker compose build
if errorlevel 1 (
    echo ERROR: build failed.
    pause & exit /b 1
)

echo Restarting services...
docker compose up -d
if errorlevel 1 (
    echo ERROR: failed to start services.
    pause & exit /b 1
)

echo Done. Services are restarting.
pause
