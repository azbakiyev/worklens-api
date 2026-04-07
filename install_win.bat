@echo off
setlocal enabledelayedexpansion
title WorkLens Installer
color 0A

echo.
echo  ================================================
echo   WorkLens Installer for Windows
echo  ================================================
echo.

set INSTALL_DIR=%USERPROFILE%\WorkLens
set GITHUB_ZIP=https://github.com/azbakiyev/worklens/archive/refs/heads/main.zip

echo  Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo  Python not found. Opening download page...
    start https://www.python.org/downloads/
    echo  Please install Python 3.10+ then run this installer again.
    pause
    exit /b 1
)
echo  Python found.

echo.
echo  Downloading WorkLens...
powershell -Command "Invoke-WebRequest -Uri '%GITHUB_ZIP%' -OutFile '%TEMP%\worklens.zip' -UseBasicParsing"
powershell -Command "Expand-Archive -Path '%TEMP%\worklens.zip' -DestinationPath '%TEMP%\worklens_install' -Force"

if exist "%INSTALL_DIR%" rmdir /s /q "%INSTALL_DIR%"
move "%TEMP%\worklens_install\worklens-main" "%INSTALL_DIR%"
del "%TEMP%\worklens.zip"

echo  Installed to: %INSTALL_DIR%

echo.
echo  Installing dependencies...
cd "%INSTALL_DIR%"
python -m venv .venv
.venv\Scripts\pip install -q -r requirements.txt
echo  Dependencies installed.

echo.
echo  Creating desktop shortcut...
set SHORTCUT=%USERPROFILE%\Desktop\WorkLens.bat
(
echo @echo off
echo cd "%INSTALL_DIR%"
echo start /B .venv\Scripts\python main.py
echo timeout /t 4 /nobreak ^>nul
echo start http://localhost:7771
) > "%SHORTCUT%"

echo.
echo  ================================================
echo   WorkLens installed successfully!
echo  ================================================
echo.
echo  Starting WorkLens...
start /B .venv\Scripts\python main.py
timeout /t 4 /nobreak >nul
start http://localhost:7771
echo.
echo  WorkLens is running at http://localhost:7771
echo  Desktop shortcut: WorkLens.bat
echo.
pause
