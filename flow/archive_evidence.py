#!/usr/bin/env python3
"""Pack completed release evidence without changing any receipt or input.

    flow/archive_evidence.py WORK/verdict.json --output wirelingo-release-evidence-REV-RUN.tar.gz \
        [--repro RUN ...] [--extra FILE ...] [--note TEXT] [--base DIR]

Reads the verdict that flow/release_evidence.py wrote, re-verifies every
receipt against the real candidate run and the validation root, and archives
every hashed file (candidate, validation, runner, result artifacts), the
receipts and their inputs, the job logs, the release-check log and verdict,
the source bundle, the release scripts, optional reproduction runs' candidate
files and extra files. Symlinks are dereferenced; archive names are paths
relative to --base (default: the directory holding the work directory).
MANIFEST.json records each payload file's sha256, and the archive is read back
and compared before it is reported. An existing output is never overwritten.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_receipts as receipts  # noqa: E402

RELEASE_SCRIPTS = ("flow/release_check.py", "flow/signoff.py", "flow/release_evidence.py",
                   "flow/archive_evidence.py", "docs/release-evidence.md")


class Manifest:
    """Archive name -> source file and sha256, refusing conflicting entries."""

    def __init__(self, base):
        self.base = Path(base).resolve()
        self.files = {}

    def add(self, path, expected=None, name=None):
        path = Path(path)
        real = path.resolve()
        if name is None:
            if not real.is_relative_to(self.base):
                raise ValueError(f"{path} is outside the archive base {self.base}")
            name = str(real.relative_to(self.base))
        actual = receipts.digest(real)
        if expected is not None and actual != expected:
            raise ValueError(f"hash mismatch: {path}")
        if name in self.files and self.files[name]["sha256"] != actual:
            raise ValueError(f"conflicting archive entry: {name}")
        self.files[name] = dict(source_path=str(real), sha256=actual, bytes=real.stat().st_size)
        return name


def collect(verdict, manifest, *, verify=receipts.verify, repro=(), extras=()):
    """Add every file the verdict's receipts hash; return the verified list."""
    run, validation = Path(verdict["run"]), Path(verdict["validation_root"])
    work, runner = Path(verdict["work"]), Path(verdict["runner_root"])
    if not verdict.get("verdict", "").startswith("RELEASE PASSED") or verdict.get("exit") != 0:
        raise ValueError(f"verdict is not a pass: {verdict.get('verdict')}")
    kinds = sorted(r["kind"] for r in verdict["receipts"])
    if kinds.count("gl") != 1 or kinds.count("precheck") != 1 or "sdf" not in kinds:
        raise ValueError(f"verdict lacks a GL, SDF or precheck receipt: {kinds}")
    verified = []
    for entry in verdict["receipts"]:
        path = Path(entry["path"])
        if receipts.digest(path) != entry["sha256"]:
            raise ValueError(f"receipt changed after the verdict: {path}")
        data = verify(path, run, entry["kind"], validation if entry["kind"] in ("gl", "sdf") else None)
        manifest.add(path)
        manifest.add(path.parent / "inputs.json")
        for key, root in (("candidate", data["worktree"]), ("validation", data["validation_root"]),
                          ("runner", data["runner_root"]), ("artifacts", path.parent)):
            for name, digest in data[key].items():
                manifest.add(Path(root) / name, digest)
        verified.append(dict(job=entry["job"], kind=entry["kind"], receipt=manifest.add(path),
                             sha256=entry["sha256"], candidate_files=len(data["candidate"]),
                             validation_files=len(data["validation"]), artifacts=len(data["artifacts"])))
    for root in repro:
        for name, digest in receipts.candidate_files(root).items():
            manifest.add(Path(root) / name, digest)
    for name in RELEASE_SCRIPTS:
        if (runner.parent / name).is_file():
            manifest.add(runner.parent / name)
    for path in sorted((work / "logs").glob("*.log")):
        manifest.add(path)
    for name in ("release-evidence.log", "release-check.log", "verdict.json", "source.bundle"):
        manifest.add(work / name)
    for path in extras:
        manifest.add(path)
    return verified


def head(root):
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def readme(verdict, manifest, verified, repro, note):
    base = manifest.base
    rel = lambda p: str(Path(p).resolve().relative_to(base)) if Path(p).resolve().is_relative_to(base) else str(p)
    lines = [f"Wirelingo release evidence: validation {verdict['validation_head'][:7]}, run {Path(verdict['run']).name}",
             "",
             f"Verdict: {verdict['verdict']}",
             "",
             f"This archive retains every file hashed by the {len(verified)} receipts ("
             + ", ".join(f"{v['job']}" for v in verified) + "), including candidate artifacts, "
             "test/firmware inputs, runner scripts and result artifacts, plus the job logs, the "
             "release-check log, verdict.json, the validation source bundle and the release scripts."
             + (" It also retains the candidate files of the reproduction run(s) "
                + ", ".join(rel(r) for r in repro) + "." if repro else ""),
             "Symlinks were dereferenced. MANIFEST.json records every payload file hash. This is an",
             "evidence bundle, not a hermetic toolchain or PDK installation.",
             "",
             f"Paths are relative to {base}. To verify after extraction, use the archived",
             f"flow/release_check.py with candidate {rel(verdict['run'])}, --validation-root",
             f"{rel(verdict['validation_root'])} and the receipt paths in MANIFEST.json. No receipt has",
             "been rewritten. The recorded original absolute output paths are needed to validate",
             "annotation/pass banners, but do not require writing there. The runners saw the run",
             f"through the view {rel(verdict['view'])}; receipts bind contents, not that path."]
    if note:
        lines += ["", note]
    return "\n".join(lines) + "\n"


def write_archive(output, manifest, extra_members):
    """Create `output` exclusively, then read every payload back and compare."""
    output = Path(output)
    if output.exists():
        raise ValueError(f"refusing to overwrite {output}")
    with tarfile.open(output, "x:gz", dereference=True) as tf:
        for name, entry in sorted(manifest.files.items()):
            tf.add(entry["source_path"], arcname=name, recursive=False)
        for name, data in extra_members.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            tf.addfile(info, io.BytesIO(data))
    with tarfile.open(output, "r:gz") as tf:
        names = set(tf.getnames())
        if names != set(manifest.files) | set(extra_members):
            raise ValueError("archive members differ from the manifest")
        for name, entry in manifest.files.items():
            if hashlib.sha256(tf.extractfile(name).read()).hexdigest() != entry["sha256"]:
                raise ValueError(f"archived hash mismatch: {name}")
    return dict(path=str(output), sha256=receipts.digest(output), bytes=output.stat().st_size,
                payload_files=len(manifest.files))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("verdict")
    ap.add_argument("--output", required=True)
    ap.add_argument("--base", help="archive names are relative to this (default: parent of the work directory)")
    ap.add_argument("--repro", action="append", default=[])
    ap.add_argument("--extra", action="append", default=[])
    ap.add_argument("--note", default="")
    a = ap.parse_args(argv)
    try:
        verdict = receipts.read_json(a.verdict)
        output = Path(a.output).resolve()
        if output.exists():
            raise ValueError(f"refusing to overwrite {output}")
        manifest = Manifest(a.base or Path(verdict["work"]).parent)
        verified = collect(verdict, manifest, repro=[Path(r) for r in a.repro], extras=[Path(e) for e in a.extra])
        info = dict(created_utc=datetime.now(timezone.utc).isoformat(), source_base=str(manifest.base),
                    verdict=verdict["verdict"], validation_head=verdict["validation_head"],
                    physical_build_head=head(verdict["run"]),
                    reproduction_heads={str(r): head(r) for r in a.repro},
                    receipts=verified, files=manifest.files)
        members = {"MANIFEST.json": (json.dumps(info, indent=2, sort_keys=True) + "\n").encode(),
                   "README.txt": readme(verdict, manifest, verified, a.repro, a.note).encode()}
        result = write_archive(output, manifest, members)
        result["verified_receipts"] = len(verified)
        status = Path(verdict["work"]) / "archive-status.json"
        receipts.write_new(status, result)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"archive failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
