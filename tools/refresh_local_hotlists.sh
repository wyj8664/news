#!/bin/zsh
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT="/Users/wangyajing/skills/新闻/TrendRadar"
weekday=$(date '+%u')
if [[ "$weekday" -ge 6 && "${NEWSHOT_FORCE_WEEKEND:-0}" != "1" ]]; then
  echo "Skipped: weekend. Manual homepage updates remain available."
  exit 0
fi
# Scheduled and manual requests share one durable queue and process lock.
exec /opt/homebrew/bin/python3 "$ROOT/tools/refresh_service.py" --enqueue --scheduled
