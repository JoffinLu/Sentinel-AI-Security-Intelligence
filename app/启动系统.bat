@echo off
rem Sentinel one-click dev launcher (wrapper).
rem Double-click this file to start: Docker -^> backend :8000 -^> frontend :5173
rem All logic lives in start_dev.py (same folder). Extra args are passed through.
cd /d "%~dp0"
".venv\Scripts\python.exe" -c "print('VENV_OK')" 2>nul | findstr /C:"VENV_OK" >nul
if errorlevel 1 goto no_venv
".venv\Scripts\python.exe" "start_dev.py" %*
if errorlevel 1 pause
exit /b

:no_venv
echo.
echo  [X] Python venv missing or unusable on this machine.
echo      (The bundled .venv is bound to the original developer PC.)
echo      First-time setup on a new machine:
echo.
echo        1) Install Python 3.13     https://www.python.org/
echo        2) In this folder run:     python -m venv .venv
echo        3) Then run:               .venv\Scripts\pip.exe install -r requirements.txt
echo        4) Double-click this file again
echo.
echo      Docker Desktop is also required. See README.md for details.
pause
exit /b 1
