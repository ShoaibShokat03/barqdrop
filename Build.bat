@echo off
REM ===================================================================
REM  BarqDrop - build a standalone Windows package (no Python needed
REM  on the target machine).
REM
REM     Build.bat             folder build + installer (or portable zip)
REM     Build.bat --onefile   also produce a single portable .exe
REM     Build.bat --clean     wipe build/ and dist/ first
REM
REM  Output:
REM     dist\BarqDrop\BarqDrop.exe          the application
REM     dist\BarqDrop-Setup-1.0.0.exe       installer (needs Inno Setup 6)
REM     dist\BarqDrop-1.0.0-portable.zip    zipped, unpack-and-run
REM ===================================================================
setlocal EnableExtensions EnableDelayedExpansion
title BarqDrop - build
cd /d "%~dp0"
set "VERSION=1.0.0"

set "ONEFILE=0"
set "DOCLEAN=0"
for %%A in (%*) do (
    if /i "%%~A"=="--onefile" set "ONEFILE=1"
    if /i "%%~A"=="--clean"   set "DOCLEAN=1"
)

REM ------------------------------------------------------------ find python
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo.
    echo   Python 3.9+ is required to BUILD BarqDrop ^(the built app itself
    echo   does not need Python^). Install it from python.org and retry.
    echo.
    pause
    exit /b 1
)

REM ------------------------------------------------------ virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo Creating the virtual environment in .venv ...
    %PY% -m venv .venv || (echo Could not create .venv & pause & exit /b 1)
)
set "VPY=.venv\Scripts\python.exe"

echo Installing runtime and build dependencies ...
"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%VPY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (echo Dependency install failed. & pause & exit /b 1)
"%VPY%" -m pip install -r requirements-build.txt --quiet --disable-pip-version-check
if errorlevel 1 (echo PyInstaller install failed. & pause & exit /b 1)

REM ------------------------------------------------------------- sanity pass
echo.
echo Running the engine self-test before packaging ...
"%VPY%" tools\test_ranges.py || (echo Range checks failed - build aborted. & pause & exit /b 1)
"%VPY%" tools\selftest.py 32  || (echo Transfer self-test failed - build aborted. & pause & exit /b 1)

REM -------------------------------------------------------------------- icon
echo.
echo Generating the application icon ...
"%VPY%" tools\make_icon.py || (echo Icon generation failed. & pause & exit /b 1)

if "%DOCLEAN%"=="1" (
    echo Cleaning previous build output ...
    if exist build rmdir /s /q build
    if exist dist  rmdir /s /q dist
)

REM ------------------------------------------------------------- folder build
echo.
echo Building dist\BarqDrop ^(this takes a couple of minutes^) ...
set "BARQDROP_ONEFILE="
"%VPY%" -m PyInstaller barqdrop.spec --noconfirm --clean --log-level WARN
if errorlevel 1 (echo PyInstaller failed. & pause & exit /b 1)
if not exist "dist\BarqDrop\BarqDrop.exe" (echo Build produced no executable. & pause & exit /b 1)

copy /y "installer\Install.bat" "dist\BarqDrop\Install.bat" >nul 2>&1
copy /y "installer\Allow-Firewall.bat" "dist\BarqDrop\Allow-Firewall.bat" >nul 2>&1
copy /y "README.md" "dist\BarqDrop\README.md" >nul 2>&1

REM ---------------------------------------------------------- portable build
if "%ONEFILE%"=="1" (
    echo.
    echo Building the single-file portable executable ...
    set "BARQDROP_ONEFILE=1"
    "%VPY%" -m PyInstaller barqdrop.spec --noconfirm --clean --log-level WARN --distpath dist\portable
    set "BARQDROP_ONEFILE="
    if exist "dist\portable\BarqDrop.exe" (
        move /y "dist\portable\BarqDrop.exe" "dist\BarqDrop-%VERSION%-portable.exe" >nul
        rmdir /s /q "dist\portable" 2>nul
    )
)

REM ----------------------------------------------------------- zip the folder
echo.
echo Creating dist\BarqDrop-%VERSION%-portable.zip ...
"%VPY%" -c "import shutil;shutil.make_archive(r'dist/BarqDrop-%VERSION%-portable','zip',r'dist','BarqDrop')" >nul
if errorlevel 1 echo   (zip step failed - the dist\BarqDrop folder is still usable)

REM --------------------------------------------------------------- installer
set "ISCC="
where ISCC >nul 2>&1 && for /f "delims=" %%I in ('where ISCC') do set "ISCC=%%I"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"

if defined ISCC (
    echo.
    echo Compiling the Windows installer with Inno Setup ...
    "!ISCC!" /Q "installer\barqdrop.iss"
    if errorlevel 1 (
        echo   Installer compilation failed - the folder build is still fine.
    ) else (
        echo   Installer written to dist\BarqDrop-Setup-%VERSION%.exe
    )
) else (
    echo.
    echo   Inno Setup 6 was not found, so no .exe installer was produced.
    echo   Install it from https://jrsoftware.org/isdl.php and re-run Build.bat
    echo   to get dist\BarqDrop-Setup-%VERSION%.exe.
    echo.
    echo   Meanwhile dist\BarqDrop-%VERSION%-portable.zip is fully standalone:
    echo   unzip it anywhere and run BarqDrop.exe, or run Install.bat inside it
    echo   to install per-user with Start Menu and desktop shortcuts.
)

echo.
echo ============================================================
echo  Build finished. Contents of dist:
dir /b dist
echo ============================================================
echo.
pause
endlocal
