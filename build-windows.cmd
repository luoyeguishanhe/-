@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Please create the project .venv and install the build dependencies first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" "packaging\build_windows.py" %*
if errorlevel 1 (
    echo Build failed. See the output above.
    pause
    exit /b 1
)
pause
