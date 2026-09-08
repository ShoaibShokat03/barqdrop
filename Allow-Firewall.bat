@echo off
REM ===================================================================
REM  BarqDrop - allow discovery and transfers through Windows Firewall.
REM
REM  RIGHT-CLICK this file and choose "Run as administrator".
REM
REM  Works both next to a packaged BarqDrop.exe and in a source checkout
REM  (where the app actually runs as .venv\Scripts\pythonw.exe).
REM ===================================================================
setlocal EnableExtensions EnableDelayedExpansion
title BarqDrop firewall rules
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Administrator rights are required to change firewall rules.
    echo   Right-click Allow-Firewall.bat  ^>  "Run as administrator".
    echo.
    pause
    exit /b 1
)

echo Removing any previous BarqDrop rules ...
for %%N in ("BarqDrop (in)" "BarqDrop (out)" "BarqDrop TCP (in)" "BarqDrop UDP (in)") do (
    netsh advfirewall firewall delete rule name=%%N >nul 2>&1
)

set "ADDED=0"

REM -- packaged build: the exe sits next to this script --------------------
if exist "%~dp0BarqDrop.exe" call :AllowProgram "%~dp0BarqDrop.exe" "BarqDrop.exe"

REM -- source checkout: the venv interpreters are what actually listen -----
if exist "%~dp0.venv\Scripts\pythonw.exe" call :AllowProgram "%~dp0.venv\Scripts\pythonw.exe" "pythonw.exe (.venv)"
if exist "%~dp0.venv\Scripts\python.exe"  call :AllowProgram "%~dp0.venv\Scripts\python.exe"  "python.exe (.venv)"

REM -- port rules always, so a rebuilt or moved exe keeps working ----------
echo Allowing the BarqDrop ports ...
netsh advfirewall firewall add rule name="BarqDrop TCP (in)" dir=in action=allow protocol=TCP localport=45878 enable=yes profile=private,domain >nul
netsh advfirewall firewall add rule name="BarqDrop UDP (in)" dir=in action=allow protocol=UDP localport=45877 enable=yes profile=private,domain >nul

echo.
if "%ADDED%"=="0" (
    echo   Note: no BarqDrop.exe and no .venv were found next to this script,
    echo   so only the port rules were added. That is enough in most cases.
)

REM -- the profile matters as much as the rules ----------------------------
echo Current network profiles:
powershell -NoProfile -Command "Get-NetConnectionProfile | Select-Object Name,NetworkCategory | Format-Table -AutoSize" 2>nul
powershell -NoProfile -Command "if (Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' }) { exit 1 } else { exit 0 }" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ONE MORE STEP - a network above is set to Public.
    echo   Windows blocks device-to-device traffic on Public networks, so
    echo   BarqDrop will still be unreachable until you change it:
    echo.
    echo     Settings ^> Network ^& Internet ^> Wi-Fi ^> your network
    echo     ^> Network profile type ^> Private
    echo.
    choice /c YN /m "   Set every Public network to Private now"
    if not errorlevel 2 (
        powershell -NoProfile -Command "Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' } | Set-NetConnectionProfile -NetworkCategory Private"
        echo   Done. Profiles are now:
        powershell -NoProfile -Command "Get-NetConnectionProfile | Select-Object Name,NetworkCategory | Format-Table -AutoSize"
    )
)

echo.
echo Finished. BarqDrop can now be discovered and receive files on this PC.
pause
exit /b 0

:AllowProgram
echo Allowing %~2 ...
netsh advfirewall firewall add rule name="BarqDrop (in)"  dir=in  action=allow program="%~1" enable=yes profile=private,domain >nul
netsh advfirewall firewall add rule name="BarqDrop (out)" dir=out action=allow program="%~1" enable=yes profile=private,domain >nul
set "ADDED=1"
goto :eof
