#!/usr/bin/env python3
"""Run Tiny Tapeout precheck privately and emit a receipt for the exact GDS.

Run with the flow Python (gdstk, klayout, yaml), a klayout binary on PATH, and
PDK_ROOT set. Neither the candidate nor tt/precheck/reports is overwritten.

    .venv-flow/bin/python flow/precheck_test.py RUN_WORKTREE --tt-root REPO/tt
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import release_receipts as receipts


def tool_files(root):
    return {str(p.relative_to(root)): receipts.digest(p)
            for folder in ("precheck", "tech") for p in sorted((root / folder).rglob("*"))
            if p.is_file() and not {"reports", "__pycache__", ".git"}.intersection(p.relative_to(root).parts)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("worktree")
    ap.add_argument("--tt-root", required=True)
    ap.add_argument("--out", help="fresh output directory; defaults to test/sim_build/precheck.*")
    a = ap.parse_args(argv)
    wt, tt = Path(a.worktree).resolve(), Path(a.tt_root).resolve()
    root = Path(__file__).resolve().parents[1]
    try:
        if not os.environ.get("PDK_ROOT"):
            raise ValueError("PDK_ROOT must be set")
        if not (tt / "precheck/precheck.py").is_file() or not (tt / "tech").is_dir():
            raise ValueError("--tt-root must contain precheck/precheck.py and tech/")
        if a.out:
            out = Path(a.out).resolve()
            out.mkdir(parents=True, exist_ok=False)
        else:
            parent = root / "test/sim_build"
            parent.mkdir(parents=True, exist_ok=True)
            out = Path(tempfile.mkdtemp(prefix="precheck.", dir=parent))
        data = receipts.begin(wt, out, "precheck", root)
        before = tool_files(tt)
        for folder in ("precheck", "tech"):
            shutil.copytree(tt / folder, out / "tt" / folder,
                            ignore=shutil.ignore_patterns("reports", "__pycache__", ".git"))
        if tool_files(out / "tt") != before or tool_files(tt) != before:
            raise ValueError("precheck support tools changed while copying")
        # Upstream writes reports here but assumes the directory already exists.
        # Keep the directory, while excluding every previous run's report files.
        (out / "tt/precheck/reports").mkdir()
        # Its info.yaml search walks upward from GDS; preserve final/gds + lef.
        for path in data["candidate"]:
            if path == "info.yaml" or path.endswith((".gds", ".lef")):
                dst = out / "design" / path
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(wt / path, dst)
                if receipts.digest(dst) != data["candidate"][path]:
                    raise ValueError(f"candidate changed while copying {path}")
        gds = receipts.one((out / "design/runs/wokwi/final/gds").glob("*.gds"), "GDS")
        command = [sys.executable, "precheck.py", "--gds", str(gds), "--tech", "ihp-sg13cmos5l"]
        env = dict(os.environ, PDK="ihp-sg13cmos5l")
        receipts.write_new(out / "command.json", dict(argv=command, cwd=str(out / "tt/precheck"),
                           python=sys.version, pdk_root=env["PDK_ROOT"], pdk=env["PDK"], support_tools=before))
        print(f"precheck outputs: {out}", flush=True)
        with (out / "precheck.log").open("x") as log:
            result = subprocess.run(command, cwd=out / "tt/precheck", env=env, stdout=log, stderr=subprocess.STDOUT)
        receipts.write_new(out / "status.json", {"returncode": result.returncode})
        if tool_files(out / "tt") != before or tool_files(tt) != before:
            raise ValueError("precheck support tools changed during verification")
        if result.returncode:
            raise ValueError(f"precheck process exited {result.returncode}; see {out / 'precheck.log'}")
        print(f"receipt: {receipts.finish(out)}")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"precheck receipt failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
