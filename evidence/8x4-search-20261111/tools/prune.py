#!/usr/bin/env python3
"""Keep the top KEEP run worktrees of a search, delete the rest (runner1).

    prune.py NAME        # NAME just finished; its search is NAME[:3] (aqf, aqp)

Only worktrees under ~/protoemu-flow/aq-search/runs/protoemu-exp-aq* whose
results/NAME/done exists are considered; a run without a done marker (still
running) is never touched, nor one with results/NAME/keep (pinned, e.g. while
its release receipts run); pinned runs count against KEEP. Ranking: table.score().
"""
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import table  # noqa: E402

B = pathlib.Path.home() / "protoemu-flow/aq-search"
RUNS = B / "runs"
KEEP = int(os.environ.get("KEEP", "2"))
IMAGE = "ghcr.io/librelane/librelane:3.1.0.dev3"


def remove(name):
    w = RUNS / f"protoemu-exp-{name}"
    assert w.parent == RUNS and w.name.startswith("protoemu-exp-aq") and "/" not in name
    subprocess.run(["git", "-C", str(B / "repo"), "worktree", "remove", "--force", str(w)], check=False)
    if w.exists():
        # root-owned files written by the dockerized flow
        subprocess.run(["docker", "run", "--rm", "-v", f"{w}:/x", IMAGE, "sh", "-c",
                        "rm -rf /x/* /x/.[!.]* 2>/dev/null; true"], check=False)
        subprocess.run(["rmdir", str(w)], check=False)
        subprocess.run(["git", "-C", str(B / "repo"), "worktree", "prune"], check=False)
    print(f"pruned {name}: {'FAILED, still present' if w.exists() else 'removed'}")


def main():
    group = sys.argv[1][:3]
    done = [n for n in table.names() if n.startswith(group)]
    present = [n for n in done if (RUNS / f"protoemu-exp-{n}").exists()]
    pinned = [n for n in present if os.path.exists(f"{table.B}/{n}/keep")]   # e.g. receipts running
    ranked = sorted((n for n in present if n not in pinned), key=lambda n: table.score(table.load(n)), reverse=True)
    for n in ranked[max(KEEP - len(pinned), 0):]:
        remove(n)
    print(f"kept {pinned + ranked[:max(KEEP - len(pinned), 0)]} ({group}; pinned {pinned})")


if __name__ == "__main__":
    main()
