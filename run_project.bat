@echo off
title Container-Based Application Development Virtual Labs
cd /d "%~dp0"

echo [1/2] Launching Docker Desktop...
if exist "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe" (
    start "" "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe"
) else if exist "C:\Program Files\Docker\Docker\Docker Desktop.exe" (
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
) else (
    echo Docker Desktop executable not found in default paths.
)

echo [2/2] Starting Virtual Labs Web Server...
echo Access URL: http://localhost:5000
echo.
.\venv\Scripts\python.exe backend\app.py
pause
