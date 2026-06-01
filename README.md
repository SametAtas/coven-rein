# coven-rein

A deterministic output guard for [Coven](https://github.com/OpenCoven/coven).

Coven is the authority boundary for what an agent may **do**: every session is
tied to a repo root, and paths, input, and kill are revalidated in Rust. Nothing
is the authority boundary for what the agent **writes**. [rein](https://github.com/SametAtas/rein)
is that, with the same local-first, deterministic, no-LLM stance.

This companion reviews each change an agent makes in a Coven session and acts on
the verdict. No Rust, no fork: it speaks Coven's documented socket contract
(`coven.daemon.v1`) and shells out to rein over the process boundary.

## How it fits Coven

Coven has no plugin or post-step hook, but it exposes two seams this rides on:

- the **repo root** every session is bound to, and
- the local API: `GET /api/v1/events`, `POST /api/v1/sessions/:id/input`,
  `POST /api/v1/sessions/:id/kill`.

Flow: a session event signals the agent finished an edit, the guard runs
`git diff | rein review --diff --format json` on the session root, and on a
`BLOCK` verdict it either feeds the findings back as input (the agent
self-corrects) or kills the session. `PASS` and `WARN` pass through.

```
Coven session  --edit-->  repo root  --diff-->  rein  --verdict-->  input / kill
```

## Run the demo (no daemon needed)

```bash
sh demo.sh
```

It shows a clean agent edit passing and a bad one (leaked key, os.system) getting
a reproducible `BLOCK` with the findings the agent would receive back.

## Run against a live Coven session

Point the watcher at the session's repo root. It rides the seam every session
shares (the root), so it needs no events API:

```bash
python coven_rein.py --watch /path/to/session/repo
```

Every change the agent makes is reviewed live; a `BLOCK` prints the findings.
To also act over Coven's API on a block, pass the socket and session:

```bash
python coven_rein.py --watch /path/to/repo \
    --sock "$COVEN_HOME/coven.sock" --session <id>   # feeds findings back as input
python coven_rein.py --watch /path/to/repo --sock ... --session ... --kill-on-block
```

## Status

- `review_change`, `guidance`, the `--review` path, and the `--watch` loop are
  complete and tested: rein gates a real diff from any working directory.
- The `--watch` path uses only the shared repo root, so it works against any live
  session with no daemon-side wiring.
- `CovenGuard.feed` / `kill` (the optional `--sock`/`--session` actions) call
  Coven's documented socket API and need a running daemon to exercise.
