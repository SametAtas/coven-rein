"""coven-rein: a deterministic output guard for Coven sessions.

Coven (github.com/OpenCoven/coven) is the authority boundary for what an agent
may DO: every session is tied to a repo root, and paths/input/kill are
revalidated in Rust. Nothing is the authority boundary for what the agent
WRITES. rein is that, with the same local-first, deterministic, no-LLM stance.

This companion watches a Coven session's repo root, runs `rein review` on each
change the agent makes, and on a BLOCK verdict acts over Coven's existing local
API: feed the findings back to the agent as input, or kill the session. No Rust,
no fork. It speaks Coven's documented socket contract (coven.daemon.v1) and
shells out to rein.

Run the offline demo (no daemon needed):
    python coven_rein.py --review /path/to/repo
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from http.client import HTTPConnection


@dataclass
class Verdict:
    verdict: str            # PASS | WARN | BLOCK
    findings: list[dict]

    @property
    def blocked(self) -> bool:
        return self.verdict == "BLOCK"

    def summary(self) -> str:
        ids = ", ".join(f["rule_id"] for f in self.findings) or "none"
        return f"{self.verdict} ({len(self.findings)} finding(s): {ids})"


def review_change(root: str, base: str = "HEAD") -> Verdict:
    """Run rein over the agent's uncommitted diff in `root`.

    The seam is the process boundary: Coven is Rust, rein is Python, they meet at
    a diff in and a verdict out. Reviewing the diff (not the whole tree) judges
    only what the agent just changed.
    """
    diff = subprocess.run(
        ["git", "-C", root, "diff", base],
        capture_output=True, text=True, check=True,
    ).stdout
    if not diff.strip():
        return Verdict("PASS", [])
    out = subprocess.run(
        ["rein", "review", "--diff", "--format", "json"],
        input=diff, capture_output=True, text=True, cwd=root,
    ).stdout
    data = json.loads(out)
    return Verdict(data.get("verdict", "PASS"), data.get("findings", []))


def guidance(v: Verdict) -> str:
    """Turn findings into a short instruction to hand back to the agent."""
    lines = [f"- {f['rule_id']}: {f['message']}" for f in v.findings]
    return "rein blocked this change; fix and retry:\n" + "\n".join(lines)


class _UnixHTTPConnection(HTTPConnection):
    """http.client over a Unix socket (Coven listens at <covenHome>/coven.sock)."""

    def __init__(self, path: str) -> None:
        super().__init__("localhost")
        self._path = path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self._path)
        self.sock = s


class CovenGuard:
    """Drive rein from a live Coven session over the coven.daemon.v1 API.

    Endpoints are Coven's documented contract; the guard logic is complete. The
    only Coven-side requirement is a running daemon to exercise this against.
    """

    def __init__(self, sock_path: str, session_id: str, root: str,
                 input_field: str = "input") -> None:
        self.sock_path = sock_path
        self.session_id = session_id
        self.root = root
        self.input_field = input_field  # JSON body key the /input endpoint expects

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        conn = _UnixHTTPConnection(self.sock_path)
        payload = json.dumps(body) if body is not None else None
        headers = {"Content-Type": "application/json"} if body else {}
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode() or "{}"
        return json.loads(raw)

    def kill(self) -> None:                       # POST /api/v1/sessions/:id/kill
        self._request("POST", f"/api/v1/sessions/{self.session_id}/kill")

    def feed(self, text: str) -> None:            # POST /api/v1/sessions/:id/input
        self._request("POST", f"/api/v1/sessions/{self.session_id}/input",
                      {self.input_field: text})

    def on_agent_change(self, *, kill_on_block: bool = False) -> Verdict:
        """Review the current change and act on the verdict.

        Call this when a session event signals the agent finished an edit
        (GET /api/v1/events?sessionId=...). PASS/WARN are let through; BLOCK
        feeds the findings back so the agent self-corrects, or kills the session.
        """
        v = review_change(self.root)
        if v.blocked:
            if kill_on_block:
                self.kill()
            else:
                self.feed(guidance(v))
        return v


def watch(root: str, interval: float = 2.0, guard: "CovenGuard | None" = None,
          kill_on_block: bool = False) -> None:
    """Poll a session's repo root and review each new change the agent makes.

    This rides the one seam every Coven session shares: the repo root it is bound
    to. It needs no events API, so it works against any live session by pointing
    at its root. When a `CovenGuard` is given, a BLOCK feeds the findings back as
    input (or kills the session); otherwise it just reports.
    """
    print(f"coven-rein: watching {root} every {interval}s (Ctrl-C to stop)")
    last = ""
    while True:
        diff = subprocess.run(
            ["git", "-C", root, "diff"], capture_output=True, text=True,
        ).stdout
        if diff != last and diff.strip():
            v = review_change(root)
            print(f"[change] {v.summary()}")
            if v.blocked:
                print(guidance(v))
                if guard is not None:
                    guard.kill() if kill_on_block else guard.feed(guidance(v))
        last = diff
        time.sleep(interval)


def _cli() -> int:
    p = argparse.ArgumentParser(description="rein output guard for Coven")
    p.add_argument("--review", metavar="REPO",
                   help="review the working diff in REPO once and exit")
    p.add_argument("--watch", metavar="REPO",
                   help="watch REPO and review each change the agent makes")
    p.add_argument("--interval", type=float, default=2.0,
                   help="watch poll interval in seconds (default 2)")
    p.add_argument("--sock", help="Coven socket path; enables input/kill on BLOCK")
    p.add_argument("--session", help="Coven session id (with --sock)")
    p.add_argument("--kill-on-block", action="store_true",
                   help="kill the session on BLOCK instead of feeding findings back")
    p.add_argument("--input-field", default="input",
                   help="JSON body key Coven's /input endpoint expects (default: input)")
    args = p.parse_args()

    if args.review:
        v = review_change(args.review)
        print(f"rein verdict: {v.summary()}")
        if v.blocked:
            print(guidance(v))
            return 1
        return 0

    if args.watch:
        guard = None
        if args.sock and args.session:
            guard = CovenGuard(args.sock, args.session, args.watch, args.input_field)
        try:
            watch(args.watch, args.interval, guard, args.kill_on_block)
        except KeyboardInterrupt:
            print("\ncoven-rein: stopped")
        return 0

    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
