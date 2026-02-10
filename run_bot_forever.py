import subprocess
import time
import sys
import os

RESTART_DELAY = 10  # seconds

# Get the venv Python path
venv_python = os.path.join(os.path.dirname(__file__), "venv", "Scripts", "python.exe")

while True:
    print("Starting trading bot...")
    
    process = subprocess.Popen(
        [venv_python, "main.py"]  # Use venv Python instead of system Python
    )

    process.wait()  # wait until it crashes or exits

    print("Bot stopped. Restarting in 10 seconds...")
    time.sleep(RESTART_DELAY)