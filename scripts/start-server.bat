@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo 服务端启动中： http://127.0.0.1:8000
echo 客户端入口： /teacher/  /student/  教师注册： /register.html
echo 同时监听 IPv4 与 IPv6，局域网/IPv6 可用 http://[本机IPv6地址]:8000 访问
echo 按 Ctrl+C 停止
echo.
".venv\Scripts\python.exe" run_server.py
