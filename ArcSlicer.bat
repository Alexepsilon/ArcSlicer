@echo off
title ArcSlicer
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

:: Try to launch with pythonw (no console window)
:: If it fails (syntax error, missing import, etc.) fall back to python so you can see the error
pythonw arc_ui.py
if %errorlevel% neq 0 (
    echo.
    echo  UI failed to start. Launching with visible console for error details...
    echo.
    python arc_ui.py
    pause
)
