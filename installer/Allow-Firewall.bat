@echo off
REM ===================================================================
REM  Allow BarqDrop through Windows Firewall on private/domain networks.
REM  Right-click this file and choose "Run as administrator".
REM ===================================================================
setlocal EnableExtensions
title BarqDrop firewall rules

net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo   This script needs administrator rights.
    echo   Right-click Allow-Firewall.bat  ^>  "Run as administrator".
    echo.
    pause
    exit /b 1
)

set "APP=%~dp0BarqDrop.exe"
netsh advfirewall firewall delete rule name="BarqDrop (in)"  >nul 2>&1
netsh advfirewall firewall delete rule name="BarqDrop (out)" >nul 2>&1

if exist "%APP%" (
    echo Allowing "%APP%" ...
    netsh advfirewall firewall add rule name="BarqDrop (in)"  dir=in  action=allow program="%APP%" enable=yes profile=private,domain
    netsh advfirewall firewall add rule name="BarqDrop (out)" dir=out action=allow program="%APP%" enable=yes profile=private,domain
) else (
    echo BarqDrop.exe not found next to this script - opening the default ports instead.
    netsh advfirewall firewall add rule name="BarqDrop (in)"  dir=in action=allow protocol=TCP localport=45878 enable=yes profile=private,domain
    netsh advfirewall firewall add rule name="BarqDrop (out)" dir=in action=allow protocol=UDP localport=45877 enable=yes profile=private,domain
)

echo.
echo Done. BarqDrop can now discover and receive on private networks.
pause
endlocal
