#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
LOG_DIR="$SCRIPT_DIR/../logs"
mkdir -p "$LOG_DIR"

run_device() {
  timestamp=$(date +"%Y%m%d-%H%M%S")
  echo "[$timestamp] Running device scraper" | tee -a "$LOG_DIR/device_scraper.log"
  "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/router_scraper.py" "$@" >>"$LOG_DIR/device_scraper.log" 2>&1
}

run_bandwidth() {
  timestamp=$(date +"%Y%m%d-%H%M%S")
  echo "[$timestamp] Running bandwidth scraper" | tee -a "$LOG_DIR/bandwidth_scraper.log"
  "$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/bandwidth_scraper.py" "$@" >>"$LOG_DIR/bandwidth_scraper.log" 2>&1
}

last_device_run=0

echo "Starting scraper loop. Press Ctrl+C to exit."
while true; do
  now=$(date +%s)
  if (( now - last_device_run >= 3600 )); then
    run_device "$@"
    last_device_run=$now
  fi
  run_bandwidth "$@"
  sleep 600
done
