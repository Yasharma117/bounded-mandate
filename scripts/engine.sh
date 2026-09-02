#!/usr/bin/env bash
# The engine, as a process that outlives the terminal that started it.
#
#     ./scripts/engine.sh start | stop | restart | status | log
#
# Started with `nohup … &` from inside another program, uvicorn stays in that
# program's process group and dies with it — so an editor window closing, or an
# agent's shell call timing out, takes the engine down and the app says
# "Can't reach the engine" about a server nobody knowingly stopped.
#
# So it is launched into a session of its own, which is the whole point of this
# file. Everything else here is so that "it is running" and "it is answering"
# are not confused with each other.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${BM_PORT:-8117}"
PIDFILE=".engine.pid"
LOCKDIR=".engine.lock"
LOGFILE=".engine.log"
# Two different questions, two different endpoints.
#
# `/api/home` is what the app opens on, and on the live backend it calls Swiggy
# — measured at 4.1s against 0.02s for `/openapi.json`. Using it as a liveness
# probe meant a perfectly healthy engine "failed to start", because the probe
# timed out on a round trip to a grocery shop.
LIVE="http://127.0.0.1:${PORT}/openapi.json"   # is the process serving?
USABLE="http://127.0.0.1:${PORT}/api/home"     # can it reach the shop?

running() {  # a live pid from our own pidfile
    [ -f "$PIDFILE" ] || return 1
    local pid; pid=$(cat "$PIDFILE" 2>/dev/null) || return 1
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

answering() { curl -fsS -o /dev/null --max-time 2 "$LIVE" 2>/dev/null; }

start() {
    # `mkdir` is atomic, which `[ -e ]` then `touch` is not. Three starts racing
    # left one server on the port and a pidfile naming a different, dead process
    # — so `stop` could no longer stop the thing that was running, which is the
    # stale-server state this whole file exists to avoid. Measured, not feared.
    if ! mkdir "$LOCKDIR" 2>/dev/null; then
        echo "  another start is in progress"
        return 1
    fi
    trap 'rmdir "$LOCKDIR" 2>/dev/null || true' RETURN

    if running; then
        echo "  already running (pid $(cat "$PIDFILE")) on :$PORT"; return 0
    fi
    # The port, not just the pidfile. A stale uvicorn from another terminal
    # serving an older shape of the API is the failure this project has already
    # been bitten by twice, and it looks exactly like the code being wrong.
    if lsof -ti ":$PORT" >/dev/null 2>&1; then
        echo "  something else is already on :$PORT — pid $(lsof -ti ":$PORT" | tr '\n' ' ')"
        echo "  it is not ours (no pidfile). Stop it, or set BM_PORT."
        return 1
    fi
    [ -f .env ] || { echo "  no .env — the engine needs its keys"; return 1; }

    set -a; . ./.env; set +a
    # A session of its own, which is the entire point of this file. `setsid` is
    # Linux-only and macOS has no equivalent binary, so this calls `setsid(2)`
    # the portable way: `start_new_session` runs it in the child between fork
    # and exec. Plain `nohup … &` only ignores SIGHUP — it leaves the process in
    # the caller's group, where a SIGTERM to that group still takes it out.
    # The venv's uvicorn directly, not `uv run uvicorn`. `uv run` forks the real
    # server as a child, so the pid we could record would be the wrapper's — and
    # `stop` would kill the wrapper and leave uvicorn holding the port, which is
    # the exact stale-server failure this script exists to prevent.
    SERVER=".venv/bin/uvicorn"
    [ -x "$SERVER" ] || { echo "  no $SERVER — run 'uv sync' first"; return 1; }

    python3 - "$SERVER" "$PORT" "$LOGFILE" "$PIDFILE" <<'LAUNCH'
import signal, subprocess, sys
server, port, logfile, pidfile = sys.argv[1:5]


def detach():
    """Runs in the child, between fork and exec.

    A new session already means no controlling terminal, so the kernel sends no
    SIGHUP when a terminal closes. Ignoring it as well is what `nohup` does and
    costs nothing: `SIG_IGN` survives exec (a handler would not), so uvicorn
    inherits it. Without this, an explicit SIGHUP still killed the engine.
    """
    signal.signal(signal.SIGHUP, signal.SIG_IGN)


with open(logfile, "ab") as log:
    child = subprocess.Popen(
        [server, "bounded_mandate.web:app", "--host", "0.0.0.0", "--port", port],
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True, preexec_fn=detach,
    )
open(pidfile, "w").write(str(child.pid) + "\n")
LAUNCH

    # "Started" means answering. A pid that exits half a second later because
    # the port was taken is not a running engine, and reporting it as one is how
    # you find out on camera.
    # A deadline, not an iteration count: each probe costs between nothing and
    # its timeout, so counting iterations makes the worst case unbounded — and
    # a start that appears to hang for minutes is worse than one that fails.
    local deadline=$((SECONDS + 30))
    while [ "$SECONDS" -lt "$deadline" ]; do
        if answering; then
            echo "  started (pid $(cat "$PIDFILE")) on :$PORT"
            status_line
            return 0
        fi
        running || { echo "  died on startup — last lines:"; tail -15 "$LOGFILE"; rm -f "$PIDFILE"; return 1; }
        sleep 0.25
    done
    echo "  started but not answering after 30s — last lines:"; tail -15 "$LOGFILE"
    return 1
}

stop() {
    if ! running; then
        rm -f "$PIDFILE"
        echo "  not running"
        return 0
    fi
    local pid; pid=$(cat "$PIDFILE")
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.25
    done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "  stopped (was pid $pid)"
}

status_line() {
    # Up and usable are different questions: the Swiggy token lapses every five
    # days, and an engine that answers perfectly while the shop refuses it is
    # the state most worth naming before a demo rather than during one.
    #
    # Passed as an argument rather than piped, so the reader below can be a
    # plain heredoc — quoting a JSON-handling script inside shell quotes inside
    # an f-string is how the first version of this shipped a SyntaxError.
    local body
    body=$(curl -fsS --max-time 20 "$USABLE" 2>/dev/null) || return 0
    python3 - "$body" <<'READ'
import json, sys
try:
    shop = json.loads(sys.argv[1]).get("shop") or {}
except Exception:
    sys.exit()
where = shop.get("backend", "?")
ok = shop.get("reachable")
print("  shop: " + str(where) + ("" if ok is None else "  reachable=" + str(ok)))
detail = shop.get("detail")
if not ok and detail:
    print("        " + str(detail)[:90])
READ
}

status() {
    if running && answering; then
        echo "  running (pid $(cat "$PIDFILE")) on :$PORT — answering"
        status_line
        return 0
    fi
    if running; then
        echo "  pid $(cat "$PIDFILE") is alive but :$PORT is not answering"
        return 1
    fi
    if lsof -ti ":$PORT" >/dev/null 2>&1; then
        echo "  not ours, but something is on :$PORT — pid $(lsof -ti ":$PORT" | tr '\n' ' ')"
        return 1
    fi
    echo "  not running"
    return 1
}

case "${1:-status}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; start ;;
    status)  status ;;
    log)     tail -f "$LOGFILE" ;;
    *)       echo "usage: $0 {start|stop|restart|status|log}"; exit 2 ;;
esac
