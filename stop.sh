#!/bin/bash
cd "$(dirname "$0")"

if [ -f .pid ]; then
    PID=$(cat .pid)
    echo "Stopping Polymarket Bot (PID: $PID)..."
    kill $PID 2>/dev/null
    rm .pid
    echo "Bot stopped"
else
    echo "No PID file found. Bot may not be running."
    # Try to find and kill anyway
    pkill -f "python main.py" 2>/dev/null
fi
