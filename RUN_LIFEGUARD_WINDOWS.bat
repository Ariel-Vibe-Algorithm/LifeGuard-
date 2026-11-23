@echo off
setlocal
cd /d "%~dp0"
title LifeGuard
if exist ".venv\Scripts\pythonw.exe" (
  ".venv\Scripts\pythonw.exe" main.py
  if errorlevel 1 goto error
  goto end
)
where py >nul 2>nul
if %errorlevel%==0 (py -3.11 main.py) else (python main.py)
if errorlevel 1 goto error
goto end
:error
echo LifeGuard could not start. Run INSTALL_WINDOWS.bat first.
pause
:end
endlocal
