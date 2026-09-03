#!/usr/bin/env bash
# Put a fresh Razorpay test key pair into .env without it touching the screen,
# the shell history, or a chat transcript.
#
#     ./scripts/set_razorpay_keys.sh
#
# Generate the pair first: Razorpay dashboard -> Test Mode -> Settings ->
# API Keys -> Generate Test Key. The secret is shown exactly once.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077

[ -f .env ] || { echo "no .env here — run this from the repo"; exit 1; }

read -rp  "Key id (rzp_test_…): " KID
read -rsp "Key secret (hidden): " KSEC
echo

case "$KID" in
  rzp_test_*) ;;
  rzp_live_*) echo "that is a LIVE key — refusing. This project is test-mode only."; exit 1 ;;
  *)          echo "that does not look like a Razorpay key id"; exit 1 ;;
esac
[ -n "$KSEC" ] || { echo "empty secret"; exit 1; }

cp .env ".env.backup.$(date +%s)"
chmod 600 .env.backup.*

python3 -c "$(cat <<'PY'
import pathlib, sys
kid, sec = sys.argv[1], sys.stdin.readline().rstrip("\n")
p = pathlib.Path(".env")
out, seen = [], set()
for line in p.read_text().splitlines():
    if line.startswith("RAZORPAY_KEY_ID="):
        line = f"RAZORPAY_KEY_ID={kid}"; seen.add("id")
    elif line.startswith("RAZORPAY_KEY_SECRET="):
        line = f"RAZORPAY_KEY_SECRET={sec}"; seen.add("secret")
    out.append(line)
if "id" not in seen:
    out.append(f"RAZORPAY_KEY_ID={kid}")
if "secret" not in seen:
    out.append(f"RAZORPAY_KEY_SECRET={sec}")
p.write_text("\n".join(out) + "\n")
PY
 )" "$KID" < <(printf '%s\n' "$KSEC")

chmod 600 .env
echo "written to .env (mode 600, previous copy kept as .env.backup.*)"

# Prove it works before you find out on camera.
set -a; . ./.env; set +a
NETRC=$(mktemp)
# Armed before anything is written to it. A Ctrl-C in the window between the
# write and the trap would leave the secret on disk, which is the one outcome
# this whole file exists to avoid.
trap 'rm -f "$NETRC"' EXIT INT TERM
chmod 600 "$NETRC"
printf 'machine api.razorpay.com login %s password %s\n' "$KID" "$KSEC" > "$NETRC"
code=$(curl -s -o /dev/null -w "%{http_code}" --netrc-file "$NETRC" \
  -X POST https://api.razorpay.com/v1/orders \
  -H "Content-Type: application/json" -d '{"amount":100,"currency":"INR"}')
case "$code" in
  200) echo "verified: Razorpay accepted the pair (test order created)." ;;
  401) echo "STILL 401 — the secret does not match that key id. Regenerate and re-run."; exit 1 ;;
  *)   echo "unexpected $code from Razorpay — not an auth failure, but check it."; exit 1 ;;
esac
