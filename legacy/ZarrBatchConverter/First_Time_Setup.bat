@echo off
setlocal enabledelayedexpansion
title Zarr Batch Converter - First Time Setup

echo ============================================
echo    Zarr Batch Converter - First Time Setup
echo ============================================
echo  Checks that this PC has what the converter needs and saves
echo  your folders to settings.ini. Safe to run again at any time.
echo.

REM --- locate python ---
set "PYRUN="
where py.exe >nul 2>&1 && set "PYRUN=py.exe -3"
if not defined PYRUN where python.exe >nul 2>&1 && set "PYRUN=python.exe"
if not defined PYRUN if exist "%USERPROFILE%\anaconda3\python.exe" set "PYRUN="%USERPROFILE%\anaconda3\python.exe""
if not defined PYRUN if exist "%USERPROFILE%\miniconda3\python.exe" set "PYRUN="%USERPROFILE%\miniconda3\python.exe""
if not defined PYRUN (
    echo Python      : NOT FOUND
    echo.
    echo   Install Python 3.9 or newer from https://www.python.org/downloads/
    echo   and tick "Add python.exe to PATH" in the installer. Anaconda also works.
    echo   Then run this setup again.
    echo.
    pause
    exit /b 1
)

REM --- python packages ---
%PYRUN% -c "import openpyxl, numpy, zarr, PIL, scipy" >nul 2>&1
if errorlevel 1 (
    echo Packages    : installing openpyxl, numpy, zarr, pillow and scipy. This takes a minute...
    %PYRUN% -m pip install --user --quiet openpyxl numpy "zarr>=3" pillow scipy
    if errorlevel 1 (
        echo.
        echo ERROR: The packages could not be installed. Check the internet connection,
        echo then run this setup again.
        pause
        exit /b 1
    )
)
echo Packages    : OK

%PYRUN% "%~dp0convert_to_zarr.py" --setup
set "RC=!errorlevel!"

echo.
pause
exit /b !RC!
