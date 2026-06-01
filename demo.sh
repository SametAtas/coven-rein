#!/bin/sh
# End-to-end demo: an agent edits a repo, rein gates the change in a Coven loop.
# Needs only git, python3, and rein on PATH (pip install rein from source).
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(mktemp -d)"
trap 'rm -rf "$REPO"' EXIT

cd "$REPO"
git init -q
printf 'def add(a, b):\n    return a + b\n' > app.py
git add app.py
git -c user.email=demo@local -c user.name=demo commit -qm "initial"

echo "== 1. agent makes a CLEAN edit =="
printf '\n\ndef sub(a: int, b: int) -> int:\n    return a - b\n' >> app.py
python3 "$HERE/coven_rein.py" --review "$REPO" || true

echo
echo "== 2. agent makes a BAD edit (leaked key + os.system) =="
# Build the sample key from parts so this source file holds no contiguous
# secret (keeps GitHub push protection quiet); app.py gets the full key at run.
K="AKIA"
printf '\nimport os\nKEY = "%sIOSFODNN7EXAMPLE"\ndef run(c):\n    os.system(c)\n' "$K" >> app.py
python3 "$HERE/coven_rein.py" --review "$REPO" || echo "(exit nonzero: Coven would feed this back or kill the session)"
