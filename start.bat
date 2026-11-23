@echo off
setlocal enabledelayedexpansion
title [V-Lab] Unified Launcher (Single Point of Access)
cd /d "%~dp0"
set "PYTHONPATH=%~dp0;%PYTHONPATH%"

:: -------------------------------------------------------------------------
:: 1. Runtime Discovery (Python & Docker)
:: -------------------------------------------------------------------------
if defined PYTHON_EXE (
    rem Using environment-specified PYTHON_EXE
) else if defined VLAB_PYTHON (
    set "PYTHON_EXE=%VLAB_PYTHON%"
) else if exist "venv\Scripts\python.exe" (
    set "PYTHON_EXE=venv\Scripts\python.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    where python >nul 2>&1 && set "PYTHON_EXE=python"
    if not defined PYTHON_EXE (
        where py >nul 2>&1 && set "PYTHON_EXE=py"
    )
    if not defined PYTHON_EXE (
        where python3 >nul 2>&1 && set "PYTHON_EXE=python3"
    )
    if not defined PYTHON_EXE (
        echo ===================================================================
        echo [ERROR] Python 3 runtime was not detected in this environment.
        echo Please install Python 3.10+ from https://www.python.org/downloads/
        echo ===================================================================
        pause
        exit /b 1
    )
)

:: Docker Daemon Verification
docker version >nul 2>&1
if %errorlevel% neq 0 (
    if exist "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe" (
        start "" "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe"
    ) else if exist "C:\Program Files\Docker\Docker\Docker Desktop.exe" (
        start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    ) else (
        echo [INFO] Real Docker daemon not detected. V-Lab container simulator is active.
    )
)

:: Network LAN IP Discovery (Allows environment override)
if defined HOST_IP set "LAN_IP=%HOST_IP%"
if not defined LAN_IP (
    set "LAN_IP=127.0.0.1"
    where powershell >nul 2>&1
    if %errorlevel% equ 0 (
        for /f "tokens=*" %%i in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias 'Wi-Fi*','Ethernet*','Local Area Connection*' -ErrorAction SilentlyContinue | Where-Object {$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*'}).IPAddress | Select-Object -First 1"') do (
            if not "%%i"=="" set "LAN_IP=%%i"
        )
    )
)

:: -------------------------------------------------------------------------
:: 2. Command Line Arguments Evaluation
:: -------------------------------------------------------------------------
set "MODE=%~1"
if /i "%MODE%"=="host1" goto RUN_HOST1
if /i "%MODE%"=="primary" goto RUN_HOST1
if /i "%MODE%"=="1" goto RUN_HOST1

if /i "%MODE%"=="host2" goto RUN_HOST2
if /i "%MODE%"=="standby" goto RUN_HOST2
if /i "%MODE%"=="2" goto RUN_HOST2

if /i "%MODE%"=="demo" goto RUN_DEMO
if /i "%MODE%"=="sim" goto RUN_DEMO
if /i "%MODE%"=="3" goto RUN_DEMO

if /i "%MODE%"=="controller" goto RUN_CONTROLLER
if /i "%MODE%"=="4" goto RUN_CONTROLLER

if /i "%MODE%"=="exit" exit /b 0
if /i "%MODE%"=="5" exit /b 0

:: -------------------------------------------------------------------------
:: 3. Interactive Menu (One Point of Access)
:: -------------------------------------------------------------------------
:MENU
cls
echo ========================================================================
echo   [V-Lab] Container-Based Virtual Lab - Unified Launcher
echo ========================================================================
echo   Detected Local LAN IP: %LAN_IP%
echo   Python Runtime:        %PYTHON_EXE%
echo ========================================================================
echo.
echo   Select the role for THIS machine:
echo.
echo   [1] HOST 1 (PRIMARY) - Main Lab Machine [RECOMMENDED]
echo       * Starts Central Web Controller (Port 5000)
echo       * Starts Host 1 Compute Node Agent (Port 5051)
echo       * Runs all student Docker workloads
echo.
echo   [2] HOST 2 (HOT STANDBY) - Secondary Lab Machine
echo       * Starts Standby Agent (Port 5052)
echo       * Pre-caches Docker images from Docker Hub
echo       * Stands ready for instant failover if Host 1 goes offline
echo.
echo   [3] LOCAL SIMULATION / DEMO (Single PC)
echo       * Starts Controller + Host 1 + Host 2 all on this PC
echo       * Allows testing automatic failover and SweetAlerts right now
echo.
echo   [4] WEB CONTROLLER ONLY (Port 5000)
echo.
echo   [5] EXIT
echo.
echo ========================================================================
set /p CHOICE="Enter choice [1-5] (Default is 1): "
if "%CHOICE%"=="" set CHOICE=1

if "%CHOICE%"=="1" goto RUN_HOST1
if "%CHOICE%"=="2" goto RUN_HOST2
if "%CHOICE%"=="3" goto RUN_DEMO
if "%CHOICE%"=="4" goto RUN_CONTROLLER
if "%CHOICE%"=="5" exit /b 0

echo Invalid choice. Please enter 1, 2, 3, 4, or 5.
timeout /t 2 >nul
goto MENU

:: -------------------------------------------------------------------------
:: Execution Modes
:: -------------------------------------------------------------------------

:RUN_HOST1
cls
echo ========================================================================
echo   [+] Starting HOST 1 (PRIMARY) + Central Controller
echo ========================================================================
echo   Local Web Access:    http://localhost:5000
echo   College LAN Access:  http://%LAN_IP%:5000
echo   Compute Agent:       HOST1 on Port 5051
echo.
echo   On Host 2 (Standby PC), point controller URL to:
echo   http://%LAN_IP%:5000
echo ========================================================================
echo.

:: Launch Host 1 Compute Agent in dedicated background window
start "V-Lab Compute Node (HOST 1 PRIMARY - Port 5051)" cmd /k "%PYTHON_EXE% agent\vlab_agent.py --host-id HOST1 --role PRIMARY --controller-url http://127.0.0.1:5000 --agent-port 5051"

:: Launch Web Controller in current foreground window
%PYTHON_EXE% backend\app.py
pause
goto MENU

:RUN_HOST2
cls
echo ========================================================================
echo   [*] Starting HOST 2 (HOT STANDBY NODE)
echo ========================================================================
set "CONFIG_FILE=host_node_config.txt"
set "CONTROLLER_URL="
if exist "%CONFIG_FILE%" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%CONFIG_FILE%") do (
        if "%%A"=="CONTROLLER_URL" set "CONTROLLER_URL=%%B"
    )
)

if not "%~2"=="" (
    set "CONTROLLER_URL=%~2"
)

if "%CONTROLLER_URL%"=="" (
    echo.
    echo Enter the Host 1 Controller URL as shown on Host 1's screen:
    echo (Example: http://192.168.1.105:5000 or http://127.0.0.1:5000)
    set /p CONTROLLER_URL="Controller URL: "
    if "!CONTROLLER_URL!"=="" set "CONTROLLER_URL=http://127.0.0.1:5000"
    (
        echo CONTROLLER_URL=!CONTROLLER_URL!
    ) > "%CONFIG_FILE%"
) else (
    echo Connected Controller: %CONTROLLER_URL%
    echo (To change this, edit or delete %CONFIG_FILE%)
)

echo.
echo Launching Host 2 Standby Agent on port 5052...
echo Pre-caching lab images in background for instant failover readiness...
echo ========================================================================
echo.

%PYTHON_EXE% agent\vlab_agent.py --host-id HOST2 --role HOT_STANDBY --controller-url %CONTROLLER_URL% --agent-port 5052
pause
goto MENU

:RUN_DEMO
cls
echo ========================================================================
echo   [!] Starting Complete Local Simulation (Controller + Host 1 + Host 2)
echo ========================================================================
echo   Host 1 Agent:  Port 5051 (Primary)
echo   Host 2 Agent:  Port 5052 (Hot Standby)
echo   Controller:    Port 5000
echo ========================================================================
echo.

start "V-Lab Agent 1 (HOST1 - Primary)" cmd /k "%PYTHON_EXE% agent\vlab_agent.py --host-id HOST1 --role PRIMARY --controller-url http://127.0.0.1:5000 --agent-port 5051"
start "V-Lab Agent 2 (HOST2 - Hot Standby)" cmd /k "%PYTHON_EXE% agent\vlab_agent.py --host-id HOST2 --role HOT_STANDBY --controller-url http://127.0.0.1:5000 --agent-port 5052"

echo Starting Controller...
%PYTHON_EXE% backend\app.py
pause
goto MENU

:RUN_CONTROLLER
cls
echo ========================================================================
echo   [+] Starting Web Controller Only (Port 5000)
echo ========================================================================
echo   Local Web Access:    http://localhost:5000
echo   College LAN Access:  http://%LAN_IP%:5000
echo ========================================================================
echo.
%PYTHON_EXE% backend\app.py
pause
goto MENU
