#!/usr/bin/env python3
"""The clean-tree receipt: the release identity of a harden.

    flow/clean_tree.py start WORKTREE --image IMAGE --pdk-root DIR [--argv ...]
    flow/clean_tree.py generated WORKTREE
    flow/clean_tree.py show WORKTREE

flow/harden.sh calls `start` in the fresh run worktree right after copying
tt-support-tools in and before anything is generated, and `generated` after
gen_config.py and the tt tool have written src/config.json, src/user_config.json
and src/config_merged.json, before LibreLane starts. The receipt
(runs/clean-tree.json) records:

* the worktree's commit and tree (git rev-parse HEAD, HEAD^{tree}) and its
  `git status --porcelain --ignored --untracked-files=all`, which must be
  empty apart from what the harden owns at that point: tt/ at start; tt/,
  the two generated configs and the receipt itself after generation;
* tt/'s commit and the same status, clean apart from
  __pycache__/<stem>.cpython-*.pyc of a tracked <stem>.py;
* the LibreLane image's ID (docker image inspect, not only its version
  string) and the PDK checkout's commit and status (clean apart from the
  SOURCES file its install script writes);
* the sha256 of src/user_config.json and src/config_merged.json.

A dirty tree still hardens (experiments need that); the receipt then says
DIRTY and why. flow/release_check.py --shipping-root fails the release unless
the receipt is CLEAN, its tree equals the clean shipping checkout's
HEAD^{tree}, tt, image and PDK equal flow/release_pins.json in that tree, and
the configs regenerated from the shipping tree with that tt equal the run's
(check_release).

Why this closes reads that no file list sees (097g: a Tcl `source` of a
sibling, an extra root-level LibreLane plugin, an info.yaml source_files entry
under tt/): every repository file the flow can read is either in the
committed tree, which the tree hash covers whatever reads it, or in tt/ at
the pinned commit with no untracked or ignored extras, or generated from those
two (and checked by regeneration). An uncommitted change makes the receipt
DIRTY; a committed one makes a different tree. Not covered: files a step
creates during the run (they derive from the inputs above, the image and the
PDK), reads outside the worktree other than the PDK and the image (the
container mounts the user's home directory; LibreLane's own paths resolve
under the worktree, the PDK root and the image), and the PDK's ignored files
(its status is taken without --ignored; it is a pinned git checkout read
only).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

RECEIPT = "runs/clean-tree.json"
SCHEMA = "protoemu-clean-tree-1"
PINS = "flow/release_pins.json"
GENERATED = ("src/user_config.json", "src/config_merged.json")
# what the harden owns in its worktree, by phase (porcelain lines)
START_OWNED = {"!! tt/"}
GENERATED_OWNED = START_OWNED | {f"!! {p}" for p in GENERATED} | {f"!! {RECEIPT}"}
PDK_OWNED = {"?? ihp-sg13cmos5l/SOURCES"}
# in a shipping checkout (or a validation clone): the harden's and the release
# tooling's own outputs and links, never tracked content
CHECKOUT_OWNED_RE = re.compile(r"^!! (tt/|\.venv-flow|runs/.*|test/sim_build/.*|exp\.log|result\.txt|"
                               r"src/user_config\.json|src/config_merged\.json)$")
PYC_RE = re.compile(r"^!! ((?:.*/)?)__pycache__/([^/]+)\.cpython-\d+\.pyc$")


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise ValueError(f"git -C {repo} {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def status(repo, ignored=True):
    args = ["status", "--porcelain", "--untracked-files=all"] + (["--ignored"] if ignored else [])
    return sorted(line for line in git(repo, *args).splitlines() if line)


def tracked(repo):
    return set(git(repo, "ls-files", "-z").split("\0")) - {""}


def stray(lines, owned, repo=None, owned_re=None):
    """The status lines outside what is owned: an exact owned line, a line
    owned_re matches, or __pycache__/<stem>.cpython-*.pyc whose <stem>.py is
    tracked in repo."""
    files = tracked(repo) if repo is not None else set()
    out = []
    for line in lines:
        if line in owned or (owned_re and owned_re.match(line)):
            continue
        m = PYC_RE.match(line)
        if m and f"{m.group(1)}{m.group(2)}.py" in files:
            continue
        out.append(line)
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for part in iter(lambda: f.read(1 << 20), b""):
            h.update(part)
    return h.hexdigest()


def image_id(image):
    r = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image], capture_output=True, text=True)
    ident = r.stdout.strip()
    return ident if r.returncode == 0 and re.fullmatch(r"sha256:[0-9a-f]{64}", ident) else None


def verdict(data):
    reasons = []
    if data["worktree"]["stray"]:
        reasons.append(f"worktree at start: {len(data['worktree']['stray'])} paths not committed: "
                       f"{data['worktree']['stray'][:5]}")
    if data.get("generated_worktree") is not None and data["generated_worktree"]["stray"]:
        reasons.append(f"worktree after generation: {len(data['generated_worktree']['stray'])} paths differ from the "
                       f"commit: {data['generated_worktree']['stray'][:5]}")
    if data.get("generated") is None:
        reasons.append("generated configs not recorded")
    tt = data["tt"]
    if tt.get("error") or tt["stray"]:
        reasons.append(f"tt/: {tt.get('error') or tt['stray'][:5]}")
    if not data["image"]["id"]:
        reasons.append(f"image {data['image']['name']}: no local image ID")
    pdk = data["pdk"]
    if pdk.get("error") or pdk["stray"]:
        reasons.append(f"PDK: {pdk.get('error') or pdk['stray'][:5]}")
    return ("DIRTY" if reasons else "CLEAN"), reasons


def start(wt, image, pdk_root, argv=(), ident=None):
    wt = Path(wt).resolve()
    data = {"schema": SCHEMA, "head": git(wt, "rev-parse", "HEAD").strip(),
            "tree": git(wt, "rev-parse", "HEAD^{tree}").strip(), "argv": list(argv)}
    lines = status(wt)
    data["worktree"] = {"status": lines, "stray": stray(lines, START_OWNED)}
    try:
        tl = status(wt / "tt")
        data["tt"] = {"head": git(wt / "tt", "rev-parse", "HEAD").strip(), "status": tl,
                      "stray": stray(tl, set(), repo=wt / "tt")}
    except (ValueError, OSError) as e:
        data["tt"] = {"head": None, "status": [], "stray": [], "error": str(e)}
    data["image"] = {"name": image, "id": ident or image_id(image)}   # ident: tests only (no docker)
    try:
        pl = status(pdk_root, ignored=False)
        data["pdk"] = {"root": str(pdk_root), "head": git(pdk_root, "rev-parse", "HEAD").strip(), "status": pl,
                       "stray": [line for line in pl if line not in PDK_OWNED]}
    except (ValueError, OSError) as e:
        data["pdk"] = {"root": str(pdk_root), "head": None, "status": [], "stray": [], "error": str(e)}
    data["generated"] = data["generated_worktree"] = None
    data["status"], data["reasons"] = verdict(data)
    (wt / "runs").mkdir(exist_ok=True)
    with (wt / RECEIPT).open("x") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    return data


def generated(wt):
    wt = Path(wt).resolve()
    data = json.loads((wt / RECEIPT).read_text())
    lines = status(wt)
    data["generated_worktree"] = {"status": lines, "stray": stray(lines, GENERATED_OWNED)}
    data["generated"] = {p: sha256(wt / p) if (wt / p).is_file() else None for p in GENERATED}
    if None in data["generated"].values():
        data["generated"] = None
    data["status"], data["reasons"] = verdict(data)
    (wt / RECEIPT).write_text(json.dumps(data, indent=1, sort_keys=True))
    return data


def regenerate(tree_root, tt_dir, python, pdk_root=None):
    """sha256 of the two generated configs, made from tree_root's committed tree
    (git archive HEAD) with tt_dir, as flow/harden.sh makes them."""
    tmp = Path(tempfile.mkdtemp(prefix="clean-tree-regen."))
    try:
        archive = subprocess.run(["git", "-C", str(tree_root), "archive", "HEAD"], capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", str(tmp)], input=archive.stdout, check=True)
        shutil.copytree(tt_dir, tmp / "tt", symlinks=True)
        env = dict(os.environ, PDK="ihp-sg13cmos5l")
        if pdk_root:
            env["PDK_ROOT"] = str(pdk_root)
        r = subprocess.run([python, "tt/tt_tool.py", "--create-user-config", "--ihp"], cwd=tmp, env=env,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise ValueError(f"tt_tool --create-user-config failed: {(r.stdout + r.stderr).strip()[-300:]}")
        return {p: sha256(tmp / p) for p in GENERATED}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_release(run, shipping, python):
    """(failures, notes) for the release: the run's clean-tree receipt against
    a clean shipping checkout and its pins."""
    run, shipping = Path(run).resolve(), Path(shipping).resolve()
    failures, notes = [], []
    p = run / RECEIPT
    if not p.is_file():
        return [f"no clean-tree receipt ({RECEIPT}): the run was not hardened by a flow/harden.sh that records one"], notes
    data = json.loads(p.read_text())
    if data.get("schema") != SCHEMA:
        return [f"{RECEIPT}: not a clean-tree receipt"], notes
    st, reasons = verdict(data)
    if st != "CLEAN" or data.get("status") != "CLEAN":
        failures += [f"the run's tree was DIRTY: {r}" for r in reasons] or ["the receipt says DIRTY"]
    try:
        ship_tree = git(shipping, "rev-parse", "HEAD^{tree}").strip()
        ship_head = git(shipping, "rev-parse", "HEAD").strip()
        lines = status(shipping)
    except (ValueError, OSError) as e:
        return failures + [f"shipping checkout {shipping}: {e}"], notes
    if data["tree"] != ship_tree:
        failures.append(f"the run's tree {data['tree']} (commit {data['head'][:12]}) is not the shipping checkout's "
                        f"{ship_tree} (commit {ship_head[:12]})")
    else:
        notes.append(f"tree {ship_tree}: equal (run commit {data['head'][:12]}, shipping commit {ship_head[:12]})")
    dirty = stray(lines, set(), repo=shipping, owned_re=CHECKOUT_OWNED_RE)
    if dirty:
        failures.append(f"the shipping checkout is not clean: {dirty[:5]}")
    try:
        pins = json.loads(git(shipping, "show", f"HEAD:{PINS}"))
    except (ValueError, OSError) as e:
        return failures + [f"no {PINS} in the shipping commit: {e}"], notes
    for key, have in (("tt_support_tools", data["tt"].get("head")), ("librelane_image", data["image"].get("id")),
                      ("pdk", data["pdk"].get("head"))):
        if have != pins.get(key):
            failures.append(f"{key}: the run used {have}, the shipping commit pins {pins.get(key)}")
        else:
            notes.append(f"{key}: {have} (pinned)")
    tt_dir = shipping / "tt" if (shipping / "tt").is_dir() else run / "tt"
    try:
        tl = status(tt_dir)
        if git(tt_dir, "rev-parse", "HEAD").strip() != pins.get("tt_support_tools") or stray(tl, set(), repo=tt_dir):
            failures.append(f"{tt_dir}: not a clean checkout of the pinned tt-support-tools")
        elif data.get("generated"):
            regen = regenerate(shipping, tt_dir, python, data["pdk"].get("root"))
            if regen != data["generated"]:
                failures.append(f"generated configs differ: the run's {data['generated']}, regenerated {regen}")
            else:
                notes.append("generated configs: regenerated from the shipping tree, equal")
    except (ValueError, OSError, subprocess.CalledProcessError) as e:
        failures.append(f"generated configs: {e}")
    return failures, notes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=("start", "generated", "show"))
    ap.add_argument("worktree")
    ap.add_argument("--image")
    ap.add_argument("--pdk-root")
    ap.add_argument("--argv", nargs=argparse.REMAINDER, default=[])
    a = ap.parse_args(argv)
    if a.phase == "start":
        if not a.image or not a.pdk_root:
            ap.error("start needs --image and --pdk-root")
        data = start(a.worktree, a.image, a.pdk_root, a.argv)
    elif a.phase == "generated":
        data = generated(a.worktree)
    else:
        data = json.loads((Path(a.worktree) / RECEIPT).read_text())
    print(f"clean tree: {data['status']} (commit {data['head'][:12]}, tree {data['tree'][:12]})")
    for r in data["reasons"]:
        print(f"clean tree: {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
