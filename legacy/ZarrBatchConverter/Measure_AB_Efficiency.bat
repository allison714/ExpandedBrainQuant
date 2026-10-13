@echo off
setlocal enabledelayedexpansion
title Antibody Labelling Efficiency

echo ============================================
echo    Antibody Labelling Efficiency
echo ============================================
echo  Measures how well each antibody channel covers the structure shown
echo  by the pan stain, for the images in the zarr folder, and adds a row
echo  per image and antibody to "AB Efficiency Summary.csv".
echo.
echo  What each sample gets:
echo    every sample      correlation, Manders, coverage, signal-to-background,
echo                      signal-to-noise, and a fidelity map (PNG)
echo    stacks            antibody penetration through the gel, with a plot
echo    montages          tile seam and illumination checks, with a plot
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
%PYRUN% -c "import openpyxl, numpy, zarr, dask.array, skimage, scipy, matplotlib" >nul 2>&1
if errorlevel 1 (
    echo Installing missing Python packages, one time only...
    REM numpy and contourpy are pinned so pip does not swap in an old numpy, which breaks zarr
    %PYRUN% -m pip install --user --quiet openpyxl "numpy>=2" "zarr>=3" "dask[array]" scikit-image scipy matplotlib "contourpy>=1.3"
)

REM Options can be added here or typed after the .bat name in a command prompt:
REM   --level 1          measure on a coarser pyramid level (faster; 0 = full resolution)
REM   --threshold li     Li's threshold for the masks instead of Otsu's
REM   --radius-um 8      rolling-ball radius, micrometres as imaged
REM   --pan 0            use channel 0 as the pan stain instead of detecting it
REM   --redo             measure samples already in the summary again
REM   --napari NAME      open that sample with its structure mask (needs: pip install "napari[all]")
%PYRUN% "%~dp0ab_efficiency.py" %*
set "RC=!errorlevel!"

echo.
pause
exit /b !RC!
