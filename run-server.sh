#!/bin/sh
# Runs the Suno API wrapper (server.py, http://127.0.0.1:8000) and starts it again when it stops.
# Output goes to server.log (kept under 10 MB, one old copy). For a service, see README.md (systemd).
cd "$(dirname "$0")"
while true; do
  if [ -f server.log ] && [ "$(wc -c < server.log)" -gt 10485760 ]; then mv -f server.log server.log.1; fi
  echo "[$(date)] starting server.py" >> server.log
  ./.venv/bin/python server.py >> server.log 2>&1
  echo "[$(date)] server.py stopped (exit $?), restarting in 10 s" >> server.log
  sleep 10
done
