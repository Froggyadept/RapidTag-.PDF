@echo off
cd /d "%~dp0"
if not exist "%~dp0app.py" (
    echo [ERROR] app.py not found in "%~dp0"
    pause
    exit /b 1
)
python -m streamlit run "%~dp0app.py" --server.address=127.0.0.1 --server.port=8501
pause
