@echo off
echo Starting Virtual Try-On Application...

:: Start the Python HTTP Server for the Frontend in a new window
echo Starting Frontend on Port 5500...
start cmd /k "cd frontend && python -m http.server 5500"

:: Start the Uvicorn Server for the Backend in this window
echo Starting Backend on Port 8000...
cd backend
call .venv\Scripts\activate
uvicorn app.main:app --reload