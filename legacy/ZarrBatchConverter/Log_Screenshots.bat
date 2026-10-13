@echo off
setlocal enabledelayedexpansion
title Zarr Screenshot Log

echo ============================================
echo    Zarr Screenshot Log
echo ============================================
echo  Exports the standard QC pictures for each sample, drawn from the
echo  image data with a scale bar, into one subfolder per sample, and
echo  lists them in "Zarr Screenshot Log.xlsx" with a rating and notes.
echo.
echo  What each sample gets:
echo    stack, one tile   XY, XZ and YZ projections; tilted volume; planes at 5, 25, 50, 75, 95%% depth
echo    stack, montage    XY projection; tilted volume; overviews at those depths; one seam crop
echo    flat montage      overview; five crops (centre and four corners)
echo    flat single tile  the full-resolution image
echo    200%% close-ups    stacks: one at each of the five depths; flat images: one
echo.
echo  Run it again at any time: samples that already have pictures only get
echo  the ones they are missing.
echo.

REM --- locate python ---
set "PYRUN="
where py.exe >nul 2>&1 && set "PYRUN=py.exe -3"
if not defined PYRUN where python.exe >nul 2>&1 && set "PYRUN=python.exe"
if not defined PYRUN if exist "%USERPROFILE%\anaconda3\python.exe" set "PYRUN="%USERPROFILE%\anaconda3\python.exe""
if not defined PYRUN if exist "%USERPROFILE%\miniconda3\python.exe" set "PYRUN="%USERPROFILE%\miniconda3\python.exe""
if not defined PYRUN (
    echo ERROR: Python not found. Run First_Time_Setup.bat first.
    pause
    exit /b 1
)
%PYRUN% -c "import openpyxl, numpy, zarr, PIL, scipy" >nul 2>&1
if errorlevel 1 (
    echo Installing missing Python packages, one time only...
    %PYRUN% -m pip install --user --quiet openpyxl numpy "zarr>=3" pillow scipy
)

REM Dense extras are off unless added here:
REM   --every 10   also save a plane every 10 slices of each stack
REM   --grid       also save a crop of every tile of each montage
%PYRUN% "%~dp0roi_log.py" --batch %*
set "RC=!errorlevel!"

echo.
pause
exit /b !RC!
