@echo off
setlocal enabledelayedexpansion
title Compare Antibody Labelling

echo ============================================
echo    Compare Antibody Labelling
echo ============================================
echo  Sets two groups of samples side by side, antibody by antibody, to
echo  see whether the labelling stayed the same or changed. The groups
echo  are told apart by what their names start with (for example SZ and
echo  AC), and paired by the antibody in the name.
echo.
echo  Per field of view it counts antibody puncta and measures how bright
echo  they are, then gives the second group as a ratio of the first with
echo  a 95%% interval. Samples with "control" in the name give the floor.
echo.
echo  Laser power and exposure are not in the files: check they matched
echo  before reading a difference as a change in the antibody.
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

REM The two groups can be typed after the .bat name:  Compare_AB.bat SZ AC
%PYRUN% "%~dp0compare_ab.py" %*
set "RC=!errorlevel!"

echo.
pause
exit /b !RC!
