@echo off
title ArcSlicer — First Time Setup
color 0B

echo.
echo  ================================================
echo    ArcSlicer - First Time Setup
echo    Arc Overhang Post-Processor for Qidi Q1 Pro
echo  ================================================
echo.

:: ── Check Python ────────────────────────────────────────────────────────
echo [1/4] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Python not found!
    echo.
    echo  Please install Python 3.10 or newer from:
    echo    https://www.python.org/downloads/
    echo.
    echo  IMPORTANT: During installation, check the box that says
    echo    "Add Python to PATH"  -- otherwise this won't work!
    echo.
    pause
    exit /b 1
)
python --version
echo  [OK] Python found.
echo.

:: ── Install dependencies ────────────────────────────────────────────────
echo [2/4] Installing required Python packages...
echo  (shapely, numpy, matplotlib, numpy-hilbert-curve)
echo.
python -m pip install --upgrade pip >nul 2>&1
python -m pip install shapely numpy matplotlib numpy-hilbert-curve

if %errorlevel% neq 0 (
    echo.
    echo  WARNING: Some packages may have failed to install.
    echo  Try running this as Administrator if you see permission errors.
) else (
    echo.
    echo  [OK] Packages installed.
)
echo.

:: ── Download arc overhang script ────────────────────────────────────────
echo [3/4] Downloading arc overhang script from GitHub...
echo.

set SCRIPT_URL=https://raw.githubusercontent.com/Wasupmacuz/arc-overhang-prusaslicer-integration/main/prusa_slicer_post_processing_script.py
set SCRIPT_OUT=%~dp0prusa_slicer_post_processing_script.py

:: Try PowerShell (available on all modern Windows)
powershell -Command "Invoke-WebRequest -Uri '%SCRIPT_URL%' -OutFile '%SCRIPT_OUT%' -UseBasicParsing" >nul 2>&1

if exist "%SCRIPT_OUT%" (
    echo  [OK] Arc script downloaded: prusa_slicer_post_processing_script.py
) else (
    echo  WARNING: Could not auto-download the script.
    echo.
    echo  Please download it manually:
    echo    1. Open: https://github.com/Wasupmacuz/arc-overhang-prusaslicer-integration
    echo    2. Click on  prusa_slicer_post_processing_script.py
    echo    3. Click "Raw", then save the file (Ctrl+S) into THIS folder:
    echo       %~dp0
    echo.
)
echo.

:: ── Create shortcuts info ────────────────────────────────────────────────
echo [4/4] Setup complete!
echo.
echo  ================================================
echo    HOW TO USE ArcSlicer
echo  ================================================
echo.
echo  1. In OrcaSlicer, enable these settings:
echo       Printer Settings ^> Use relative E distances  [ON]
echo       Print Settings ^> Detect overhangs            [ON]
echo       Slice your model and EXPORT the G-code
echo       (File ^> Export ^> Export G-code)
echo.
echo  2. Drop the exported .gcode file into:
echo       %~dp0input\
echo.
echo  3. Double-click  ArcSlicer.bat  to open the UI
echo.
echo  4. Browse to your file, adjust settings if needed,
echo     then click  PROCESS G-CODE
echo.
echo  5. Your processed file will appear in:
echo       %~dp0output\
echo     Ready to send to your Qidi Q1 Pro!
echo.
echo  ================================================
echo.
pause
