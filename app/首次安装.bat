@echo off
rem Sentinel first-time setup launcher (for a NEW machine).
rem Double-click AFTER installing Python 3.13 + Docker Desktop (+ Node.js optional).
rem Steps: .env -> venv -> pip deps -> npm deps -> docker containers -> snapshot.
cd /d "%~dp0"
py -3.13 -c "print('PYSETUP_OK')" 2>nul | findstr /C:"PYSETUP_OK" >nul
if not errorlevel 1 set "PYCMD=py -3.13"
if defined PYCMD goto run_setup
py -3 -c "print('PYSETUP_OK')" 2>nul | findstr /C:"PYSETUP_OK" >nul
if not errorlevel 1 set "PYCMD=py -3"
if defined PYCMD goto run_setup
python -c "print('PYSETUP_OK')" 2>nul | findstr /C:"PYSETUP_OK" >nul
if not errorlevel 1 set "PYCMD=python"
if defined PYCMD goto run_setup
goto no_python

:run_setup
%PYCMD% "setup_dev.py" %*
if errorlevel 1 pause
exit /b

:no_python
echo.
echo  [X] Python not found on this machine.
echo      Please install Python 3.13 first  (check "Add python.exe to PATH"):
echo      https://www.python.org/downloads/
echo      Docker Desktop is also required. Node.js is optional.
echo.
echo      Then double-click this file again.
pause
exit /b 1
