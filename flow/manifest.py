#!/usr/bin/env python3
"""Bind verification to actual file contents, including uncommitted team fixes.

    python flow/manifest.py --output test/sim_build/candidate.json --pdk-root ~/ttpdk
    python flow/manifest.py --verify test/sim_build/candidate.json

The input manifest is immutable: result tools write separate result manifests.
"""
import argparse
import datetime
import hashlib
import importlib.metadata
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources(root=ROOT):
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                                    cwd=root, text=True).splitlines()
    return {n: sha(root / n) for n in sorted(set(names)) if (root / n).is_file()
            and (n.startswith(("src/", "firmware/", "tools/", "test/", "formal/", "flow/", ".github/"))
                 or n in ("info.yaml", "requirements.txt")
                 or ("/" not in n and n.startswith("librelane_plugin_") and n.endswith(".py")))}


def identity(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def physical_flow_files(files):
    """Flow implementation is part of the candidate, separate from its RTL."""
    return {n: h for n, h in files.items() if n.startswith("flow/")
            or ("/" not in n and n.startswith("librelane_plugin_") and n.endswith(".py"))}


def snapshot(repo, work):
    """Copy actual team edits into a new worktree, rejecting concurrent edits."""
    import shutil
    repo, work = pathlib.Path(repo), pathlib.Path(work)
    before = sources(repo)
    for name in before:
        dst = work / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / name, dst)
    for name in sources(work).keys() - before.keys():
        (work / name).unlink()  # candidate-deleted inputs in the new worktree only
    if sources(repo) != before or sources(work) != before:
        raise ValueError("source changed while snapshotting; isolated snapshot retained, do not harden it")


def hardening_files(root):
    """Bind the generated TT configuration and actual support-tool bytes."""
    root = pathlib.Path(root)
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                                    cwd=root / "tt", text=True).splitlines()
    paths = [root / "tt" / n for n in sorted(set(names)) if (root / "tt" / n).is_file()]
    if not paths:
        raise ValueError("no Tiny Tapeout support inputs found")
    for name in ("src/user_config.json", "src/config_merged.json"):
        path = root / name
        if not path.is_file():
            raise ValueError(f"missing generated hardening input: {name}")
        paths.append(path)
    return {str(p.relative_to(root)): sha(p) for p in paths}


def capture(pdk_root=None, hardening=False):
    files = sources()
    result = dict(schema=2, created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  source_tree_sha256=identity(files), files=files)
    result["hardware_files"] = {n: h for n, h in files.items() if n.startswith("src/") or n == "info.yaml"}
    result["hardware_sha256"] = identity(result["hardware_files"])
    result["physical_flow_files"] = physical_flow_files(files)
    result["physical_flow_sha256"] = identity(result["physical_flow_files"])
    if hardening:
        result["hardening_files"] = hardening_files(ROOT)
        result["hardening_sha256"] = identity(result["hardening_files"])
    result["generator"] = json.loads((ROOT / "src/config.json").read_text()).get("//generator")
    result["tools"] = {}
    for name in ("librelane", "cocotb", "pytest"):
        try:
            result["tools"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result["tools"][name] = None
    image = "ghcr.io/librelane/librelane:3.1.0.dev3"
    inspect = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                             capture_output=True, text=True)
    result["tools"]["librelane_image"] = dict(name=image, id=inspect.stdout.strip() if inspect.returncode == 0 else None)
    if (ROOT / "tt").exists():
        revision = subprocess.run(["git", "-C", str(ROOT / "tt"), "rev-parse", "HEAD"], capture_output=True, text=True)
        result["tools"]["tt_support_tools_commit"] = revision.stdout.strip() if revision.returncode == 0 else None
    if pdk_root:
        pdk = pathlib.Path(pdk_root).expanduser() / "ihp-sg13cmos5l"
        result["pdk"] = {str(p.relative_to(pdk)): sha(p) for p in sorted(pdk.rglob("*"))
                         if p.is_file() and p.suffix in (".lib", ".lef", ".json")}
        if not result["pdk"]:
            raise ValueError("no PDK timing/LEF files found")
    return result


def verify_physical_inputs(candidate, candidate_root):
    """Reject legacy/unbound or changed flow code in the hardened worktree.

    Validation software may be newer than the physical candidate. Compare flow
    inputs with that candidate's own checkout, never with the validator's root.
    """
    expected = candidate.get("physical_flow_files")
    if not isinstance(expected, dict) or not expected:
        return "candidate has no pre-run physical-flow provenance"
    if physical_flow_files(candidate.get("files", {})) != expected or identity(expected) != candidate.get("physical_flow_sha256"):
        return "invalid candidate physical-flow identity"
    if physical_flow_files(sources(candidate_root)) != expected:
        return "physical-flow/plugin hashes changed in hardened candidate"
    runtime = candidate.get("hardening_files")
    if not isinstance(runtime, dict) or not runtime or identity(runtime) != candidate.get("hardening_sha256"):
        return "candidate has no valid pre-run TT/generated-config provenance"
    if hardening_files(candidate_root) != runtime:
        return "TT/generated-config hashes changed in hardened candidate"
    return None


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=pathlib.Path)
    mode.add_argument("--verify", type=pathlib.Path)
    ap.add_argument("--pdk-root")
    ap.add_argument("--hardening-candidate", action="store_true",
                    help="also require and bind generated configuration and TT support inputs")
    a = ap.parse_args()
    if a.verify:
        manifest = json.loads(a.verify.read_text())
        if sources() != manifest["files"]:
            raise SystemExit("candidate sources changed since manifest capture")
        if identity(manifest["files"]) != manifest["source_tree_sha256"]:
            raise SystemExit("invalid manifest source identity")
        if "hardening_files" in manifest:
            error = verify_physical_inputs(manifest, ROOT)
            if error:
                raise SystemExit(error)
        print("candidate source hashes match")
    else:
        if a.output.exists():
            raise SystemExit("refusing to replace an existing candidate manifest")
        a.output.write_text(json.dumps(capture(a.pdk_root, a.hardening_candidate), indent=2) + "\n")
        print(f"captured {a.output}")


if __name__ == "__main__":
    main()
