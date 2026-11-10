@echo off
REM Start the shop at http://127.0.0.1:8000 (Ollama must be running)
cd /d %~dp0
call venv\Scripts\activate.bat
echo Shop:      http://127.0.0.1:8000
echo Assistant: http://127.0.0.1:8000/dashboard/assistant/  (login admin@example.com / admin12345)
python manage.py runserver 127.0.0.1:8000
pause
