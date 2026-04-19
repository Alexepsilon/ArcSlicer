@echo off
title ArcSlicer

:: Run from the folder where this .bat file lives
cd /d "%~dp0"

:: Check that the arc script is present
if not exist "prusa_slicer_post_processing_script.py" (
    echo.
    echo  Arc script not found!
    echo  Please run setup.bat first.
    echo.
    pause
    exit /b 1
)

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  Python not found! Please run setup.bat first.
    echo.
    pause
    exit /b 1
)

:: Launch the UI (hide the console window after launch)
start "" /B pythonw arc_ui.py 2>nul
if %errorlevel% neq 0 (
    :: pythonw not available, fall back to python (shows a console)
    python arc_ui.py
)
