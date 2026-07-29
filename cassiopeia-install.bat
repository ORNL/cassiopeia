@echo off
:: Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
:: SPDX-License-Identifier: Apache-2.0
::
:: Cassiopeia — Windows installer (Docker Compose)
:: Run this file once after installing Docker Desktop.

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo   Cassiopeia — Windows installer
echo   Project directory: %~dp0
echo.

:: ── Docker check ─────────────────────────────────────────────────────────────
echo [1/4] Checking Docker...
where docker >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker not found.
    echo        Install Docker Desktop from https://www.docker.com/products/docker-desktop/
    echo        then re-run this installer.
    pause & exit /b 1
)
docker info >nul 2>&1
if not errorlevel 1 goto DOCKER_READY
echo        Docker is not running — starting Docker Desktop...
start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
echo        Waiting for Docker to be ready (this may take up to 60 seconds)...
set /a TRIES=0
:WAIT_DOCKER
timeout /t 5 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto DOCKER_READY
set /a TRIES=!TRIES!+1
if !TRIES! lss 12 goto WAIT_DOCKER
echo ERROR: Docker did not start in time.
echo        Please start Docker Desktop manually and re-run this installer.
pause & exit /b 1
:DOCKER_READY
echo        Docker is running.

:: ── .env setup ───────────────────────────────────────────────────────────────
echo [2/4] Configuring environment...
if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo        Created .env from .env.example.
    ) else (
        echo        WARNING: neither .env nor .env.example found.
        echo        Copy .env from another installation or contact your administrator.
        pause & exit /b 1
    )
) else (
    echo        .env found.
)
echo        NOTE: LLM API keys are configured per-user in the Settings UI after launch.

:: ── Build images ─────────────────────────────────────────────────────────────
echo [3/4] Building Docker images (first run may take several minutes)...
docker compose build
if errorlevel 1 (
    echo ERROR: docker compose build failed.
    pause & exit /b 1
)
echo        Images built successfully.

:: ── Desktop shortcuts ────────────────────────────────────────────────────────
echo [4/4] Creating desktop shortcuts...

set PUBLIC_DESKTOP=%USERPROFILE%\Desktop
set INSTALL_DIR=%~dp0

:: Use a temporary VBScript to create .lnk shortcuts
set VBS=%TEMP%\cassiopeia_shortcut.vbs

:: Start shortcut
(
    echo Set oWS = WScript.CreateObject^("WScript.Shell"^)
    echo Set sc = oWS.CreateShortcut^("%PUBLIC_DESKTOP%\Start Cassiopeia.lnk"^)
    echo sc.TargetPath = "%INSTALL_DIR%cassiopeia-start.bat"
    echo sc.WorkingDirectory = "%INSTALL_DIR%"
    echo sc.IconLocation = "%INSTALL_DIR%cassiopeia.ico,0"
    echo sc.Description = "Start Cassiopeia"
    echo sc.Save
) > "%VBS%"
cscript /nologo "%VBS%"

:: Stop shortcut
(
    echo Set oWS = WScript.CreateObject^("WScript.Shell"^)
    echo Set sc = oWS.CreateShortcut^("%PUBLIC_DESKTOP%\Stop Cassiopeia.lnk"^)
    echo sc.TargetPath = "%INSTALL_DIR%cassiopeia-stop.bat"
    echo sc.WorkingDirectory = "%INSTALL_DIR%"
    echo sc.IconLocation = "%INSTALL_DIR%cassiopeia.ico,0"
    echo sc.Description = "Stop Cassiopeia"
    echo sc.Save
) > "%VBS%"
cscript /nologo "%VBS%"

del "%VBS%" >nul 2>&1
echo        Shortcuts created on desktop.

:: ── Done ─────────────────────────────────────────────────────────────────────
echo.
echo   Installation complete.
echo.
echo   To start Cassiopeia: double-click "Start Cassiopeia" on the desktop,
echo                        or run cassiopeia-start.bat
echo   To stop:             double-click "Stop Cassiopeia",
echo                        or run cassiopeia-stop.bat
echo.
pause
