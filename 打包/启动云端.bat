@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "xiangke\cloud.py" cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)
if not defined AI_API_KEY (
  echo 没有设置星火令牌 AI_API_KEY。疑惑会保存并等待答疑，配置后请重启云端。
)
echo 云端启动后，老师和家长的页面在 http://127.0.0.1:8766
"%PY%" -m xiangke.cloud
pause
