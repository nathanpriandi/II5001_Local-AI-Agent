@echo off
REM One-time setup: virtual environment, packages, database, demo data.
REM Run it again at any time to reset the demo (it keeps the installed packages).
cd /d %~dp0
if not exist venv (
  where py >nul 2>nul && (py -3 -m venv venv) || (python -m venv venv)
  if errorlevel 1 goto :error
)
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || goto :error
python manage.py migrate || goto :error
python manage.py seed_demo_store || goto :error
python manage.py evaluate_agents
echo.
echo Setup finished. Now double-click run.bat
pause
exit /b 0
:error
echo.
echo Setup failed. Read the error above.
pause
exit /b 1
