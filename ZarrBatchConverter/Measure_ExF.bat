@echo off
setlocal enabledelayedexpansion
title Expansion Factor from Synapses

echo ============================================
echo    Expansion Factor from Synapses
echo ============================================
echo  Finds synapses in the pan stain, measures the distance between the
echo  presynaptic dense projections and the postsynaptic density, and
echo  divides it by 82 nm (M'Saad, Cairns et al. 2025) to get the
echo  expansion factor. Synapses carrying antibody are listed first.
echo.
echo  You get "Synapse Log.xlsx" and a picture of each synapse. Type Y or N
echo  in the log's Accept column, then run this again: the Summary sheet
echo  then gives the expansion factor from the synapses you accepted.
echo.
echo  Left out: samples with SZ or "control" in the name, and repeats
echo  named number_number (Sample1_2), unless the number ends an antibody's
echo  name (Homer1_2) or a grid size (3x3_1, the 2D overview).
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
REM   GluA2              only the samples whose names contain this text
REM   --max-planes 50    use up to this many planes of a stack (default 12)
REM   --synaptic y       answer "is it a synaptic antibody" in advance (y or n)
REM   --no-links         do not save Neuroglancer views of the synapses on the server
%PYRUN% "%~dp0synapse_exf.py" %*
set "RC=!errorlevel!"

echo.
pause
exit /b !RC!
