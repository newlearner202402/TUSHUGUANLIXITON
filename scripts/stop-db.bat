@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo 正在停止 MySQL...
"mysql\bin\mysqladmin.exe" -u root --password=library123 shutdown
echo 完成
timeout /t 2 >nul
