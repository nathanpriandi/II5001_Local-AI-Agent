@echo off
REM Start the agent worker (own window) and the shop at http://127.0.0.1:8000
REM Ollama must be running with the model pulled: ollama pull qwen3:4b
cd /d %~dp0
call venv\Scripts\activate.bat
REM The new window starts in this folder, so relative paths work even with spaces in the path
start "Agent worker" cmd /k venv\Scripts\python.exe manage.py agent_worker
echo.
echo Storefront + Shopping Agent: http://127.0.0.1:8000/agent/   (student@example.com / student12345)
echo Agent Control Center:        http://127.0.0.1:8000/dashboard/assistant/control/   (admin@example.com / admin12345)
echo Keep the "Agent worker" window open; it runs both agents in the background.
echo.
python manage.py runserver 127.0.0.1:8000
pause
