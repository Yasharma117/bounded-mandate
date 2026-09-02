#!/usr/bin/env bash
# Attacks on the engine supervisor. Reports, does not assert.
#
# The claim is "it outlives whatever started it, and it never leaves a stale
# server on the port". Both of those are worth trying to break rather than
# trusting, since the whole reason this file exists is that the previous
# arrangement failed silently and got blamed on unrelated features.
cd /Users/yashsharma/BUILDATHON

E=./scripts/engine.sh
PORT=8117
pass=0; fail=0

report() {  # name  ok?  note
    if [ "$2" = "1" ]; then printf "  [ok  ] %s\n" "$1"; pass=$((pass+1))
    else printf "  [FAIL] %s\n" "$1"; fail=$((fail+1)); fi
    [ -n "${3:-}" ] && printf "         %s\n" "$3"
    return 0
}
up() { curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/openapi.json" 2>/dev/null; }
bound() { lsof -ti ":$PORT" >/dev/null 2>&1; }

echo
echo "=== the original failure: signals aimed at whoever launched it ==="

$E stop >/dev/null 2>&1
$E start >/dev/null 2>&1
ENGINE=$(cat .engine.pid)

# A subshell starts nothing — but this is the shape that killed it before: a
# signal to the *group* of the process that launched the engine.
kill -TERM -$$ 2>/dev/null || true
sleep 1
up && report "survives SIGTERM to the launching process group" 1 || report "survives SIGTERM to the launching process group" 0

# SIGHUP is what a closing terminal sends.
kill -HUP "$ENGINE" 2>/dev/null || true
sleep 1
up && report "survives SIGHUP (a closed terminal)" 1 || report "survives SIGHUP (a closed terminal)" 0

echo
echo "=== stale and corrupt pidfiles ==="

# A pidfile naming a pid that is long gone.
$E stop >/dev/null 2>&1
echo "999999" > .engine.pid
out=$($E start 2>&1); code=$?
up && [ "$code" = "0" ] && report "a stale pidfile does not block a start" 1 "$out" \
    || report "a stale pidfile does not block a start" 0 "$out"

# Garbage in the pidfile.
$E stop >/dev/null 2>&1
echo "not-a-pid" > .engine.pid
out=$($E start 2>&1); code=$?
up && [ "$code" = "0" ] && report "a corrupt pidfile does not block a start" 1 "$out" \
    || report "a corrupt pidfile does not block a start" 0 "$out"

# An empty pidfile.
$E stop >/dev/null 2>&1
: > .engine.pid
out=$($E start 2>&1)
up && report "an empty pidfile does not block a start" 1 || report "an empty pidfile does not block a start" 0

echo
echo "=== stop never leaves the port held ==="

for i in 1 2 3 4 5; do
    $E stop >/dev/null 2>&1
    if bound; then report "stop leaves nothing on the port (cycle $i)" 0 "port still bound"; break; fi
    $E start >/dev/null 2>&1
    if ! up; then report "start comes back (cycle $i)" 0 "not answering"; break; fi
    [ "$i" = "5" ] && report "five stop/start cycles leave no orphan and always come back" 1
done

echo
echo "=== a foreign process on the port ==="

$E stop >/dev/null 2>&1
python3 -c "
import socket, time, sys
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', $PORT)); s.listen(1)
sys.stderr.write('bound\n'); sys.stderr.flush()
time.sleep(12)
" 2>/dev/null &
SQUATTER=$!
sleep 1
out=$($E start 2>&1); code=$?
[ "$code" != "0" ] && report "start refuses when something else holds the port" 1 "$(echo "$out" | head -2 | tr '\n' ' ')" \
    || report "start refuses when something else holds the port" 0 "started anyway: $out"
kill $SQUATTER 2>/dev/null; wait $SQUATTER 2>/dev/null || true
sleep 1

echo
echo "=== concurrent starts ==="

$E stop >/dev/null 2>&1
rm -f .engine.pid
($E start >/dev/null 2>&1) & ($E start >/dev/null 2>&1) & ($E start >/dev/null 2>&1) &
wait
sleep 2
n=$(lsof -ti ":$PORT" 2>/dev/null | wc -l | tr -d ' ')
[ "$n" = "1" ] && report "three simultaneous starts leave exactly one server" 1 \
    || report "three simultaneous starts leave exactly one server" 0 "$n processes on :$PORT"

# The half the first run missed: one server was left, and the pidfile named
# something else — so `stop` could no longer stop it.
onport=$(lsof -ti ":$PORT" 2>/dev/null | head -1)
infile=$(cat .engine.pid 2>/dev/null)
[ -n "$onport" ] && [ "$onport" = "$infile" ] \
    && report "and the pidfile still names the server that is running" 1 \
    || report "and the pidfile still names the server that is running" 0 "port=$onport pidfile=$infile"

echo
echo "=== state survives a restart storm ==="

$E status >/dev/null 2>&1 || $E start >/dev/null 2>&1
curl -fsS -X PUT "http://127.0.0.1:$PORT/api/list/usual/schedule" \
     -H "Content-Type: application/json" -d '{"every_days":11}' >/dev/null 2>&1
curl -fsS -X PUT "http://127.0.0.1:$PORT/api/address" \
     -H "Content-Type: application/json" -d '{"address_id":"d6vvq4cia3n7nnhs7400"}' >/dev/null 2>&1

for _ in 1 2 3; do $E restart >/dev/null 2>&1; done
sleep 1
days=$(curl -fsS "http://127.0.0.1:$PORT/api/lists" 2>/dev/null | python3 -c "
import json,sys
print(next(l['every_days'] for l in json.load(sys.stdin)['lists'] if l['list_id']=='usual'))" 2>/dev/null)
addr=$(curl -fsS "http://127.0.0.1:$PORT/api/addresses" 2>/dev/null | python3 -c "
import json,sys; print(json.load(sys.stdin)['delivery_id'])" 2>/dev/null)
[ "$days" = "11" ] && report "a list edit survives three restarts" 1 || report "a list edit survives three restarts" 0 "every_days=$days"
[ "$addr" = "d6vvq4cia3n7nnhs7400" ] && report "an address choice survives three restarts" 1 \
    || report "an address choice survives three restarts" 0 "delivery_id=$addr"

echo
echo "=== put it back the way the demo wants it ==="
curl -fsS -X PUT "http://127.0.0.1:$PORT/api/list/usual/schedule" \
     -H "Content-Type: application/json" -d '{"every_days":4}' >/dev/null 2>&1
curl -fsS -X PUT "http://127.0.0.1:$PORT/api/address" \
     -H "Content-Type: application/json" -d '{"address_id":"d86lmbjedmej3uqmebcg"}' >/dev/null 2>&1
$E status | sed 's/^/  /'

echo
echo "  $pass passed, $fail failed"
