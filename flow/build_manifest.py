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
# LibreLane 3.1.0.dev3's Path-typed variables (the verifier's censuses in 097d
# and 098: every step's and flow's variables, LibreLane's PDK, SCL and option
# lists, the plugin's; 67 names), and every deprecated name of one, which
# LibreLane reads before the canonical name: every value of one of these is a
# path, whatever its shape.
PATH_KEYS = frozenset("""
CELL_BB_VERILOG_MODELS CELL_CDLS CELL_GDS CELL_LEFS CELL_LIBS CELL_MAGLEFS CELL_MAGS CELL_SPICE_MODELS
CELL_VERILOG_MODELS DESIGN_DIR EQY_SCRIPT EXTRA_CDLS EXTRA_GDS EXTRA_LEFS EXTRA_LIBS EXTRA_SPEFS
EXTRA_SPICE_MODELS EXTRA_VERILOG_MODELS FALLBACK_SDC FP_DEF_TEMPLATE FP_TRACKS_INFO IO_PIN_ORDER_CFG
KLAYOUT_DEF_LAYER_MAP KLAYOUT_DRC_RUNSET KLAYOUT_PROPERTIES KLAYOUT_TECH LINTER_VLTS MACRO_PLACEMENT_CFG
MAGICRC MAGIC_DRC_MAGLEFS MAGIC_PDK_SETUP MAGIC_TECH NETGEN_SETUP PAD_CDLS PAD_GDS PAD_LEFS PAD_LIBS
PAD_SPICE_MODELS PAD_VERILOG_MODELS PDK_ROOT PDN_CFG PNR_EXCLUDED_CELL_FILE
PNR_SDC_FILE RCX_RULESETS RCX_SDC_FILE SIGNOFF_SDC_FILE STA_EXTRA_CORNER_TCL_FILE SYNTH_ABC_STRATEGY_SCRIPT
SYNTH_CSA_MAP SYNTH_EXCLUDED_CELL_FILE SYNTH_EXTRA_MAPPING_FILE SYNTH_FA_MAP SYNTH_LATCH_MAP SYNTH_RCA_MAP
SYNTH_TRISTATE_MAP TECH_LEFS VERILOG_FILES VERILOG_INCLUDE_DIRS VSRC_LOC_FILES
STD_CELL_LIBRARY_CDL GDS_FILES CELLS_GDS CELLS_LEF LIB EXTRA_GDS_FILES VERILOG_FILES_BLACKBOX
FALLBACK_SDC_FILE BASE_SDC_FILE SDC_FILE TRACKS_INFO_FILE FP_PIN_ORDER_CFG KLAYOUT_DRC_TECH_SCRIPT
LINTER_VLT MAGIC_MAGICRC MAGIC_TECH_FILE NETGEN_SETUP_FILE FP_PDN_CFG DRC_EXCLUDE_CELL_LIST
PNR_EXCLUSION_CELL_LIST CARRY_SELECT_ADDER_MAP NO_SYNTH_CELL_LIST SYNTH_EXCLUSION_CELL_LIST
FULL_ADDER_MAP RIPPLE_CARRY_ADDER_MAP TRISTATE_BUFFER_MAP
KLAYOUT_ANTENNA_RUNSET KLAYOUT_DENSITY_RUNSET KLAYOUT_FILLER_SCRIPT KLAYOUT_LVS_SCRIPT
KLAYOUT_SEALRING_SCRIPT MAGIC_FILLER_SCRIPT PAD_CFG VHDL_FILES
""".split())
# MACROS' Path fields (LibreLane's Macro: each macro's fields, at any depth below)
MACRO_PATH_FIELDS = frozenset("gds lef vh nl pnl spef lib spice sdf json_h".split())
PATH_PREFIXES = ("dir::", "pdk_dir::", "refg::", "ref::")
# LibreLane flattens a matching pdk::<glob> or scl::<glob> block into the top
# level; no config here uses one, so such a key is refused outright
BLOCK_PREFIXES = ("pdk::", "scl::")
CELL_PIN_RE = r"^[A-Za-z0-9_]+/[A-Za-z0-9_]+$"
# Keys LibreLane's preprocessor exposes as symbols (librelane/config/preprocessor.py
# Keys and PROCESS_INFO_ALLOWLIST): a config's own PDKPATH redirects every later
# pdk_dir::, PDK/STD_CELL_LIBRARY override the PDK and library; the generator
# writes none of them, so each is refused as a key
SYMBOL_KEYS = frozenset("PDK_ROOT PDK PDKPATH STD_CELL_LIBRARY STD_CELL_LIBRARY_OPT PAD_CELL_LIBRARY DESIGN_DIR".split())


def path_value_strings(v):
    """The strings of a value under a Path name (LibreLane makes a Path of any
    value's str(): true, {} or [] name root files), or None when its shape is
    not a path value: null, a string, a non-empty list of strings, or a
    non-empty dict (corner -> path) of strings and non-empty lists of strings."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
        return list(v)
    if isinstance(v, dict) and v:
        out = []
        for x in v.values():
            if isinstance(x, str):
                out.append(x)
            elif isinstance(x, list) and x and all(isinstance(y, str) for y in x):
                out += x
            else:
                return None
        return out
    return None


def names_existing(v, roots):
    """Whether v names an existing path relative to one of roots (the worktree
    root, the harden's working directory, and src/)."""
    return any(os.path.lexists(os.path.join(r, v)) for r in roots)


def is_path_string(key, field, v, roots):
    """Whether a config string is a path. By name: any value of a Path-typed key
    (top level) or of a MACROS Path field. Whatever the key (the backstop): "."
    and "..", a LibreLane path prefix, a "/", a known file suffix, or a name that
    exists relative to the worktree root or src/; a "cell/pin" value of a
    *_CELL(S) key is not a path unless it exists (no Path-typed key is named
    *_CELL)."""
    if key in PATH_KEYS or (key == "MACROS" and field in MACRO_PATH_FIELDS):
        return True
    if v in (".", "..") or v.startswith(PATH_PREFIXES):
        return True
    exists = names_existing(v, roots)
    if exists:
        return True
    if "/" in v or v.lower().endswith(PATH_SUFFIXES):
        return not (str(key).endswith(("_CELL", "_CELLS")) and re.match(CELL_PIN_RE, v))
    return False


def walk_config(config, roots, path, refuse):
    """Call path(top_key, string) for each path string of a LibreLane config at
    any depth (is_path_string; every string under a Path name), and
    refuse(string) for what is refused before any allowlist: a pdk::/scl:: key;
    a symbol key (SYMBOL_KEYS); a value of a Path name that is not path-shaped
    (path_value_strings); meta.version (with version 3 LibreLane
    runs str.format on every string after rewriting dir::/pdk_dir::, and the
    generator writes no version); any string with "{" or "}" (no generated or
    recorded config has one). A number is a path as str(number) under a Path
    name, or when that names an existing path (LibreLane makes a Path of it);
    booleans and null are not. Comment keys ("//...") are skipped."""
    def walk(v, top, field, depth):
        if isinstance(v, dict):
            for k, x in v.items():
                k = str(k)
                if k.startswith("//"):
                    continue
                if k.startswith(BLOCK_PREFIXES):
                    refuse(k)
                    continue
                if depth == 1 and top == "meta" and k == "version":
                    refuse("meta.version")
                    continue
                if depth == 0 and k in SYMBOL_KEYS:
                    refuse(k)
                    continue
                if (depth == 0 and k in PATH_KEYS) or (depth == 2 and top == "MACROS" and k in MACRO_PATH_FIELDS):
                    # a Path name: only path-shaped values, each string a path (097g)
                    strings = path_value_strings(x)
                    if strings is None:
                        refuse(json.dumps(x, sort_keys=True))
                        continue
                    for s in strings:
                        if "{" in s or "}" in s:
                            refuse(s)
                        else:
                            path(k if depth == 0 else top, s)
                    continue
                walk(x, k if depth == 0 else top, k if (depth == 2 and top == "MACROS") else field, depth + 1)
        elif isinstance(v, list):
            for x in v:
                walk(x, top, field, depth)
        elif isinstance(v, str):
            if "{" in v or "}" in v:
                refuse(v)
            elif is_path_string(top, field, v, roots):
                path(top, v)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            if top in PATH_KEYS or (top == "MACROS" and field in MACRO_PATH_FIELDS) or names_existing(str(v), roots):
                path(top, str(v))
    walk(config, None, None, 0)


def config_path_strings(config, roots=()):
    """(key, string) for the path strings of a LibreLane config (walk_config),
    keyed by the top-level key; what walk_config refuses outright comes as
    (None, string). roots: where a bare name may exist (the worktree root and
    src/)."""
    out = []
    walk_config(config, roots, lambda k, v: out.append((k, v)), lambda s: out.append((None, s)))
    return out


def dir_files(real):
    """Every file under a directory, sorted, each resolved (symlinks followed);
    None when the walk meets a directory twice (a symlink loop)."""
    seen, files = set(), []
    for d, dirs, names in os.walk(real, followlinks=True):
        if os.path.realpath(d) in seen:
            return None
        seen.add(os.path.realpath(d))
        files += [os.path.realpath(os.path.join(d, f)) for f in names]
    return sorted(files)


def config_paths(root):
    """(bound, refused) for src/config.json's path-like strings, by the allowlist
    above: a dir:: path is resolved against src/ (normalized, symlinks
    followed); a file inside the worktree is bound by its path relative to the
    resolved worktree root, a directory inside src/ binds every file under it
    (sorted, each resolved, existing and inside the worktree; a symlink loop is
    refused); the root and any other directory are refused. Refused: a dir::
    path with glob characters, or missing, or resolving outside the worktree;
    pdk_dir:: with ".."; a pdk::/scl:: key; and any other path string
    (is_path_string, at any depth: unprefixed, refg::, absolute)."""
    root = Path(root)
    try:
        config = json.loads((root / "src/config.json").read_text())
    except (OSError, ValueError):
        return {}, []
    real_root = os.path.realpath(root)
    inside = lambda p: os.path.commonpath([real_root, p]) == real_root
    src = os.path.join(real_root, "src")
    bound, refused = {}, []
    for key, s in config_path_strings(config, (real_root, src)):
        if key is None:            # refused outright (walk_config)
            refused.append(s)
            continue
        if s.startswith("pdk_dir::"):
            rest = s[len("pdk_dir::"):]
            if os.path.isabs(rest) or ".." in Path(rest).parts:
                refused.append(s)
            continue
        if not s.startswith("dir::") or GLOB_CHARS & set(s):
            refused.append(s)
            continue
        rel = s[len("dir::"):]
        # LibreLane: os.path.abspath (".." applied to the text), then the OS
        # follows links; both the lexical path and the file opened must be inside
        norm = os.path.normpath(os.path.join(src, rel))
        real = os.path.realpath(norm)
        if os.path.isabs(rel) or not inside(norm) or not inside(real) or not os.path.exists(real):
            refused.append(s)
            continue
        if os.path.isdir(real) and (os.path.commonpath([src, real]) != src or os.path.commonpath([src, norm]) != src):
            refused.append(s)      # a directory only inside src/ (097c: dir::.. names the root)
            continue
        files = [real] if os.path.isfile(real) else dir_files(real)
        if files is None or not all(inside(f) and os.path.isfile(f) for f in files):
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


def resolved_paths(root, resolved):
    """(bound, refused) for the paths LibreLane itself resolved for the run (its
    resolved.json: absolute paths for every Path variable, MACROS included),
    whatever spelling the config used. A path under the run's design directory's
    parent (the worktree when it hardened) is taken relative to the worktree now,
    must be inside it lexically and after links, and binds its file (a directory:
    every file under it, inside src/ only), except under tt/ (tt-support-tools,
    recorded by its tree digest); a path under the run's PDK_ROOT or in the flow
    image (/nix/store/) is a tool input; anything else is refused. Nothing
    without a DESIGN_DIR."""
    design = resolved.get("DESIGN_DIR")
    if not isinstance(design, str) or not os.path.isabs(design):
        return {}, []
    old_root = os.path.dirname(os.path.normpath(design))
    pdk_root = resolved.get("PDK_ROOT")
    real_root = os.path.realpath(root)
    src = os.path.join(real_root, "src")
    inside = lambda p, d=real_root: os.path.commonpath([d, p]) == d
    strings = []
    for k, v in resolved.items():
        if k in PATH_KEYS and k not in SYMBOL_KEYS:
            strings += path_value_strings(v) or []
    for macro in (resolved.get("MACROS") or {}).values():
        for k, v in (macro or {}).items():
            if k in MACRO_PATH_FIELDS:
                strings += path_value_strings(v) or []
    bound, refused = {}, []
    for s in strings:
        norm = os.path.normpath(s) if os.path.isabs(s) else None
        if norm is not None and os.path.commonpath([old_root, norm]) == old_root:
            rel = os.path.relpath(norm, old_root)
            if rel.split(os.sep)[0] == "tt":
                continue           # tt-support-tools: a tool input (tools.tt_support_tools, its tree digest)
            cur = os.path.normpath(os.path.join(real_root, rel))
            real = os.path.realpath(cur)
            if not inside(cur) or not inside(real) or not os.path.exists(real):
                refused.append(s)
                continue
            if os.path.isdir(real):
                files = dir_files(real) if inside(cur, src) and inside(real, src) else None
            else:
                files = [real]
            if files is None or not all(inside(f) and os.path.isfile(f) for f in files):
                refused.append(s)
                continue
            for f in files:
                bound[os.path.relpath(f, real_root)] = digest(f)
        elif norm is not None and isinstance(pdk_root, str) and os.path.isabs(pdk_root) and \
                os.path.commonpath([os.path.normpath(pdk_root), norm]) == os.path.normpath(pdk_root):
            continue
        elif norm is not None and norm.startswith("/nix/store/"):
            continue               # inside the flow image (LibreLane's own scripts: tools.image)
        else:
            refused.append(s)
    return dict(sorted(bound.items())), sorted(set(refused))


def run_manifest(worktree):
    """The manifest of a hardened worktree (runs/wokwi/final): what it was built from and what it made.
    Its files are the build inputs plus every file LibreLane resolved for the run
    inside the worktree (resolved_paths); what that refuses is refused."""
    wt = Path(worktree)
    run = wt / "runs/wokwi"
    resolved = json.loads((run / "resolved.json").read_text())
    files = build_inputs(wt)
    rbound, rrefused = resolved_paths(wt, resolved)
    for name, value in rbound.items():
        files.setdefault(name, value)
    return with_refused(wt, dict(
        schema=SCHEMA, kind="run", files=dict(sorted(files.items())),
        tools=tools_of(wt, resolved.get("meta", {}).get("librelane_version"), pdk_sources(resolved)),
        outputs=dict(netlist=digest(one((run / "final/nl").glob("*.nl.v"), "final netlist")),
                     gds_normalized=normalized_gds_digest(one((run / "final/gds").glob("*.gds"), "final GDS")))),
        extra_refused=rrefused)


def with_refused(root, manifest, extra_refused=()):
    """Adds "refused" (config paths outside the worktree, and resolved paths
    refused) only when there are any, so a manifest without them is unchanged."""
    refused = sorted(set(config_paths(root)[1]) | set(extra_refused))
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
            failures.append(f"{name}: src/config.json (or the run's resolved.json) names {s!r}, which is absolute or outside the worktree "
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
