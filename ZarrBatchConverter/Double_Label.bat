@echo off
setlocal enabledelayedexpansion
title Double Label: Bleed-through and Efficiency

echo ============================================
echo    Double Label: Bleed-through and Efficiency
echo ============================================
echo  For every sample with two antibody channels: does one channel show up
echo  in the other (bleed-through), and how much of the synapses confirmed
echo  by one antibody does the other label (efficiency)?
echo.
echo  Run Measure_ExF.bat first: the efficiency uses the synapses it found.
echo  Results go to "Zarr Comparisons\Double Label".
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

REM Text typed after the .bat name limits it to the samples whose names contain it:
REM   Double_Label.bat 260616
%PYRUN% "%~dp0double_label.py" %*
set "RC=!errorlevel!"

echo.
pause
exit /b !RC!
