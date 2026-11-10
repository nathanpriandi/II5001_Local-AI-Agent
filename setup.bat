@echo off
REM One-time setup: virtual environment, packages, database, demo data
cd /d %~dp0
python -m venv venv || goto :error
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || goto :error
python manage.py migrate || goto :error
python manage.py seed_demo_store || goto :error
echo.
echo Setup finished. Now double-click run.bat
pause
exit /b 0
:error
echo.
echo Setup failed. Read the error above.
pause
exit /b 1
