@echo off
REM ===================================================================
REM  BarqDrop - per-user install from the portable folder.
REM  Copies the app to %LOCALAPPDATA%\Programs\BarqDrop and creates
REM  Start Menu + desktop shortcuts. No admin rights required.
REM  (Uninstall.bat is written next to the installed app.)
REM ===================================================================
setlocal EnableExtensions
title Install BarqDrop
cd /d "%~dp0"

set "TARGET=%LOCALAPPDATA%\Programs\BarqDrop"
if not exist "BarqDrop.exe" (
    echo This script must sit next to BarqDrop.exe.
    pause
    exit /b 1
)

echo Installing BarqDrop to "%TARGET%" ...
if exist "%TARGET%" (
    taskkill /f /im BarqDrop.exe >nul 2>&1
    timeout /t 1 /nobreak >nul
)
robocopy "%~dp0." "%TARGET%" /e /njh /njs /ndl /nfl /np >nul
if errorlevel 8 (
    echo Copy failed.
    pause
    exit /b 1
)

set "SM=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$w = New-Object -ComObject WScript.Shell;" ^
  "foreach ($p in @('%SM%\BarqDrop.lnk', [Environment]::GetFolderPath('Desktop') + '\BarqDrop.lnk')) {" ^
  "  $s = $w.CreateShortcut($p); $s.TargetPath = '%TARGET%\BarqDrop.exe';" ^
  "  $s.WorkingDirectory = '%TARGET%'; $s.IconLocation = '%TARGET%\BarqDrop.exe,0';" ^
  "  $s.Description = 'BarqDrop - fast local file transfer'; $s.Save() }"

> "%TARGET%\Uninstall.bat" echo @echo off
>> "%TARGET%\Uninstall.bat" echo taskkill /f /im BarqDrop.exe ^>nul 2^>^&1
>> "%TARGET%\Uninstall.bat" echo del "%SM%\BarqDrop.lnk" ^>nul 2^>^&1
>> "%TARGET%\Uninstall.bat" echo del "%%USERPROFILE%%\Desktop\BarqDrop.lnk" ^>nul 2^>^&1
>> "%TARGET%\Uninstall.bat" echo netsh advfirewall firewall delete rule name="BarqDrop (in)" ^>nul 2^>^&1
>> "%TARGET%\Uninstall.bat" echo netsh advfirewall firewall delete rule name="BarqDrop (out)" ^>nul 2^>^&1
>> "%TARGET%\Uninstall.bat" echo echo BarqDrop shortcuts removed. Delete this folder to finish.
>> "%TARGET%\Uninstall.bat" echo pause

echo.
echo Installed. Shortcuts created in the Start Menu and on the desktop.
echo.
echo If Windows Firewall blocks discovery, right-click Allow-Firewall.bat
echo in "%TARGET%" and choose "Run as administrator".
echo.
start "" "%TARGET%\BarqDrop.exe"
pause
endlocal
