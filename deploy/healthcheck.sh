#!/usr/bin/env bash
# Is the collector actually WRITING, as opposed to merely running?
#
# systemd's Restart=always covers the process dying. It does not cover the
# process being alive and stuck - a socket read with no timeout, a wedged DNS
# lookup, a retry loop that never exits. That is the failure this project
# already hit once: 90 minutes of a healthy-looking process producing nothing,
# discovered only because the data file had stopped growing.
#
# So the health signal is the data, not the process table.
#
#   ./healthcheck.sh              report and exit 1 if stale
#   ./healthcheck.sh --restart    also restart the collector if stale
#
# Exit 0 fresh, 1 stale, 2 no data at all.

set -uo pipefail

DATA_DIR="${DATA_DIR:-/opt/quant-research/data}"
MAX_AGE="${MAX_AGE:-600}"          # 10 minutes = 10 missed 60s cycles
SERVICE="${SERVICE:-kalshi-collector}"
RESTART=0
[[ "${1:-}" == "--restart" ]] && RESTART=1

newest=$(find "$DATA_DIR/raw" -name 'kalshi_orderbook_*.jsonl' -printf '%T@ %p\n' 2>/dev/null \
         | sort -rn | head -1)

if [[ -z "$newest" ]]; then
    echo "CRITICAL: no order book files under $DATA_DIR/raw"
    exit 2
fi

mtime=${newest%% *}
path=${newest#* }
age=$(( $(date +%s) - ${mtime%.*} ))

if (( age <= MAX_AGE )); then
    echo "OK: $(basename "$path") written ${age}s ago"
    exit 0
fi

echo "STALE: $(basename "$path") last written ${age}s ago (limit ${MAX_AGE}s)"

if (( RESTART )); then
    # Log loudly before acting. A watchdog that restarts silently turns a
    # recurring bug into a statistic nobody ever looks at, and the restart
    # count is the only evidence the underlying fault exists.
    logger -t kalshi-watchdog "data stale ${age}s, restarting $SERVICE"
    echo "restarting $SERVICE"
    systemctl restart "$SERVICE"
fi

exit 1
