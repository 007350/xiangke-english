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
echo 正在加载模型，稍等几秒，浏览器会自动打开。用完关掉这个窗口即可。
"%PY%" -m xiangke.app --open
pause
