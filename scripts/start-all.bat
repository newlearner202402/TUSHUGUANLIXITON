@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo === 1/3 启动 MySQL ===
call "scripts\start-db.bat"
echo.
echo === 2/3 启动服务端 ===
start "图书管理系统-服务端" "scripts\start-server.bat"
timeout /t 4 >nul
echo.
echo === 3/3 打开浏览器 ===
start "" http://127.0.0.1:8000/
echo.
echo 已就绪。教师端 /teacher/ ，学生端 /student/
timeout /t 3 >nul
