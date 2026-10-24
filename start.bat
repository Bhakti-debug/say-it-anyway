@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up Quiet Space for the first time...
    py -3.13 -m venv .venv
    if errorlevel 1 goto failed
)

echo Checking and installing the app's Python packages...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

cd frontend
if not exist "node_modules" (
    echo Setting up the React frontend for the first time...
    call npm install
    if errorlevel 1 goto failed
)
echo Preparing the React page...
call npm run build
if errorlevel 1 goto failed
cd ..

echo.
echo Quiet Space is starting. Keep this window open while you use it.
echo Open http://127.0.0.1:8000 in your browser.
echo Press Ctrl+C here to stop the app.
echo.
".venv\Scripts\python.exe" -m uvicorn app:app
goto done

:failed
echo.
echo Setup could not finish. Check that Python and Node.js are installed and that you are connected to the internet, then try again.
pause

:done
