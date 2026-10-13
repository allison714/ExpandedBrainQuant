@echo off
setlocal enabledelayedexpansion
title NG Link Generator

echo ============================================
echo    Neuroglancer Link Generator
echo ============================================
echo.

REM --- locate python ---
set "PYEXE="
for %%P in (py.exe python.exe python3.exe) do (
    if not defined PYEXE (
        where %%P >nul 2>&1 && set "PYEXE=%%P"
    )
)
if not defined PYEXE (
    echo ERROR: Python not found on PATH.
    echo Install Python 3.9+ from https://www.python.org/downloads/
    echo and ensure "Add python.exe to PATH" was checked.
    echo.
    pause
    exit /b 1
)

REM py.exe wants a launcher arg; python.exe doesn't
set "PYRUN=%PYEXE%"
if /i "%PYEXE%"=="py.exe" set "PYRUN=py.exe -3"

REM --- ensure deps ---
echo Checking Python dependencies...
%PYRUN% -c "import openpyxl, numpy, zarr, fsspec, aiohttp" >nul 2>&1
if errorlevel 1 (
    echo Installing missing packages (one-time)...
    %PYRUN% -m pip install --user --quiet openpyxl numpy "zarr>=3" "fsspec[http]" aiohttp
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo.
echo ------------------------------------------------
echo Input mode:
echo   [1] HTTP URL to a folder containing .ome.zarr subdirs (auto-discover)
echo   [2] Path to a text file with one zarr URL per line
echo ------------------------------------------------
set "MODE="
set /p MODE=Enter 1 or 2:

if "!MODE!"=="1" (
    set /p SRC=Enter folder URL (ending in /):
    set "SRCFLAG=--folder-url"
) else if "!MODE!"=="2" (
    set /p SRC=Enter path to URL list file:
    set "SRCFLAG=--url-file"
) else (
    echo Invalid choice.
    pause
    exit /b 1
)

if "!SRC!"=="" (
    echo No input provided.
    pause
    exit /b 1
)

echo.
echo ------------------------------------------------
echo Output:
echo   [1] Pick a specific .xlsx file path
echo   [2] Pick a folder (file will be named ng_links.xlsx)
echo ------------------------------------------------
set "OMODE="
set /p OMODE=Enter 1 or 2:

if "!OMODE!"=="1" (
    set /p OUT=Enter output .xlsx path:
) else if "!OMODE!"=="2" (
    set /p ODIR=Enter output folder path:
    if "!ODIR!"=="" (
        echo No folder provided.
        pause
        exit /b 1
    )
    if not exist "!ODIR!" (
        echo Folder does not exist: !ODIR!
        pause
        exit /b 1
    )
    set "OUT=!ODIR!\ng_links.xlsx"
) else (
    echo Invalid choice.
    pause
    exit /b 1
)

if "!OUT!"=="" (
    echo No output path provided.
    pause
    exit /b 1
)

echo.
echo Running: !SRCFLAG! "!SRC!" --output "!OUT!"
echo.
%PYRUN% "%~dp0generate_ng_links.py" !SRCFLAG! "!SRC!" --output "!OUT!"
if errorlevel 1 (
    echo.
    echo ERROR: Generator failed. See messages above.
    pause
    exit /b 1
)

echo.
echo Done. Output: !OUT!
echo.
pause
