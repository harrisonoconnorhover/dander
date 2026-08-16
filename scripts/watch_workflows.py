#!/usr/bin/env python3
"""Live monitor for Dander agent workflows (the `feature` orchestration and other runs).

Watches the transcript directories every Workflow run writes under
``~/.claude/projects/<this-repo>/*/subagents/workflows/wf_*`` and renders a live, multi-run
dashboard: each run's agents, their role/ticket, whether they're running/done, and pass/fail
verdicts — refreshing in place. Pure stdlib, so it runs with plain ``python3`` (no venv, no deps).

Usage:
    python3 scripts/watch_workflows.py             # live, refresh every 2s, newest 8 runs
    python3 scripts/watch_workflows.py --once      # render one frame and exit
    python3 scripts/watch_workflows.py -n 5        # refresh every 5s
    python3 scripts/watch_workflows.py --all       # include older/idle runs
    python3 scripts/watch_workflows.py PATH         # point at a specific workflows dir or run dir

Nothing here is Dander product code — it's dev/ops tooling for observing the agent workforce.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from typing import TypedDict

HOME = os.path.expanduser("~")
# Claude Code slugs a project's ~/.claude/projects/ dir from its absolute repo path by
# replacing path separators with "-" (e.g. /Users/x/Dev/dander -> -Users-x-Dev-dander). Scope the
# glob to that one project dir — otherwise it silently sweeps in every other Claude Code project on
# the machine, including long-dead ones, and their stale run dirs read as "still running" here.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_SLUG = REPO_ROOT.replace(os.sep, "-")
# `**` matches the <session-id> nesting under the project dir at any depth.
DEFAULT_GLOB = os.path.join(
    HOME,
    ".claude",
    "projects",
    PROJECT_SLUG,
    "**",
    "subagents",
    "workflows",
)

# Consider a run "active" if any transcript changed within this many seconds.
ACTIVE_WINDOW = 25.0

C = {
    "reset": "\033[0m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "cyan": "\033[36m",
    "blue": "\033[34m",
    "mag": "\033[35m",
}
if not sys.stdout.isatty():
    C = {k: "" for k in C}  # no color when piped

# Known workforce roles, matched directly so JSON-escaped quotes in the transcript don't matter.
_ROLE_RE = re.compile(
    r"\b(product|design|code-python|code-sql|code-terraform|pr-review|documentation)\b"
)
_TICKET_RE = re.compile(r"DANDER-\d+")


class AgentSummary(TypedDict):
    """One rendered workflow-agent row."""

    role: str
    ticket: str
    state: str
    summary: str
    age: float


class RunSummary(TypedDict):
    """Typed data required to render one workflow run."""

    id: str
    total: int
    done: int
    running: int
    active: bool
    elapsed: float
    idle: float
    agents: list[AgentSummary]


def human_age(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def find_run_dirs(target: str | None) -> list[str]:
    """Return workflow run directories (wf_*) to display, newest first."""
    if target:
        if os.path.basename(target.rstrip("/")).startswith("wf_"):
            return [target]
        roots = [target]
    else:
        roots = glob.glob(DEFAULT_GLOB, recursive=True)
    runs: list[str] = []
    for root in roots:
        runs.extend(glob.glob(os.path.join(root, "wf_*")))
    runs = [d for d in runs if os.path.isdir(d)]
    runs.sort(key=lambda d: os.path.getmtime(d), reverse=True)
    return runs


def label_for(transcript: str) -> tuple[str, str]:
    """Best-effort (role, ticket) recovered from the head of an agent transcript."""
    role, ticket = "", ""
    try:
        with open(transcript, encoding="utf-8", errors="ignore") as fh:
            head = fh.read(8192)
        m = _ROLE_RE.search(head)
        if m:
            role = m.group(1)
        t = _TICKET_RE.search(head)
        if t:
            ticket = t.group(0)
    except OSError:
        pass
    return role, ticket


def summarize_result(result: object) -> str:
    """One-line summary of a completed agent's structured return value."""
    if not isinstance(result, dict):
        return ""
    if "tickets" in result and isinstance(result["tickets"], list):
        return f"{len(result['tickets'])} ticket(s)"
    if "verdict" in result:
        v = result.get("verdict", "?")
        color = C["green"] if v == "PASS" else C["red"]
        return f"{color}{v}{C['reset']}"
    if "approach" in result:
        return "design ready"
    return ""


def read_journal(run_dir: str) -> tuple[set[str], dict[str, object]]:
    """Return (started agentIds, {agentId: result}) from journal.jsonl."""
    started: set[str] = set()
    results: dict[str, object] = {}
    path = os.path.join(run_dir, "journal.jsonl")
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                aid = d.get("agentId")
                if not aid:
                    continue
                if d.get("type") == "started":
                    started.add(aid)
                elif d.get("type") == "result":
                    results[aid] = d.get("result")
    except OSError:
        pass
    return started, results


def summarize_run(run_dir: str, now: float) -> RunSummary:
    metas = glob.glob(os.path.join(run_dir, "agent-*.meta.json"))
    transcripts = glob.glob(os.path.join(run_dir, "agent-*.jsonl"))
    started, results = read_journal(run_dir)

    run_mtime = os.path.getmtime(run_dir)
    last_activity = max((os.path.getmtime(t) for t in transcripts), default=run_mtime)
    first_seen = min((os.path.getmtime(m) for m in metas), default=run_mtime)
    active = (now - last_activity) < ACTIVE_WINDOW

    agents: list[AgentSummary] = []
    for t in sorted(transcripts, key=os.path.getmtime):
        aid = os.path.basename(t)[len("agent-") : -len(".jsonl")]
        role, ticket = label_for(t)
        if aid in results:
            state = "done"
        elif aid in started:
            state = "run"
        else:
            state = "spawn"
        agents.append(
            {
                "role": role or "agent",
                "ticket": ticket,
                "state": state,
                "summary": summarize_result(results.get(aid)) if aid in results else "",
                "age": now - os.path.getmtime(t),
            }
        )

    return {
        "id": os.path.basename(run_dir),
        "total": len(metas) or len(transcripts),
        "done": len(results),
        "running": len(started) - len(results),
        "active": active,
        "elapsed": now - first_seen,
        "idle": now - last_activity,
        "agents": agents,
    }


def render(runs: list[RunSummary], show_all: bool) -> str:
    now_str = time.strftime("%H:%M:%S")
    header = f"{C['bold']}Dander workflow monitor{C['reset']}"
    out = [f"{header}  {C['dim']}{now_str}  (Ctrl-C to exit){C['reset']}"]
    shown = [r for r in runs if show_all or r["active"] or r["idle"] < 600]
    if not shown:
        msg = "no recent workflow runs. Launch one, or pass --all to see older runs."
        out.append(f"\n  {C['dim']}{msg}{C['reset']}")
        return "\n".join(out)

    for r in shown:
        dot = f"{C['yellow']}●{C['reset']}" if r["active"] else f"{C['dim']}○{C['reset']}"
        if r["active"]:
            status = f"{C['yellow']}RUNNING{C['reset']}"
        else:
            status = f"{C['dim']}idle {human_age(r['idle'])}{C['reset']}"
        out.append(
            f"\n{dot} {C['cyan']}{r['id']}{C['reset']}  {status}  "
            f"{C['dim']}elapsed {human_age(r['elapsed'])}{C['reset']}  "
            f"agents {C['green']}{r['done']} done{C['reset']}"
            + (f", {C['yellow']}{r['running']} running{C['reset']}" if r["running"] > 0 else "")
        )
        for a in r["agents"]:
            if a["state"] == "done":
                marker = f"{C['green']}✓{C['reset']}"
                tail = f"  {a['summary']}" if a["summary"] else ""
            elif a["state"] == "run":
                marker = f"{C['yellow']}▸{C['reset']}"
                tail = f"  {C['dim']}working… ({human_age(a['age'])} since last write){C['reset']}"
            else:
                marker = f"{C['dim']}·{C['reset']}"
                tail = f"  {C['dim']}queued{C['reset']}"
            role = f"{C['blue']}{a['role']:<13}{C['reset']}"
            dash = f"{C['dim']}—{C['reset']}"
            ticket = f"{C['mag']}{a['ticket']}{C['reset']}" if a["ticket"] else dash
            out.append(f"   {marker} {role} {ticket}{tail}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Live monitor for Dander agent workflows.")
    ap.add_argument("path", nargs="?", help="specific workflows dir or a single wf_* run dir")
    ap.add_argument("-n", "--interval", type=float, default=2.0, help="refresh seconds (default 2)")
    ap.add_argument("--once", action="store_true", help="render a single frame and exit")
    ap.add_argument("--all", action="store_true", help="include older/idle runs")
    args = ap.parse_args()

    try:
        while True:
            now = time.time()
            runs = [summarize_run(d, now) for d in find_run_dirs(args.path)]
            frame = render(runs, args.all)
            if args.once:
                print(frame)
                return 0
            sys.stdout.write("\033[2J\033[H" + frame + "\n")
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
