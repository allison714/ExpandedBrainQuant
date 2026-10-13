@echo off
setlocal enabledelayedexpansion
title Save Neuroglancer View

echo ============================================
echo    Save Neuroglancer View
echo ============================================
echo  Takes the view you have open in Neuroglancer and asks whether to
echo    1. make it your default link for that image, in your notes workbook
echo    2. log it as an ROI: a crop, a rating and notes in the screenshot folder
echo  You can say yes to either, both or neither.
echo.
echo  Before pressing a key:
echo    1. In the browser, set up the view the way you want it.
echo    2. Click the address bar, press Ctrl+A, then Ctrl+C.
echo.
pause
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

%PYRUN% "%~dp0roi_log.py" --save-view
set "RC=!errorlevel!"

echo.
pause
exit /b !RC!
