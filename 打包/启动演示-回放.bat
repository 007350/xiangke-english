@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)
"%PY%" -c "import rapidocr" 2>nul
if errorlevel 1 (
  echo 还没有安装依赖，请先运行：pip install -r requirements.txt
  pause
  exit /b 1
)
"%PY%" -m xiangke.download_model
if errorlevel 1 (
  pause
  exit /b 1
)
rem 回放使用新的临时数据库，与日常使用的记录隔离。
set "REPLAY_DB=%TEMP%\xiangke-replay-%RANDOM%-%RANDOM%"
start "乡课英语 云端（回放）" "%PY%" -m xiangke.cloud --db "%REPLAY_DB%\cloud.db" --replay 演示\星火录制.json
echo 这是演示备份：云端用提前录好的星火内容，不联网，页面会标明。
echo 操作步骤见“演示\演示步骤.md”。老师和家长的页面在 http://127.0.0.1:8766
echo 正在加载模型，稍等几秒，浏览器会自动打开。用完关掉这两个窗口即可。
"%PY%" -m xiangke.app --db "%REPLAY_DB%\phone.db" --open
pause
