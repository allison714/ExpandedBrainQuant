@echo off
setlocal enabledelayedexpansion
title Re-measure hand-placed lines

echo ============================================
echo    Re-measure hand-placed lines
echo ============================================
echo  For every synapse where you placed the measurement line yourself on the
echo  Synapse Review page (Fix line), reads the pan stain along your line the
echo  same way as the automatic reading and writes the peak-to-peak distance
echo  and its expansion factor into the Synapse Log. Import the page's CSV
echo  with review_import.py first. A check picture per synapse goes to
echo  Zarr Synapse ExF\<sample>\manual_lines, and the agreement with the
echo  automatic reading to Zarr Synapse ExF\Manual vs automatic.csv.
echo  Close Synapse Log.xlsx in Excel before running.
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

%PYRUN% "%~dp0remeasure_lines.py" %*
echo.
pause
