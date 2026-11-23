@echo off
setlocal
cd /d "%~dp0"
title LifeGuard Server
rem Local LifeGuard backend (alerts, live location, escalation, community).
rem For other computers on the network: set LIFEGUARD_SERVER_HOST=0.0.0.0 and configure TLS
rem (LIFEGUARD_TLS_CERT / LIFEGUARD_TLS_KEY). Clients refuse plain http to non-local servers.
if not exist ".venv\Scripts\python.exe" call INSTALL_WINDOWS.bat
".venv\Scripts\python.exe" -m server.lifeguard_server
pause
