@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo 正在启动 MySQL（端口 3306）...
start "MySQL" /min "mysql\bin\mysqld.exe" --defaults-file="%CD%\mysql\my.ini" --console
timeout /t 5 >nul
echo 若已启动，接着运行 scripts\start-server.bat
