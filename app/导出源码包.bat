@echo off
rem Sentinel source-package exporter (run on the DEV machine).
rem Builds a clean zip for teammates: excludes .venv/node_modules/.env/logs,
rem optionally embeds a live DB snapshot the recipient can import.
cd /d "%~dp0"
".venv\Scripts\python.exe" -c "print('VENV_OK')" 2>nul | findstr /C:"VENV_OK" >nul
if not errorlevel 1 goto run_venv
py -3.13 -c "print('PYSETUP_OK')" 2>nul | findstr /C:"PYSETUP_OK" >nul
if not errorlevel 1 set "PYCMD=py -3.13"
if defined PYCMD goto run_sys
py -3 -c "print('PYSETUP_OK')" 2>nul | findstr /C:"PYSETUP_OK" >nul
if not errorlevel 1 set "PYCMD=py -3"
if defined PYCMD goto run_sys
python -c "print('PYSETUP_OK')" 2>nul | findstr /C:"PYSETUP_OK" >nul
if not errorlevel 1 set "PYCMD=python"
if defined PYCMD goto run_sys
echo.
echo  [X] No Python found (.venv or system) - nothing to run.
pause
exit /b 1

:run_venv
".venv\Scripts\python.exe" "export_package.py" %*
if errorlevel 1 pause
exit /b

:run_sys
%PYCMD% "export_package.py" %*
if errorlevel 1 pause
exit /b
