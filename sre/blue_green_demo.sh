#!/usr/bin/env bash
#
# blue_green_demo.sh
#
# Runnable, self-contained blue/green deploy demo. Closes one item of the
# "no blue-green deployment automation anywhere" gap named in
# ../../02-concepts/03-reliability-debugging-ops.md (section 8) and in
# this folder's README.md.
#
# What it does:
#   1. Starts two versions of a tiny dummy HTTP app (bluegreen_demo/app.py,
#      v1 on :9101 "blue", v2 on :9102 "green") - self-contained so this
#      demo doesn't depend on the sibling services/ microservices other
#      agents are building.
#   2. Fronts them with nginx (nginx-bluegreen.conf in this directory),
#      listening on :8088, initially routed to v1 (blue).
#   3. Fires a continuous stream of requests at :8088 in the background.
#   4. Flips the nginx upstream to v2 (green) and reloads nginx
#      (`nginx -s reload`) WHILE that request stream is still running.
#   5. Reports how many of those in-flight requests failed. A clean flip
#      should show 0 dropped/failed requests - reload swaps the upstream
#      for new connections without killing connections already in flight.
#   6. Cleans up everything it started (nginx, both dummy apps, temp dir)
#      on exit, whether it succeeded or not.
#
# Requirements: nginx, python3, curl - all standard, nothing installed by
# this script. No docker required; this runs entirely as local processes.
#
# Usage:
#   ./blue_green_demo.sh
#
# See README.md in this directory for why this same pattern is
# meaningfully HARDER for a stateful real-time voice service than it is
# for the plain HTTP case demonstrated here.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_PY="${SCRIPT_DIR}/bluegreen_demo/app.py"
NGINX_TEMPLATE="${SCRIPT_DIR}/nginx-bluegreen.conf"

BLUE_PORT=9101
GREEN_PORT=9102
FRONT_PORT=8088
PROBE_COUNT=80
PROBE_INTERVAL=0.05

log() { printf '\n=== %s ===\n' "$1"; }

# --- Preflight -----------------------------------------------------------

command -v nginx  >/dev/null 2>&1 || { echo "ERROR: nginx not found on PATH. Install it (e.g. 'brew install nginx' or 'apt-get install nginx') and re-run." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found on PATH." >&2; exit 1; }
command -v curl   >/dev/null 2>&1 || { echo "ERROR: curl not found on PATH." >&2; exit 1; }
[ -f "$APP_PY" ] || { echo "ERROR: expected dummy app at $APP_PY" >&2; exit 1; }
[ -f "$NGINX_TEMPLATE" ] || { echo "ERROR: expected nginx template at $NGINX_TEMPLATE" >&2; exit 1; }

RUNDIR="$(mktemp -d "${TMPDIR:-/tmp}/bluegreen-demo.XXXXXX")"
mkdir -p "${RUNDIR}/logs" "${RUNDIR}/tmp/client_body" "${RUNDIR}/tmp/proxy" \
         "${RUNDIR}/tmp/fastcgi" "${RUNDIR}/tmp/uwsgi" "${RUNDIR}/tmp/scgi"

BLUE_PID=""
GREEN_PID=""
NGINX_CONF="${RUNDIR}/nginx.conf"
NGINX_STARTED=0

cleanup() {
    log "Cleaning up"
    if [ "$NGINX_STARTED" = "1" ]; then
        nginx -c "$NGINX_CONF" -p "${RUNDIR}/" -s stop >/dev/null 2>&1 || true
    fi
    [ -n "$BLUE_PID" ] && kill "$BLUE_PID" >/dev/null 2>&1 || true
    [ -n "$GREEN_PID" ] && kill "$GREEN_PID" >/dev/null 2>&1 || true
    wait "$BLUE_PID" "$GREEN_PID" >/dev/null 2>&1 || true
    rm -rf "$RUNDIR"
    echo "Removed scratch run dir: $RUNDIR"
}
trap cleanup EXIT INT TERM

render_conf() {
    # Rewrite the upstream's active port and re-point log/temp paths at
    # our scratch run dir. This is the one-line change that IS the
    # blue/green flip; everything else in the config stays identical.
    local active_port="$1"
    sed -e "s#__RUNDIR__#${RUNDIR}#g" -e "s#__ACTIVE_PORT__#${active_port}#g" \
        "$NGINX_TEMPLATE" > "$NGINX_CONF"
}

wait_for() {
    local url="$1" name="$2"
    for _ in $(seq 1 50); do
        if curl -sf "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.1
    done
    echo "ERROR: $name never became ready at $url" >&2
    exit 1
}

# --- Step 1: start v1 (blue) and v2 (green) -------------------------------

log "Starting v1 (blue) on :${BLUE_PORT} and v2 (green) on :${GREEN_PORT}"
VERSION=v1 PORT=$BLUE_PORT  python3 "$APP_PY" > "${RUNDIR}/logs/v1.log" 2>&1 &
BLUE_PID=$!
VERSION=v2 PORT=$GREEN_PORT python3 "$APP_PY" > "${RUNDIR}/logs/v2.log" 2>&1 &
GREEN_PID=$!

wait_for "http://127.0.0.1:${BLUE_PORT}/"  "v1 (blue)"
wait_for "http://127.0.0.1:${GREEN_PORT}/" "v2 (green)"
echo "v1 (blue)  pid=$BLUE_PID  responding on :${BLUE_PORT}"
echo "v2 (green) pid=$GREEN_PID responding on :${GREEN_PORT}"

# --- Step 2: start nginx pointed at blue ----------------------------------

log "Starting nginx on :${FRONT_PORT}, upstream = v1 (blue, :${BLUE_PORT})"
render_conf "$BLUE_PORT"
nginx -t -c "$NGINX_CONF" -p "${RUNDIR}/"
nginx -c "$NGINX_CONF" -p "${RUNDIR}/"
NGINX_STARTED=1
wait_for "http://127.0.0.1:${FRONT_PORT}/health" "nginx front door"

echo "Response via nginx (should be v1):"
curl -s "http://127.0.0.1:${FRONT_PORT}/" | python3 -m json.tool

# --- Step 3: fire a continuous request stream while we flip ---------------

PROBE_LOG="${RUNDIR}/probe.log"
: > "$PROBE_LOG"

log "Firing ${PROBE_COUNT} requests at :${FRONT_PORT} while flipping blue -> green mid-stream"
(
    for i in $(seq 1 "$PROBE_COUNT"); do
        code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${FRONT_PORT}/" || echo "000")
        echo "$code" >> "$PROBE_LOG"
        sleep "$PROBE_INTERVAL"
    done
) &
PROBE_PID=$!

# Let a chunk of requests land on blue first, so the flip happens
# genuinely mid-stream rather than before traffic starts.
sleep 1

log "Flipping upstream: v1 (blue, :${BLUE_PORT}) -> v2 (green, :${GREEN_PORT})"
render_conf "$GREEN_PORT"
nginx -t -c "$NGINX_CONF" -p "${RUNDIR}/"
nginx -c "$NGINX_CONF" -p "${RUNDIR}/" -s reload
echo "Reload issued. New connections now route to v2 (green)."

wait "$PROBE_PID"

echo
echo "Response via nginx (should now be v2):"
curl -s "http://127.0.0.1:${FRONT_PORT}/" | python3 -m json.tool

# --- Step 4: report results -------------------------------------------------

log "Results"
total=$(wc -l < "$PROBE_LOG" | tr -d ' ')
ok=$(grep -c '^200$' "$PROBE_LOG" || true)
bad=$((total - ok))

echo "Total requests fired while flipping: $total"
echo "Successful (HTTP 200):               $ok"
echo "Failed/dropped:                      $bad"

if [ "$bad" -eq 0 ]; then
    echo
    echo "PASS: zero dropped requests during the blue -> green flip."
else
    echo
    echo "NOTE: $bad request(s) failed during the flip - inspect ${PROBE_LOG}"
fi

echo
echo "See README.md in this directory for why this same flip is much"
echo "harder for a stateful real-time voice service (in-flight WebRTC/SIP"
echo "calls need draining, not just a traffic switch) than it is for this"
echo "plain HTTP case."
