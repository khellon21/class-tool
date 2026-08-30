#!/bin/bash
# Double-click this file in Finder to start the app.
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "No .env file - the app needs your NVIDIA API key."
  echo "Create it with:  echo 'NVIDIA_API_KEY=your-key' > .env"
  echo
  read -n 1 -s -r -p "Press any key to close."
  exit 1
fi

# Reuse the tab if it is already running rather than failing on a busy port.
if lsof -ti:5005 >/dev/null 2>&1; then
  echo "Already running. Opening it."
  open http://localhost:5005
  exit 0
fi

set -a
. ./.env
set +a

echo "Starting Class Notes at http://localhost:5005"
echo "Keep this window open while you use it. Close it or press Ctrl+C to stop."
echo

(sleep 2 && open http://localhost:5005) &
exec .venv/bin/python app.py
