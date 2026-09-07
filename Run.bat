@echo off
REM ===================================================================
REM  BarqDrop - create/use the .venv, install dependencies, launch.
REM     Run.bat            start the app
REM     Run.bat --console  start it with a visible console (for logs)
REM     Run.bat --check    only prepare the environment, then report
REM     Run.bat --test     prepare, then run the engine self-test
REM ===================================================================
setlocal EnableExtensions EnableDelayedExpansion
title BarqDrop
cd /d "%~dp0"

REM ------------------------------------------------------------ find python
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo.
    echo   Python 3.9 or newer was not found on this machine.
    echo   Install it from https://www.python.org/downloads/windows/
    echo   and tick "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
)

REM ------------------------------------------------------ virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo Creating the virtual environment in .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo.
        echo   Could not create the virtual environment.
        pause
        exit /b 1
    )
    if exist ".venv\deps.stamp" del ".venv\deps.stamp" >nul 2>&1
)

set "VPY=.venv\Scripts\python.exe"
set "VPYW=.venv\Scripts\pythonw.exe"
if not exist "%VPYW%" set "VPYW=%VPY%"

REM ------------------------------- install requirements only when they change
"%VPY%" -c "import hashlib;print(hashlib.sha256(open('requirements.txt','rb').read()).hexdigest())" > ".venv\req.hash"
set "REQHASH="
set "OLDHASH="
set /p REQHASH=<".venv\req.hash"
if exist ".venv\deps.stamp" set /p OLDHASH=<".venv\deps.stamp"
if not defined REQHASH set "REQHASH=unknown"

if not "!REQHASH!"=="!OLDHASH!" (
    echo Installing dependencies ^(the first run downloads ~100 MB and takes a few minutes^) ...
    "%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
    "%VPY%" -m pip install -r requirements.txt --disable-pip-version-check
    if errorlevel 1 (
        echo.
        echo   Dependency installation failed - check the internet connection and retry.
        pause
        exit /b 1
    )
    > ".venv\deps.stamp" echo !REQHASH!
)

if not exist "assets\barqdrop.ico" "%VPY%" tools\make_icon.py >nul 2>&1

REM ------------------------------------------------------------------ launch
if /i "%~1"=="--check" (
    echo.
    "%VPY%" -c "import sys,PySide6,cryptography,psutil;print('Python      ', sys.version.split()[0]);print('PySide6     ', PySide6.__version__);print('cryptography', cryptography.__version__);print('psutil      ', psutil.__version__)"
    "%VPY%" -c "import importlib.util as u;print('winsdk       ', 'installed (Wi-Fi Direct hotspot available)' if u.find_spec('winsdk') else 'not installed (Wi-Fi Direct hotspot disabled)')"
    echo.
    echo Environment is ready. Run.bat with no arguments starts BarqDrop.
    exit /b 0
)
if /i "%~1"=="--test" (
    "%VPY%" tools\test_ranges.py
    "%VPY%" tools\selftest.py 64
    exit /b %errorlevel%
)
if /i "%~1"=="--console" (
    "%VPY%" run_barqdrop.py
    exit /b %errorlevel%
)

echo Starting BarqDrop ...
start "BarqDrop" "%VPYW%" run_barqdrop.py
endlocal
