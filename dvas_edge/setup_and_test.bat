@echo off
REM ============================================================
REM setup_and_test.bat
REM Kerala Police Drone Video Analytics — Quick Start (Windows)
REM ============================================================
REM Run this from the project root directory.
REM It creates a venv, installs dependencies, and runs the tests.

SET VENV_DIR=.venv

echo.
echo [1/4] Creating virtual environment in %VENV_DIR% ...
python -m venv %VENV_DIR%
IF ERRORLEVEL 1 (
    echo ERROR: Failed to create venv. Make sure Python 3.8+ is installed.
    exit /b 1
)

echo.
echo [2/4] Activating venv and upgrading pip ...
CALL %VENV_DIR%\Scripts\activate.bat
python -m pip install --upgrade pip --quiet

echo.
echo [3/4] Installing project dependencies ...
pip install -r requirements.txt
IF ERRORLEVEL 1 (
    echo ERROR: pip install failed.
    exit /b 1
)

echo.
echo [4/4] Running test suite ...
python -m pytest tests/ -v --timeout=30 --tb=short
IF ERRORLEVEL 1 (
    echo.
    echo WARNING: Some tests failed. See output above.
) ELSE (
    echo.
    echo All tests passed!
)

echo.
echo To start the edge layer:
echo   %VENV_DIR%\Scripts\activate.bat
echo   python main.py
echo.
echo Prometheus metrics: http://localhost:8000/metrics
