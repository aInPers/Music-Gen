@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Launch the GUI without a console window.
rem Prefer py -3 (has tkinter on this machine), fall back to python.
py -3 -c "import tkinter, numpy, soundfile" 2>nul
if errorlevel 1 goto fallback
start "" pyw -3 -m musicgen gui %*
exit /b 0
:fallback
python -c "import tkinter, numpy, soundfile" 2>nul
if errorlevel 1 goto nopython
start "" pythonw -m musicgen gui %*
exit /b 0
:nopython
echo [ERROR] No Python 3 with tkinter/numpy/soundfile found.
echo Run:  py -3 -m pip install -r requirements.txt
pause
exit /b 1
