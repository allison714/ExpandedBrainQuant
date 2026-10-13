@echo off
REM Starts the expansion-factor measurement. The programs live in the Zarr Batch Converter
REM folder beside this one, because they share its settings.
set "TOOL=%~dp0..\ZarrBatchConverter\Measure_ExF.bat"
if not exist "%TOOL%" (
    echo Cannot find the ZarrBatchConverter folder beside this one:
    echo   %TOOL%
    pause
    exit /b 1
)
call "%TOOL%" %*
