@echo off
setlocal
cd /d "%~dp0"
title LifeGuard - Build Windows App
if not exist ".venv\Scripts\python.exe" call INSTALL_WINDOWS.bat
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pyinstaller
if errorlevel 1 goto failed
pyinstaller --noconfirm --clean LifeGuard.spec
if errorlevel 1 goto failed
echo Build complete: dist\LifeGuard\LifeGuard.exe
echo Distribute the entire dist\LifeGuard folder.
pause
exit /b 0
:failed
echo Build failed. Review the error above.
pause
exit /b 1
