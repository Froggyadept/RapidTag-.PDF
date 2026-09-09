@echo off
cd /d "%~dp0"
echo ============================================
echo   RapidTag - installing dependencies
echo   Folder: %cd%
echo ============================================
echo.

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo ============================================
echo   Done! Now run start.bat
echo ============================================
pause
