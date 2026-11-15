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
  the two generated configs and the receipt itself after generation. No
  bytecode or __pycache__ entry is owned at start (a .pyc that copies its
  source's mtime and size in its header is loaded instead of the source);
  after generation, bytecode is owned only if it was not there at start and
  belongs to a tracked .py (compiled from the clean sources), and it is
  listed as "compiled";
* tt/'s commit and the same status, clean (flow/harden.sh copies tt/ in
  without bytecode), and after generation clean apart from bytecode compiled
  since start for its tracked modules;
* the LibreLane image's ID (docker image inspect, not only its version
  string) and the PDK checkout's commit and status (clean apart from the
  SOURCES file its install script writes);
* the sha256 of src/user_config.json and src/config_merged.json;
* bytes, not only status (100c): every file of the commit's tree (git
  ls-tree, with GIT_NO_REPLACE_OBJECTS=1) hashed as git would store it
  (no filters) against its blob, absent files included, in the worktree
  and in tt/ against the pinned tt commit's tree: an assume-unchanged or
  skip-worktree entry, a smudge filter, a sparse checkout or a git replace
  that status does not show is DIRTY;
* the outer process: PYTHONPATH and LIBRELANE_IMAGE_OVERRIDE (either set is
  DIRTY; flow/harden.sh refuses them), a digest of the flow venv's installed
  distributions (each dist-info RECORD), and any librelane_plugin_*/
  openlane_plugin_* module on the outer sys.path (DIRTY: the worktree's own
  plugin is the only one).

A dirty tree still hardens (experiments need that); the receipt then says
DIRTY and why. flow/release_check.py --shipping-root fails the release unless
the receipt is CLEAN and belongs to this run (its path, tree and HEAD); the
run worktree is still that tree, byte for byte, with nothing but the harden's
outputs added; its tree equals the clean shipping checkout's HEAD^{tree};
tt, image and PDK equal flow/release_pins.json in that tree, and exp.log's
container command names the receipt's image; and the configs regenerated
from the shipping tree with that tt equal both the generated record and the
run's own src/config_merged.json and src/user_config.json, the files
LibreLane read (check_release).

Why this closes reads that no file list sees (097g: a Tcl `source` of a
sibling, an extra root-level LibreLane plugin, an info.yaml source_files entry
under tt/): every repository file the flow can read is either in the
committed tree, which the tree hash covers whatever reads it, or in tt/ at
the pinned commit with no untracked or ignored extras, or generated from those
two (and checked by regeneration). An uncommitted change makes the receipt
DIRTY; a committed one makes a different tree. Not covered (the verifier's
list in item 100, finding 3): files a step creates during the run (they
derive from the inputs above, the image and the PDK); reads outside the
worktree other than the PDK and the image by committed code (reviewable in
the tree; config paths there are refused by gen_config.py and the manifest,
and the container passes only PDK_ROOT, PDK and DISPLAY); the PDK's ignored
files (its status is taken without --ignored; none exist today); bytecode
written after the generated record, such as the plugin's when LibreLane
imports it (compiled from the bound sources); the outer venv's contents
beyond the recorded digest (recorded, not pinned); and the seconds between
the generated record and LibreLane's start, inside one script.
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
# what the run worktree may hold at release besides its tree: the harden's outputs
RUN_OWNED_RE = re.compile(r"^(\?\?|!!) (exp\.log|result\.txt|runs/.*|tt/|src/user_config\.json|src/config_merged\.json|"
                          r"test/sim_build/.*)$")
GIT_ENV = dict(os.environ, GIT_NO_REPLACE_OBJECTS="1")
SCHEMA = "protoemu-clean-tree-1"
PINS = "flow/release_pins.json"
GENERATED = ("src/user_config.json", "src/config_merged.json")
# what the harden owns in its worktree, by phase (porcelain lines)
START_OWNED = {"!! tt/"}
GENERATED_OWNED = START_OWNED | {f"!! {p}" for p in GENERATED} | {f"!! {RECEIPT}"}
PDK_OWNED = {"?? ihp-sg13cmos5l/SOURCES"}
# in a shipping checkout (or a validation clone): the harden's and the release
# tooling's own outputs and links, never tracked content
CHECKOUT_OWNED_RE = re.compile(r"^(!! (tt/|runs/.*|test/sim_build/.*|src/user_config\.json|src/config_merged\.json)|"
                               r"(\?\?|!!) (\.venv-flow(/.*)?|exp\.log|result\.txt))$")
PYC_RE = re.compile(r"^!! ((?:.*/)?)__pycache__/([^/]+)\.cpython-\d+\.pyc$")


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=GIT_ENV)
    if r.returncode != 0:
        raise ValueError(f"git -C {repo} {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def status(repo, ignored=True):
    args = ["status", "--porcelain", "--untracked-files=all"] + (["--ignored"] if ignored else [])
    return sorted(line for line in git(repo, *args).splitlines() if line)


def tracked(repo):
    return set(git(repo, "ls-files", "-z").split("\0")) - {""}


def stray(lines, owned, repo=None, owned_re=None, before=None):
    """The status lines outside what is owned: an exact owned line, a line
    owned_re matches, and, only when repo is given, __pycache__/<stem>.cpython-*.pyc
    whose <stem>.py is tracked in repo and (with before) that was not there
    before. Without repo, any bytecode or __pycache__ entry is a stray: a .pyc
    whose header copies its source's mtime and size is loaded instead of the
    source, so bytecode present at start could carry other code (100b)."""
    files = tracked(repo) if repo is not None else set()
    out = []
    for line in lines:
        if line in owned or (owned_re and owned_re.match(line)):
            continue
        m = PYC_RE.match(line)
        if m and f"{m.group(1)}{m.group(2)}.py" in files and (before is None or line not in before):
            continue
        out.append(line)
    return out


def bytes_differ(repo, treeish):
    """The paths of treeish's files whose bytes in repo's worktree differ from
    their blobs, or that are absent or of another kind, hashed as git stores
    them without filters (GIT_NO_REPLACE_OBJECTS=1): what status misses when
    an index flag, a filter, a sparse checkout or a replace ref hides it."""
    repo = Path(repo)
    fmt = git(repo, "rev-parse", "--show-object-format").strip() or "sha1"
    out = []
    for entry in git(repo, "ls-tree", "-r", "-z", "--full-tree", treeish).split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, kind, blob = meta.split()
        if kind != "blob":
            continue           # a submodule: not a file of this tree
        p = repo / path
        if mode == "120000":
            if not p.is_symlink():
                out.append(f"{path} (not a link)")
                continue
            data = os.readlink(p).encode()
        elif p.is_symlink() or not p.is_file():
            out.append(f"{path} ({'absent' if not os.path.lexists(p) else 'not a file'})")
            continue
        else:
            data = p.read_bytes()
            if os.access(p, os.X_OK) != (mode == "100755"):     # 100d: the executable bit too
                out.append(f"{path} (mode differs from {mode})")
                continue
        h = hashlib.new(fmt, f"blob {len(data)}\0".encode() + data).hexdigest()
        if h != blob and not (mode != "120000" and converted(repo, treeish, path, blob)):
            out.append(f"{path} (bytes differ{'' if mode == '120000' else '; the gate needs a default clone, without core.autocrlf'})")
    return out


def converted(repo, treeish, path, blob):
    """Whether a file whose raw bytes differ is its blob after the end-of-line
    conversion the committed tree's own attributes ask for (text or eol set on
    the path in treeish): a clean checkout writes those converted."""
    attrs = git(repo, "check-attr", f"--source={treeish}", "text", "eol", "--", path)
    if not re.search(r": (text: (set|auto)|eol: (lf|crlf))$", attrs, re.M):
        return False
    return git(repo, "hash-object", f"--path={path}", "--", path).strip() == blob


def pinned_tt(wt):
    """The tt-support-tools commit the worktree's own commit pins (or None)."""
    try:
        return json.loads(git(wt, "show", f"HEAD:{PINS}")).get("tt_support_tools")
    except (ValueError, OSError):
        return None


def venv_digest():
    """A digest of the outer python's installed distributions: each dist-info's
    name and the sha256 of its RECORD (the files and their hashes it installed)."""
    h, n = hashlib.sha256(), 0
    for sp in sorted({p for p in sys.path if p.endswith("site-packages") and os.path.isdir(p)}):
        for d in sorted(Path(sp).glob("*.dist-info")):
            rec = d / "RECORD"
            h.update(f"{d.name}\0{sha256(rec) if rec.is_file() else '-'}\n".encode())
            n += 1
    return {"prefix": sys.prefix, "sha256": h.hexdigest(), "distributions": n}


def outer_plugins(syspath=None):
    """librelane_plugin_*/openlane_plugin_* modules on this python's sys.path (or
    syspath), as LibreLane's plugin discovery would see them outside the worktree."""
    import pkgutil
    here = str(Path(__file__).resolve().parent)
    paths = [p for p in (sys.path if syspath is None else syspath) if p and os.path.realpath(p) != here]
    return sorted(m.name for m in pkgutil.iter_modules(paths)
                  if m.name.startswith(("librelane_plugin_", "openlane_plugin_")))


def compiled_since(lines, before):
    """The bytecode lines not in before (compiled after the start record)."""
    return [line for line in lines if PYC_RE.match(line) and line not in before]


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


def verdict(data, final=True):
    """("CLEAN"|"DIRTY", reasons). At start (final=False) the generated configs
    are not yet expected."""
    reasons = []
    if data["worktree"]["stray"]:
        reasons.append(f"worktree at start: {len(data['worktree']['stray'])} paths not committed: "
                       f"{data['worktree']['stray'][:5]}")
    if data.get("generated_worktree") is not None and data["generated_worktree"]["stray"]:
        reasons.append(f"worktree after generation: {len(data['generated_worktree']['stray'])} paths differ from the "
                       f"commit: {data['generated_worktree']['stray'][:5]}")
    if data.get("generated_tt") and data["generated_tt"]["stray"]:
        reasons.append(f"tt/ after generation: {data['generated_tt']['stray'][:5]}")
    if final and data.get("generated") is None:
        reasons.append("generated configs not recorded")
    for key, where in (("worktree", "worktree at start"), ("generated_worktree", "worktree after generation"),
                       ("tt", "tt/ at start"), ("generated_tt", "tt/ after generation")):
        diff = (data.get(key) or {}).get("bytes_differ")
        if diff:
            reasons.append(f"{where}: {len(diff)} files differ from the tree in bytes: {diff[:5]}")
    for name, value in (data.get("env") or {}).items():
        if value:
            reasons.append(f"{name} was set ({value}): the outer process was not the flow's own")
    if data.get("outer_plugins"):
        reasons.append(f"LibreLane plugins outside the worktree on the outer sys.path: {data['outer_plugins']}")
    tt = data["tt"]
    if tt.get("error") or tt["stray"]:
        reasons.append(f"tt/: {tt.get('error') or tt['stray'][:5]}")
    if not data["image"]["id"]:
        reasons.append(f"image {data['image']['name']}: no local image ID")
    pdk = data["pdk"]
    if pdk.get("error") or pdk["stray"]:
        reasons.append(f"PDK: {pdk.get('error') or pdk['stray'][:5]}")
    return ("DIRTY" if reasons else "CLEAN"), reasons


def start(wt, image, pdk_root, argv=(), ident=None, environ=None, syspath=None):
    """The start record (environ, syspath: the outer process's, for tests that
    run in-process; flow/harden.sh runs this as a script, so they are its own)."""
    environ = os.environ if environ is None else environ
    wt = Path(wt).resolve()
    data = {"schema": SCHEMA, "head": git(wt, "rev-parse", "HEAD").strip(),
            "tree": git(wt, "rev-parse", "HEAD^{tree}").strip(), "argv": list(argv)}
    data["worktree_path"] = str(wt)
    lines = status(wt)      # at start no bytecode or __pycache__ anywhere is owned (100b)
    data["worktree"] = {"status": lines, "stray": stray(lines, START_OWNED),
                        "bytes_differ": bytes_differ(wt, "HEAD^{tree}")}
    data["env"] = {k: environ.get(k) for k in ("PYTHONPATH", "LIBRELANE_IMAGE_OVERRIDE")}
    data["venv"] = venv_digest()
    data["outer_plugins"] = outer_plugins(syspath)
    try:
        tl = status(wt / "tt")
        pin = pinned_tt(wt)
        data["tt"] = {"head": git(wt / "tt", "rev-parse", "HEAD").strip(), "pinned": pin, "status": tl,
                      "stray": stray(tl, set()),
                      "bytes_differ": bytes_differ(wt / "tt", f"{pin or 'HEAD'}^{{tree}}")}
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
    data["status"], data["reasons"] = verdict(data, final=False)
    data["phase"] = "start"
    (wt / "runs").mkdir(exist_ok=True)
    with (wt / RECEIPT).open("x") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    return data


def generated(wt):
    wt = Path(wt).resolve()
    data = json.loads((wt / RECEIPT).read_text())
    lines = status(wt)      # bytecode compiled since start from tracked, clean sources is owned
    start_lines = set(data["worktree"]["status"])
    data["generated_worktree"] = {"status": lines, "compiled": compiled_since(lines, start_lines),
                                  "stray": stray(lines, GENERATED_OWNED, repo=wt, before=start_lines),
                                  "bytes_differ": bytes_differ(wt, "HEAD^{tree}")}
    if data["tt"].get("head"):
        tl = status(wt / "tt")
        tt_start = set(data["tt"]["status"])
        data["generated_tt"] = {"status": tl, "compiled": compiled_since(tl, tt_start),
                                "stray": stray(tl, set(), repo=wt / "tt", before=tt_start),
                                "bytes_differ": bytes_differ(wt / "tt", f"{data['tt'].get('pinned') or 'HEAD'}^{{tree}}")}
    data["generated"] = {p: sha256(wt / p) if (wt / p).is_file() else None for p in GENERATED}
    if None in data["generated"].values():
        data["generated"] = None
    data["status"], data["reasons"] = verdict(data)
    data["phase"] = "generated"
    (wt / RECEIPT).write_text(json.dumps(data, indent=1, sort_keys=True))
    return data


def regenerate(tree_root, tt_dir, python, pdk_root=None):
    """sha256 of the two generated configs, made from tree_root's committed tree
    (git archive HEAD) with tt_dir, as flow/harden.sh makes them."""
    # beside the checkout, not under /tmp: the tt tool's yosys (yowasp) could not
    # read a /tmp tree on runner1 (item 100, finding 1)
    tmp = Path(tempfile.mkdtemp(prefix="clean-tree-regen.", dir=Path(tree_root).resolve().parent))
    try:
        archive = subprocess.run(["git", "-C", str(tree_root), "archive", "HEAD"], capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", str(tmp)], input=archive.stdout, check=True)
        # tt without its bytecode, and none written: the sources alone (100b)
        shutil.copytree(tt_dir, tmp / "tt", symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        env = dict(os.environ, PDK="ihp-sg13cmos5l", PYTHONDONTWRITEBYTECODE="1",
                   PATH=os.pathsep.join([os.path.dirname(os.path.abspath(python)), os.environ.get("PATH", "")]))
        if pdk_root:
            env["PDK_ROOT"] = str(pdk_root)
        r = subprocess.run([python, "-B", "tt/tt_tool.py", "--create-user-config", "--ihp"], cwd=tmp, env=env,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise ValueError(f"tt_tool --create-user-config failed: {(r.stdout + r.stderr).strip()[-300:]}")
        return {p: sha256(tmp / p) for p in GENERATED}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def container_image(run):
    """The image LibreLane's container command ran (exp.log's line after
    "Running containerized command:"), or None."""
    log = Path(run) / "exp.log"
    if not log.is_file():
        return None
    lines = log.read_text(errors="replace").splitlines()
    for k, line in enumerate(lines):
        if "Running containerized command" in line and k + 1 < len(lines):
            words = lines[k + 1].split()
            for w in words:
                if re.fullmatch(r"(\S+/)?\S*librelane\S*:\S+|sha256:[0-9a-f]{64}", w) and not w.startswith("-"):
                    return w
    return None


def check_release(run, shipping, python):
    """(failures, notes) for the release: the run's clean-tree receipt, the run
    worktree as it is now, a clean shipping checkout and its pins."""
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
    # the receipt belongs to this run, and the run is still that tree (100c, finding 3f)
    if data.get("worktree_path") != str(run):
        failures.append(f"the receipt was recorded in {data.get('worktree_path')}, not in this run ({run})")
    try:
        run_tree = git(run, "rev-parse", "HEAD^{tree}").strip()
        if run_tree != data["tree"] or git(run, "rev-parse", "HEAD").strip() != data["head"]:
            failures.append(f"the run worktree is at tree {run_tree}, not the receipt's {data['tree']}")
        start_lines = set(data["worktree"]["status"])
        extra = [line for line in stray(status(run), set(), repo=run, owned_re=RUN_OWNED_RE, before=start_lines)]
        if extra:
            failures.append(f"the run worktree holds more than its tree and the harden's outputs: {extra[:5]}")
        diff = bytes_differ(run, data["tree"])
        if diff:
            failures.append(f"the run worktree differs from its tree in bytes: {diff[:5]}")
    except (ValueError, OSError) as e:
        failures.append(f"the run worktree: {e}")
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
    diff = bytes_differ(shipping, "HEAD^{tree}")
    if diff:
        failures.append(f"the shipping checkout differs from its tree in bytes: {diff[:5]}")
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
    used = container_image(run)        # finding 3b: the image LibreLane actually ran
    if used is None:
        failures.append("exp.log names no container image (no \"Running containerized command\" line)")
    elif used not in (data["image"].get("name"), data["image"].get("id")):
        failures.append(f"LibreLane ran image {used}, not the receipt's {data['image'].get('name')}")
    else:
        notes.append(f"LibreLane ran {used}")
    tt_dir = shipping / "tt" if (shipping / "tt").is_dir() else run / "tt"
    try:
        pin = pins.get("tt_support_tools")
        tl = status(tt_dir)
        if git(tt_dir, "rev-parse", "HEAD").strip() != pin or stray(tl, set(), repo=tt_dir) \
                or bytes_differ(tt_dir, f"{pin}^{{tree}}"):
            failures.append(f"{tt_dir}: not a clean checkout of the pinned tt-support-tools")
        elif data.get("generated"):
            regen = regenerate(shipping, tt_dir, python, data["pdk"].get("root"))
            # the config LibreLane read (finding 3e): the run's own files, as tt_tool --harden left them
            run_files = {f: sha256(run / f) if (run / f).is_file() else None for f in GENERATED}
            if regen != data["generated"]:
                failures.append(f"generated configs differ: the run's {data['generated']}, regenerated {regen}")
            elif run_files != regen:
                failures.append(f"the configs LibreLane read differ from the regenerated ones: {run_files}")
            else:
                notes.append("generated configs: regenerated from the shipping tree, equal to the record and to the "
                             "files LibreLane read")
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
    print(f"clean tree ({data.get('phase', a.phase)}): {data['status']} (commit {data['head'][:12]}, "
          f"tree {data['tree'][:12]})")
    for r in data["reasons"]:
        print(f"clean tree: {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
