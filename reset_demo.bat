@echo off
REM Put the shop back to the starting state before a demonstration
cd /d %~dp0
call venv\Scripts\activate.bat
python manage.py seed_demo_store
pause
