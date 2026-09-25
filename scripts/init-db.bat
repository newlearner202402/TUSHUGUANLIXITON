@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo 正在建库、建表并写入示例数据...
".venv\Scripts\python.exe" -m server.init_db
echo.
pause
