@echo off
setlocal enabledelayedexpansion
title Zarr Batch Converter

echo ============================================
echo    Zarr Batch Converter
echo ============================================
echo  Converts every image in a folder and its subfolders to OME-Zarr
echo  and updates the Excel image list with Neuroglancer links.
echo.

REM --- locate python ---
set "PYRUN="
where py.exe >nul 2>&1 && set "PYRUN=py.exe -3"
if not defined PYRUN where python.exe >nul 2>&1 && set "PYRUN=python.exe"
if not defined PYRUN if exist "%USERPROFILE%\anaconda3\python.exe" set "PYRUN="%USERPROFILE%\anaconda3\python.exe""
if not defined PYRUN (
    echo ERROR: Python not found. Install Python 3.9+ or Anaconda and run again.
    echo.
    pause
    exit /b 1
)

REM --- ensure deps ---
%PYRUN% -c "import openpyxl, numpy, zarr, scipy" >nul 2>&1
if errorlevel 1 (
    echo Installing missing Python packages, one time only...
    %PYRUN% -m pip install --user --quiet openpyxl numpy "zarr>=3" scipy
    if errorlevel 1 (
        echo ERROR: Failed to install Python packages.
        pause
        exit /b 1
    )
)

REM --- first run on this PC: set the folders ---
if not exist "%~dp0settings.ini" (
    echo This PC has not been set up yet. Starting the first-time setup.
    echo.
    %PYRUN% "%~dp0convert_to_zarr.py" --setup
    if errorlevel 1 (
        pause
        exit /b 1
    )
    echo.
)

REM --- source: drag a folder or file onto this .bat, or type/paste it ---
set "SRC=%~1"
if not defined SRC (
    echo Drag a folder, or a single image file, into this window, then press Enter.
    echo Or just press Enter to rebuild the image list without converting anything.
    set /p "SRC=Folder or file to convert: "
)
if defined SRC set "SRC=!SRC:"=!"

echo.
if not defined SRC (
    %PYRUN% "%~dp0convert_to_zarr.py" --list-only
) else (
    if not exist "!SRC!" (
        echo Not found: !SRC!
        pause
        exit /b 1
    )
    %PYRUN% "%~dp0convert_to_zarr.py" "!SRC!"
)
set "RC=!errorlevel!"

echo.
if not "!RC!"=="0" echo Finished with problems. See the messages above.
pause
exit /b !RC!
