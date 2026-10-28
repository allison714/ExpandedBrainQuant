@echo off
rem Builds the Synapse Review page (phone-friendly Y/N review of every logged synapse) and puts a
rem password-protected copy on the lab server beside the gallery. Export the CSV from the page when
rem you stop reviewing, then run:  python review_import.py <that csv>  and Measure_ExF.bat.
cd /d "%~dp0"
python synapse_review.py --password %*
echo.
pause
