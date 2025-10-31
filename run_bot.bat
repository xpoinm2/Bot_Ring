@echo off
REM Ensure the script works relative to its own directory
cd /d "%~dp0"

REM Create a virtual environment if it does not exist
if not exist .venv (
    py -3 -m venv .venv
)

REM Activate the virtual environment
call .venv\Scripts\activate.bat

REM Upgrade pip and install dependencies inside the virtual environment
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

REM Run the bot
python Main.py

REM Keep the console window open after execution
pause