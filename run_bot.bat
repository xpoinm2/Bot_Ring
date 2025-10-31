@echo off
REM Create a virtual environment if it does not exist
if not exist .venv (
    python -m venv .venv
)

REM Activate the virtual environment
call .venv\Scripts\activate.bat

REM Upgrade pip and install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

REM Run the bot
python Main.py