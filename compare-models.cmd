@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment missing. Follow README.md first.
    pause
    exit /b 1
)
if not exist "benchmark.local.json" (
    echo benchmark.local.json missing. See docs and benchmark.example.json.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 -m subtitle_app.benchmark %*
set "BENCHMARK_EXIT_CODE=%ERRORLEVEL%"
if not "%BENCHMARK_EXIT_CODE%"=="0" echo Comparison failed. Read the message above.
pause
exit /b %BENCHMARK_EXIT_CODE%
