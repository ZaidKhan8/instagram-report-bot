@echo off
cd /d "%~dp0"
echo.
where python >nul 2>nul && (python shuru.py %* & goto :end)
where py >nul 2>nul && (py shuru.py %* & goto :end)
echo PYTHON NAHI MILA. Ye 2 minute ka kaam hai:
echo   1. python.org/downloads kholiye
echo   2. Python download kariye aur install kariye
echo   3. Install karte waqt "Add Python to PATH" wala box TICK kariye
echo   4. Phir is file ko dobara double-click kariye
:end
echo.
pause
