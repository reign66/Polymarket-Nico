#!/bin/bash
cd "$(dirname "$0")"

if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

if [ -d "venv" ]; then
    source venv/bin/activate
fi

mkdir -p logs

echo "Starting Polymarket Bot..."
nohup python main.py > logs/bot_stdout.log 2>&1 &
echo $! > .pid
echo "Bot started with PID $(cat .pid)"
echo "Dashboard: http://localhost:${PORT:-5000}"
echo "Logs: tail -f logs/bot.log"
