@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)
if not defined AI_API_KEY (
  echo 没有设置星火的令牌 AI_API_KEY。这样云端只收记录，不写新讲法。
  echo 没有令牌也想看完整流程，请用“启动演示-回放.bat”。
)
echo 云端启动后，老师和家长的页面在 http://127.0.0.1:8766
"%PY%" -m xiangke.cloud
pause
