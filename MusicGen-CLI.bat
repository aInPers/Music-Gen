@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem CLI entry, same as: python -m musicgen ...
rem e.g.  MusicGen-CLI.bat render examples\demo\demo.musicproj -o out.wav
py -3 -c "import numpy" 2>nul
if errorlevel 1 goto fallback
py -3 -m musicgen %*
exit /b %errorlevel%
:fallback
python -m musicgen %*
exit /b %errorlevel%
