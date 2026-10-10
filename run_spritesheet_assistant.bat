@echo off
setlocal
cd /d "%~dp0"
title Depth Hammer Spritesheet Assistant

set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"

"%VENV_PYTHON%" -c "import sys; assert sys.version_info.major == 3" >nul 2>nul
if errorlevel 1 (
    if exist .venv (
        echo Preserving the unusable environment in a .venv-backup folder...
        move .venv .venv-backup-%RANDOM% >nul
        if errorlevel 1 goto :setup_failed
    )
    echo First-run setup: creating the private Python environment...
    call :create_venv
    if errorlevel 1 goto :setup_failed

    echo Installing Pillow and NumPy...
    "%VENV_PYTHON%" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :setup_failed
)

"%VENV_PYTHON%" -c "import PIL, numpy" >nul 2>nul
if errorlevel 1 (
    "%VENV_PYTHON%" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :setup_failed
)

"%VENV_PYTHON%" app.py
if errorlevel 1 (
    echo.
    echo The assistant stopped with an error.
    pause
)
exit /b

:create_venv
"%LOCALAPPDATA%\Python\bin\python.exe" -c "import sys; assert sys.version_info.major == 3" >nul 2>nul
if not errorlevel 1 (
    "%LOCALAPPDATA%\Python\bin\python.exe" -m venv .venv
    exit /b
)
py -3 -c "import sys; assert sys.version_info.major == 3" >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv .venv
    exit /b
)
python -c "import sys; assert sys.version_info.major == 3" >nul 2>nul
if not errorlevel 1 (
    python -m venv .venv
    exit /b
)
goto :no_python

:no_python
echo.
echo Python 3 was not found. Install Python 3 from python.org, then run this file again.
pause
exit /b 1

:setup_failed
echo.
echo First-run setup failed. Check your internet connection and Python installation.
echo You can delete the .venv folder and run this file again to retry.
pause
exit /b 1
