@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo 服务端启动中： http://127.0.0.1:8000
echo 客户端入口： /teacher/  /student/  教师注册： /register.html
echo 按 Ctrl+C 停止
echo.
".venv\Scripts\python.exe" -m uvicorn server.main:app --host 127.0.0.1 --port 8000
