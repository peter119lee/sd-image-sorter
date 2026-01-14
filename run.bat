@echo off
echo ==========================================
echo    SD Image Sorter - Starting...
echo ==========================================
echo.

cd /d "%~dp0"

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.9+ from https://python.org
    pause
    exit /b 1
)

REM Check if dependencies are installed
if not exist "backend\venv" (
    echo First run detected. Setting up virtual environment...
    echo.
    python -m venv backend\venv
    call backend\venv\Scripts\activate.bat
    pip install -r backend\requirements.txt
) else (
    call backend\venv\Scripts\activate.bat
)

echo.
echo Starting server...
echo.
echo ========================================
echo   Open your browser to:
echo   http://localhost:8000
echo ========================================
echo.
echo Press Ctrl+C to stop the server.
echo.

cd backend
python main.py

pause
