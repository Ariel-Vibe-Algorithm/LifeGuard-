@echo off
setlocal
cd /d "%~dp0"
title LifeGuard - Setup
where py >nul 2>nul
if errorlevel 1 (echo Install Python 3.11 x64 from python.org and enable Add Python to PATH.& pause& exit /b 1)
if not exist ".venv\Scripts\python.exe" py -3.11 -m venv .venv
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip "setuptools>=84.1.0"
if errorlevel 1 goto failed
pip install -r requirements-windows.txt
if errorlevel 1 goto failed
rem On-device Hebrew speech model (~72 MB, CC-BY-4.0, checksum-verified). Voice commands stay offline.
python speech_local.py --download he
if errorlevel 1 echo WARNING: speech model download failed - voice commands disabled until: python speech_local.py --download he
echo Setup complete.
echo Double-click RUN_LIFEGUARD_WINDOWS.bat to start.
pause
exit /b 0
:failed
echo Installation failed. Verify Python 3.11 x64 and review the error above.
pause
exit /b 1
