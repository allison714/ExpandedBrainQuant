@echo off
setlocal enabledelayedexpansion
title 2026 AC Gallery - password protected copy

echo ============================================
echo    2026 AC Gallery, password protected
echo ============================================
echo  Builds the gallery as ONE file on the lab server, locked with a password
echo  you choose now, and puts its web address on the clipboard to share.
echo  Anyone with the address sees only a password box; with the password
echo  the full gallery opens in their browser (thumbnails, not full-size images).
echo.
echo  The open copy of the gallery folders on the server is NOT updated by this:
echo  it is not protected. See QUICKSTART, 'Sharing the gallery'.
echo.

REM --- locate python ---
set "PYRUN="
where py.exe >nul 2>&1 && set "PYRUN=py.exe -3"
if not defined PYRUN where python.exe >nul 2>&1 && set "PYRUN=python.exe"
if not defined PYRUN if exist "%USERPROFILE%naconda3\python.exe" set "PYRUN="%USERPROFILE%naconda3\python.exe""
if not defined PYRUN if exist "%USERPROFILE%\miniconda3\python.exe" set "PYRUN="%USERPROFILE%\miniconda3\python.exe""
if not defined PYRUN (
    echo ERROR: Python not found. Run First_Time_Setup.bat first.
    pause
    exit /b 1
)
%PYRUN% -c "import cryptography" >nul 2>&1
if errorlevel 1 %PYRUN% -m pip install --user --quiet cryptography

%PYRUN% "%~dp0make_gallery.py" --server --password
set "RC=!errorlevel!"

echo.
pause
exit /b !RC!
