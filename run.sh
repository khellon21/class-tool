#!/bin/bash
# Start the class notes app. Put NVIDIA_API_KEY in .env next to this script.
cd "$(dirname "$0")"
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
exec .venv/bin/python app.py
