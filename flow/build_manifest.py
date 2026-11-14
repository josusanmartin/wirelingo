#!/usr/bin/env python3
"""The shipping-build identity of a hardened run or of a checkout, and their comparison.

    flow/build_manifest.py write RUN [--out FILE]   # after a harden: RUN/build-manifest.json by default
    flow/build_manifest.py show PATH                # a run, a checkout or a manifest file
    flow/build_manifest.py compare A B [--require-tools]   # exit 1 when they differ

A manifest binds what a Tiny Tapeout build of the design reads, by SHA-256:
the settings and sources under src/ (the generated config.json and the
project.v defines as the run used them, the SDCs, the PDN and corner Tcl, the
RTL and the macro models), info.yaml's build keys (tiles, top module, source
files, language, clock; not the datasheet text), the generator
(flow/gen_config.py: recorded, but a difference is only noted, since no build
runs it), the LibreLane plugin and the flow scripts it executes (each script
the plugin joins to HERE or names, and the Tcl those source; and every flow/
file that src/config.json names, such as a PnR SDC), and the tools: the tt-support-tools tree (tt/, as flow/harden.sh copies it in;
its git commit when tt/.git is there), the LibreLane version (a run's
resolved.json) and the PDK's SOURCES line. A run's manifest also binds its
final netlist and its GDS with the BGNLIB/BGNSTR timestamps zeroed, the
normalization under which a clean rebuild reproduces it (docs/physical.md).

flow/release_check.py --shipping-root CHECKOUT requires the run's build
inputs to equal the checkout's: a run hardened with other settings, scripts or
RTL than the shipping commit's is not that commit's build, however good its
receipts. Firmware, tools and tests are not build inputs, so validating new
firmware on a pinned netlist (--validation-root) stays a separate operation.
`compare` with two builds' manifests (the receipted run's and a CI or
reproduction build's) also compares the tools, the netlist and the GDS.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SCHEMA = 1
PLUGIN = "librelane_plugin_protoemu.py"
BUILD_INPUT_GLOBS = (PLUGIN, "flow/gen_config.py", "src/**/*")
# info.yaml's keys the build reads; the title, description and pinout are datasheet text
INFO = "info.yaml"
INFO_BUILD_KEYS = ("language", "clock_hz", "tiles", "top_module", "source_files", "analog_pins", "uses_3v3")
PLUGIN_SCRIPT_RE = r'os\.path\.join\(HERE, "([^"]+)"\)'
PLUGIN_NAMED_RE = r"\b([A-Za-z0-9_]+\.(?:tcl|py))\b"
GENERATOR = "flow/gen_config.py"
TCL_SOURCE_RE = r"^\s*source \[file join \[file dirname \[info script\]\] (\S+)\]"
# written by tt_tool.py --create-user-config from config.json and info.yaml at build time
GENERATED = {"src/user_config.json", "src/config_merged.json"}
# read by the signoff STA only (STA_EXTRA_CORNER_TCL_FILE); everything else shapes the layout
SIGNOFF_ONLY = {"src/timing_checks.tcl"}
SKIP_PARTS = {"__pycache__", ".git", ".pytest_cache"}
GDS_TIMESTAMPED = (0x0102, 0x0502)     # BGNLIB and BGNSTR: two 6-word dates each


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(1 << 20), b""):
            h.update(part)
    return h.hexdigest()


def executed_scripts(root):
    """The flow/ scripts the plugin runs (os.path.join(HERE, ...)) and the Tcl
    they source next to themselves, transitively. A missing one is still named,
    so that the comparison reports it."""
    root = Path(root)
    plugin = root / PLUGIN
    text = plugin.read_text() if plugin.is_file() else ""
    todo = re.findall(PLUGIN_SCRIPT_RE, text)
    # a script the plugin names any other way: any flow/ file named in it
    todo += [name for name in re.findall(PLUGIN_NAMED_RE, text) if (root / "flow" / name).is_file()]
    seen = set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        path = root / "flow" / name
        if path.suffix == ".tcl" and path.is_file():
            todo += re.findall(TCL_SOURCE_RE, path.read_text(), re.M)
    return sorted(f"flow/{name}" for name in seen)


# Config path allowlist (097b): dir:: paths without glob characters that
# resolve inside the worktree are build inputs; pdk_dir:: paths without ".."
# are tool inputs (the PDK's SOURCES line is recorded under tools); every other
# path-like string is refused. The generator writes only these two forms.
PATH_SUFFIXES = (".sdc", ".tcl", ".v", ".sv", ".vh", ".svh", ".lef", ".lib", ".gds", ".def", ".odb", ".json",
                 ".py", ".cdl", ".spice", ".spef", ".mag", ".yaml", ".yml", ".txt", ".cfg", ".lyp", ".lydrc")
GLOB_CHARS = set("*?[]")
CELL_PIN_RE = r"^[A-Za-z0-9_]+/[A-Za-z0-9_]+$"


def config_path_strings(config):
    """(key, string) for the path-like strings of a LibreLane config: any string
    with a "/" or ending in a known file suffix, except "cell/pin" values of
    *_CELL keys (DIODE_CELL). Comment keys ("//...") are skipped."""
    out = []

    def walk(key, v):
        if isinstance(v, dict):
            for k, x in v.items():
                if not str(k).startswith("//"):
                    walk(key or k, x)
        elif isinstance(v, list):
            for x in v:
                walk(key, x)
        elif isinstance(v, str) and ("/" in v or v.lower().endswith(PATH_SUFFIXES)):
            if str(key).endswith(("_CELL", "_CELLS")) and re.match(CELL_PIN_RE, v):
                return
            out.append((key, v))
    walk(None, config)
    return out


def config_paths(root):
    """(bound, refused) for src/config.json's path-like strings, by the allowlist
    above: a dir:: path is resolved against src/ (normalized, symlinks
    followed); a file inside the worktree is bound by its path relative to the
    resolved worktree root, a directory binds every file under it (sorted, each
    resolved and inside the worktree). Refused: a dir:: path with glob characters,
    or missing, or resolving outside the worktree; pdk_dir:: with ".."; and any
    other path-like string (unprefixed, refg::, absolute)."""
    root = Path(root)
    try:
        config = json.loads((root / "src/config.json").read_text())
    except (OSError, ValueError):
        return {}, []
    real_root = os.path.realpath(root)
    inside = lambda p: os.path.commonpath([real_root, p]) == real_root
    src = os.path.join(real_root, "src")
    bound, refused = {}, []
    for _, s in config_path_strings(config):
        if s.startswith("pdk_dir::"):
            rest = s[len("pdk_dir::"):]
            if os.path.isabs(rest) or ".." in Path(rest).parts:
                refused.append(s)
            continue
        if not s.startswith("dir::") or GLOB_CHARS & set(s):
            refused.append(s)
            continue
        rel = s[len("dir::"):]
        real = os.path.realpath(os.path.join(src, rel))
        if os.path.isabs(rel) or not inside(real) or not os.path.exists(real):
            refused.append(s)
            continue
        files = [real] if os.path.isfile(real) else sorted(
            os.path.realpath(os.path.join(d, f)) for d, _, names in os.walk(real, followlinks=True) for f in names)
        if not all(inside(f) for f in files):
            refused.append(s)
            continue
        for f in files:
            bound[os.path.relpath(f, real_root)] = digest(f)
    return dict(sorted(bound.items())), sorted(set(refused))


def build_inputs(root):
    root = Path(root)
    files = {}
    for pattern in BUILD_INPUT_GLOBS:
        for p in sorted(root.glob(pattern)):
            rel = p.relative_to(root)
            if p.is_file() and str(rel) not in GENERATED and not SKIP_PARTS.intersection(rel.parts) \
                    and p.suffix != ".pyc":
                files[str(rel)] = digest(p)
    for name in executed_scripts(root):
        files[name] = digest(root / name) if (root / name).is_file() else "missing"
    for name, value in config_paths(root)[0].items():
        files.setdefault(name, value)
    if (root / INFO).is_file():
        text = info_build_keys((root / INFO).read_text())
        files[f"{INFO} ({', '.join(INFO_BUILD_KEYS)})"] = hashlib.sha256(text.encode()).hexdigest()
    return dict(sorted(files.items()))


def info_build_keys(text):
    """The lines of info.yaml's project: keys that the build reads, comments stripped."""
    out, in_project, keep = [], False, False
    for line in text.splitlines():
        if re.match(r"^\S", line):
            in_project, keep = line.startswith("project:"), False
            continue
        key = re.match(r"^  (\w+):", line)
        if key:
            keep = in_project and key[1] in INFO_BUILD_KEYS
        elif not re.match(r"^   ", line):
            keep = False                    # a blank or comment line ends a list
        if keep:
            out.append(re.sub(r"\s+#.*$", "", line).rstrip())
    return "\n".join(out) + "\n"


def git_commit(tree):
    """The commit a copied git checkout is at, read from its .git directory (no git needed)."""
    git = Path(tree) / ".git"
    if not git.is_dir() or not (git / "HEAD").is_file():
        return None
    head = (git / "HEAD").read_text().strip()
    if re.fullmatch(r"[0-9a-f]{40}", head):
        return head
    ref = head.removeprefix("ref: ").strip()
    if (git / ref).is_file():
        return (git / ref).read_text().strip()
    packed = git / "packed-refs"
    if packed.is_file():
        for line in packed.read_text().splitlines():
            if line.endswith(" " + ref):
                return line.split()[0]
    return None


def tree_digest(tree):
    tree = Path(tree)
    h, n = hashlib.sha256(), 0
    for p in sorted(tree.rglob("*")):
        rel = p.relative_to(tree)
        if p.is_file() and not SKIP_PARTS.intersection(rel.parts) and p.suffix != ".pyc":
            h.update(f"{rel}\0{digest(p)}\n".encode())
            n += 1
    return h.hexdigest(), n


def tools_of(root, librelane_version=None, pdk=None):
    tt = Path(root) / "tt"
    tt_id = None
    if tt.is_dir():
        tree, n = tree_digest(tt)
        tt_id = dict(commit=git_commit(tt), tree=tree, files=n)
    return dict(tt_support_tools=tt_id, librelane_version=librelane_version, pdk=pdk)


def normalized_gds_digest(path):
    """SHA-256 of a GDSII stream with its BGNLIB/BGNSTR timestamps zeroed."""
    data = bytearray(Path(path).read_bytes())
    i = 0
    while i + 4 <= len(data):
        length = int.from_bytes(data[i:i + 2], "big")
        kind = int.from_bytes(data[i + 2:i + 4], "big")
        if length < 4:
            break
        if kind in GDS_TIMESTAMPED:
            data[i + 4:i + length] = bytes(length - 4)
        i += length
    return hashlib.sha256(data).hexdigest()


def one(paths, what):
    paths = list(paths)
    if len(paths) != 1:
        raise ValueError(f"expected one {what}, found {len(paths)}")
    return paths[0]


def pdk_sources(resolved):
    root, name = resolved.get("PDK_ROOT"), resolved.get("PDK")
    if not root or not name:
        return None
    sources = Path(root) / name / "SOURCES"
    return sources.read_text().strip() if sources.is_file() else None


def run_manifest(worktree):
    """The manifest of a hardened worktree (runs/wokwi/final): what it was built from and what it made."""
    wt = Path(worktree)
    run = wt / "runs/wokwi"
    resolved = json.loads((run / "resolved.json").read_text())
    return with_refused(wt, dict(
        schema=SCHEMA, kind="run", files=build_inputs(wt),
        tools=tools_of(wt, resolved.get("meta", {}).get("librelane_version"), pdk_sources(resolved)),
        outputs=dict(netlist=digest(one((run / "final/nl").glob("*.nl.v"), "final netlist")),
                     gds_normalized=normalized_gds_digest(one((run / "final/gds").glob("*.gds"), "final GDS")))))


def with_refused(root, manifest):
    """Adds "refused" (config paths outside the worktree) only when there are any,
    so a manifest without them is unchanged."""
    refused = config_paths(root)[1]
    if refused:
        manifest["refused"] = refused
    return manifest


def venv_librelane(root):
    for meta in sorted((Path(root) / ".venv-flow").glob("lib/python*/site-packages/librelane-*.dist-info/METADATA")):
        m = re.search(r"^Version: (\S+)$", meta.read_text(errors="replace"), re.M)
        if m:
            return m[1]
    return None


def checkout_manifest(root):
    """The manifest of a source checkout: its build inputs, its tt/ and its
    .venv-flow LibreLane when present (flow/harden.sh builds with both)."""
    if not (Path(root) / "src").is_dir():
        raise ValueError(f"{root}: not a checkout (no src/)")
    return with_refused(root, dict(schema=SCHEMA, kind="checkout", files=build_inputs(root),
                                   tools=tools_of(root, venv_librelane(root)), outputs={}))


def load(path):
    path = Path(path)
    if path.is_file():
        data = json.loads(path.read_text())
        if data.get("schema") != SCHEMA:
            raise ValueError(f"{path}: not a build manifest (schema {SCHEMA})")
        return data
    if (path / "runs/wokwi/final").is_dir():
        return run_manifest(path)
    if path.is_dir():
        return checkout_manifest(path)
    raise ValueError(f"{path}: no run, checkout or manifest")


def generator_check(root):
    """(returncode, output) of the checkout's own `flow/gen_config.py --preset
    submission --check`, as CI runs it: its committed config.json must be the
    generator's submission preset."""
    root = Path(root).resolve()        # it runs with cwd=root
    if not (root / GENERATOR).is_file():
        return 2, f"{root}: no {GENERATOR}"
    r = subprocess.run([sys.executable, str(root / GENERATOR), "--preset", "submission", "--check"],
                       cwd=root, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def compare(a, b, *, require_tools=False, names=("run", "shipping")):
    """(failures, notes) between two manifests. Build inputs must be equal. The
    tools and outputs are compared where both sides record them; a missing side
    is a failure only with require_tools."""
    failures, notes = [], []
    for m, name in ((a, names[0]), (b, names[1])):
        for s in m.get("refused", []):
            failures.append(f"{name}: src/config.json names {s!r}, which is absolute or outside the worktree "
                            "(refused: the build would read a file no manifest binds)")
    fa, fb = a["files"], b["files"]
    for path in sorted(set(fa) | set(fb)):
        if fa.get(path) != fb.get(path):
            what = (f"only in the {names[0]}" if path not in fb else f"only in the {names[1]}" if path not in fa
                    else "differs")
            if path == GENERATOR:      # recorded; the build reads config.json, which is bound
                notes.append(f"{path} {what} (the generator, not read by the build)")
                continue
            kind = "signoff STA only" if path in SIGNOFF_ONLY else "layout input"
            failures.append(f"build input {path} {what} ({kind})")
    for section in ("tools", "outputs"):
        for key in sorted(set(a.get(section, {})) | set(b.get(section, {}))):
            va, vb = a.get(section, {}).get(key), b.get(section, {}).get(key)
            if va is None or vb is None:
                message = f"{key}: not compared ({names[0]}: {va}, {names[1]}: {vb})"
                (failures if require_tools else notes).append(message)
            elif va != vb:
                failures.append(f"{key} differs ({names[0]}: {va}, {names[1]}: {vb})")
            else:
                notes.append(f"{key}: equal")
    return failures, notes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    w = sub.add_parser("write", help="write a hardened run's manifest (never overwrites)")
    w.add_argument("run")
    w.add_argument("--out", help="default RUN/build-manifest.json")
    s = sub.add_parser("show")
    s.add_argument("path")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--require-tools", action="store_true",
                   help="a tool, netlist or GDS identity recorded on one side only is a failure")
    a = ap.parse_args(argv)
    try:
        if a.command == "write":
            out = Path(a.out) if a.out else Path(a.run) / "build-manifest.json"
            with out.open("x") as f:
                json.dump(run_manifest(a.run), f, indent=1, sort_keys=True)
                f.write("\n")
            print(f"build manifest: {out}")
            return 0
        if a.command == "show":
            print(json.dumps(load(a.path), indent=1, sort_keys=True))
            return 0
        failures, notes = compare(load(a.a), load(a.b), require_tools=a.require_tools, names=("A", "B"))
    except (OSError, ValueError, KeyError) as e:
        print(f"build manifest failed: {e}")
        return 2
    for line in notes:
        print(f"  {line}")
    for line in failures:
        print(f"  DIFFERS: {line}")
    print("BUILDS DIFFER" if failures else "SAME BUILD")
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
