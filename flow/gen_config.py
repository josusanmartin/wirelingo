#!/usr/bin/env python3
"""Generate src/config.json for the SRAM build.

    python3 flow/gen_config.py [--banks N] [--density 44] [--grt-adjust 0.10] [--tuned NAME]

The tile (6x4 or 8x4) comes from info.yaml, the bank count defaults to
src/project.v's PE_NCORES. The banks sit in the swapped bottom corners; on 8x4,
three or four spread along the bottom edge. Signoff times the real latch arcs.
Experimental overrides must be named and recorded separately from submission.
"""
import argparse
import collections
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
M = "RM_IHPSG13_1P_256x32_c2_bm_bist"
P = "pdk_dir::libs.ref/sg13cmos5l_sram"
W, H = 416.64, 118.78
ROW = 3.78
SITE = 0.48
# Tiny Tapeout's IHP SG13CMOS5L die sizes (tt-support-tools
# tech/ihp-sg13cmos5l/tile_sizes.yaml, d66cf17). The core area is the die less
# the margins set below (LEFT/RIGHT_MARGIN_MULT 6 sites, TOP/BOTTOM 1 row).
TILES = {"6x4": (1289.28, 710.64), "8x4": (1724.16, 710.64)}
MARGIN_X, MARGIN_Y = 6 * SITE, ROW
# --late-clock-core's registers of core 0 (Q net name prefixes)
LATE_CORE_REGS = "r0,r1,r2,r3,crc"
# Tuned option sets from the 8x4 settings searches (docs/physical.md, "8x4
# settings searches"). `--tuned NAME` stands for these options, given before
# any others on the command line, so the config equals the searched run's.
TUNED = {
    # the 2-core fallback (flow/harden.sh NAME 2 -- --tuned fallback-8x4): run aqf-d44l8lcch15
    "fallback-8x4": ["--density", "44", "--late-clock", "8", "--late-clock-core", "4",
                     "--set", "GRT_RESIZER_HOLD_SLACK_MARGIN=0.15"],
    # the 3-core build (flow/harden.sh NAME -- --tuned rt10-8x4): run aqp-rt10, its
    # options in its order (evidence/8x4-search-20261114/3core/aqp-rt10/spec.txt)
    "rt10-8x4": ["--reg-clock-delay", "6", "--reg-clock-delay-gates", "8", "--sram-advance-toward", "0.5",
                 "--post-repair-hold", "--set", "GRT_RESIZER_HOLD_SLACK_MARGIN=0.30",
                 "--set", "DRT_SAVE_DRC_REPORT_ITERS=8", "--set", 'CTS_APPLY_NDR="none"',
                 "--sram-addr-buf8", "--post-repair-hold-passes", "2", "--pnr-max-transition"],
}

# The submission preset's option set by (tile, src/project.v's PE_NCORES), for
# builds a search tuned: the 2-core 8x4 fallback hardens with fallback-8x4
# (a shipping commit sets PE_NCORES 2 and regenerates src/config.json), the
# 3-core 8x4 build with rt10-8x4. 6x4 keeps the plain defaults.
SUBMISSION_TUNED = {("8x4", 2): "fallback-8x4", ("8x4", 3): "rt10-8x4"}


PATH_SUFFIXES = (".sdc", ".tcl", ".v", ".sv", ".vh", ".svh", ".lef", ".lib", ".gds", ".def", ".odb", ".json",
                 ".py", ".cdl", ".spice", ".spef", ".mag", ".yaml", ".yml", ".txt", ".cfg", ".lyp", ".lydrc")


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


def paths_outside_repo(config, root=ROOT):
    """The config's path-like strings outside flow/build_manifest.py's allowlist
    (097b): a dir:: path is allowed without glob characters when it normalizes
    to a path inside the repository (the manifest also requires the file to
    exist; this generator runs from bare test copies too), pdk_dir:: without
    "..", and a directory only inside src/ (never the root). Every other path
    string (is_path_string, at any depth; 097e: names that exist under root or
    src/ whatever the key) is refused: unprefixed (LibreLane reads it from the
    working directory), refg::, absolute; and every pdk::/scl:: key."""
    real_root = os.path.realpath(root)
    src = os.path.join(real_root, "src")
    found = []

    def check(key, v):
        if v.startswith("pdk_dir::"):
            rest = v[len("pdk_dir::"):]
            if os.path.isabs(rest) or ".." in pathlib.PurePath(rest).parts:
                found.append(v)
            return
        rel = v[len("dir::"):] if v.startswith("dir::") else None
        target = os.path.normpath(os.path.join(src, rel)) if rel is not None else None
        real = os.path.realpath(target) if target is not None and os.path.lexists(target) else target
        if rel is None or set("*?[]") & set(v) or os.path.isabs(rel) or \
                os.path.commonpath([real_root, target]) != real_root or \
                os.path.commonpath([real_root, real]) != real_root or \
                (os.path.isdir(target) and (os.path.commonpath([src, real]) != src or
                                            os.path.commonpath([src, target]) != src)):
            found.append(v)    # a directory only inside src/, so never the root (097c); links followed (097g)
    walk_config(config, (real_root, src), check, found.append)
    return found


def expand_tuned(argv):
    """Replace --tuned NAME (or --tuned=NAME) by TUNED[NAME], placed first."""
    out, tuned = [], []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--tuned" or a.startswith("--tuned="):
            name = a.split("=", 1)[1] if "=" in a else (argv[i + 1] if i + 1 < len(argv) else "")
            i += 1 if "=" in a else 2
            if name not in TUNED or tuned:
                raise SystemExit(f"--tuned: expected one of {sorted(TUNED)} once, got {name!r}")
            tuned = TUNED[name]
            continue
        out.append(a)
        i += 1
    return tuned + out


def tile_of(info):
    """The tiles: value of a Tiny Tapeout info.yaml."""
    found = re.findall(r'^  tiles: "([0-9]+x[0-9]+)"\s*(?:#.*)?$', pathlib.Path(info).read_text(), re.M)
    if len(found) != 1 or found[0] not in TILES:
        raise SystemExit(f"{info}: expected one tiles: line naming one of {sorted(TILES)}, found {found}")
    return found[0]


def core_corner(tile, tt=ROOT / "tt"):
    """Top-right corner of the core area; checked against tt-support-tools when present."""
    die_x, die_y = TILES[tile]
    sizes = pathlib.Path(tt) / "tech/ihp-sg13cmos5l/tile_sizes.yaml"
    if sizes.is_file():
        line = re.findall(rf'^{tile}: "0 0 ([0-9.]+) ([0-9.]+)"$', sizes.read_text(), re.M)
        if line[:1] != [(f"{die_x:.2f}", f"{die_y:.2f}")]:
            raise SystemExit(f"{sizes}: tile {tile} is {line}, this generator assumes {die_x} x {die_y}")
    return round(die_x - MARGIN_X, 2), round(die_y - MARGIN_Y, 2)


def project_cores(project=ROOT / "src/project.v"):
    found = re.findall(r"^`define PE_NCORES ([0-9])$", pathlib.Path(project).read_text(), re.M)
    if len(found) != 1:
        raise SystemExit(f"{project}: expected one `define PE_NCORES N")
    return int(found[0])


def inst(b):
    return f"u_chip.u_imem.g_bank[{b}].g_on.u_bank.sram"


def esc(b):
    return f"u_chip\\.u_imem\\.g_bank\\\\?\\[{b}\\\\?\\]\\.g_on\\.u_bank\\.sram"


def snap(v, g):
    return round(int(v / g) * g, 2)


def placements(nbanks, layout="top", core=(1286.40, 706.86)):
    """top:   banks 0-1 along the top edge from the right (pins down), banks 2-3
              along the bottom edge (mirrored, pins up).
    sides: macros rotated 90 degrees on the right and left die edges, pins facing
           inward, starting at the bottom (bank 0 right, 1 left, 2 right, 3 left);
           the top band stays free for the I/O pins (all at the top-left).
    spread: evenly along the bottom edge, mirrored (pins up); bank 0 leftmost,
           bank 1 rightmost, banks 2 and 3 in between (with two banks, the
           corners-swap positions).
    The top-left corner, where all the TT I/O pins are, stays free either way.
    core is the core area's top-right corner (core_corner)."""
    CORE_X1, CORE_Y1 = core
    if layout in ("spread-right", "corners-top"):
        # 3 banks: banks 0 and 1 in the swapped bottom corners; bank 2 on the
        # right half, so core 0 (the critical one: line unit, I/O pins) keeps
        # the left and centre. spread-right: bank 2 at the bottom, where 4-bank
        # spread puts its bank 3; corners-top: bank 2 at the top right, pins down.
        if nbanks != 3:
            raise SystemExit(f"{layout}: a 3-bank layout")
        pl = placements(2, "corners-swap", core)
        if layout == "spread-right":
            pl[2] = placements(4, "spread", core)[3]
        else:
            pl[2] = (snap(CORE_X1 - W - 3 * SITE, SITE), snap(CORE_Y1 - H - 2 * ROW, ROW), "N")
        return pl
    if layout == "spread":
        # core 0's line unit and the I/O pins (top left) pull bank 0's logic left
        gap = (CORE_X1 - 2.88 - 6 * SITE - nbanks * W) / max(nbanks - 1, 1)
        if gap < 6 * SITE:
            raise SystemExit(f"spread: {nbanks} banks do not fit along this tile's bottom edge")
        order = {1: [0], 2: [0, 1], 3: [0, 2, 1], 4: [0, 2, 3, 1]}[nbanks]
        return {b: (snap(2.88 + 3 * SITE + order.index(b) * (W + gap), SITE), 3.78, "FS") for b in range(nbanks)}
    if layout == "row":
        # side by side along the bottom edge, centred, mirrored (pins up): the
        # logic sits in one block above them, close to every bank's pins
        gap = 6 * SITE
        width = nbanks * W + (nbanks - 1) * gap
        x0 = snap((CORE_X1 + 2.88 - width) / 2, SITE)
        return {b: (snap(x0 + b * (W + gap), SITE), 3.78, "FS") for b in range(nbanks)}
    if layout in ("corners", "corners-swap"):
        # bottom corners, mirrored (pins up), banks 2-3 above them if needed;
        # corners-swap puts bank 0 on the left, near core 0's logic, which the
        # line unit and the I/O pins (top left) pull to that side
        x_r = snap(CORE_X1 - W - 3 * SITE, SITE)
        x_l = snap(2.88 + 3 * SITE, SITE)
        y2 = snap(3.78 + H + 30 * ROW, ROW)
        spots = [(x_r, 3.78, "FS"), (x_l, 3.78, "FS"), (x_r, y2, "FS"), (x_l, y2, "FS")]
        if layout == "corners-swap":
            spots = [spots[1], spots[0], spots[3], spots[2]]
        return {b: spots[b] for b in range(nbanks)}
    if layout == "sides":
        x_r = snap(CORE_X1 - H - 3 * SITE, SITE)
        x_l = snap(2.88 + 3 * SITE, SITE)
        y0, y1 = 3.78, snap(3.78 + W + 3 * ROW, ROW)
        spots = [(x_r, y0, "E"), (x_l, y0, "W"), (x_r, y1, "E"), (x_l, y1, "W")]
        return {b: spots[b] for b in range(nbanks)}
    y_top = snap(CORE_Y1 - H - 2 * ROW, ROW)
    x_r = snap(CORE_X1 - W - 3 * SITE, SITE)
    x_l = snap(x_r - W - 8.64, SITE)
    spots = [(x_r, y_top, "N"), (x_l, y_top, "N"), (x_r, 3.78, "FS"), (x_l, 3.78, "FS")]
    return {b: spots[b] for b in range(nbanks)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", choices=["submission", "experiment"], default="submission")
    ap.add_argument("--output", type=pathlib.Path,
                    default=pathlib.Path(__file__).resolve().parents[1] / "src/config.json")
    ap.add_argument("--check", action="store_true", help="fail if output differs; never write")
    ap.add_argument("--info", type=pathlib.Path, default=ROOT / "info.yaml",
                    help="Tiny Tapeout info.yaml whose tiles: sets the die (default: this repository's)")
    ap.add_argument("--banks", type=int, choices=range(1, 5), default=None,
                    help="SRAM banks (default: src/project.v's PE_NCORES)")
    # Release candidate: run t48ba (2026-10-07); the clock-tree and repair
    # options below default to its settings. Earlier: v2c (2026-10-06). At GRT_ADJUSTMENT 0.15 global routing
    # reserved too much Metal3 and ended with ~100 overflows that detailed
    # routing couldn't clear; 0.10 leaves ~10-40, which it does. Density also
    # steers routability-driven placement here (docs/physical.md).
    ap.add_argument("--density", type=int, default=34)
    ap.add_argument("--grt-adjust", type=float, default=0.10)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--every-other", action=argparse.BooleanOptionalAction, default=True,
                    help="extra SRAM array stripe pairs (prism's setting)")
    ap.add_argument("--layout", choices=["top", "sides", "corners", "corners-swap", "row", "spread", "spread-right", "corners-top"], default=None,
                    help="SRAM placement (default: corners-swap; spread for 3-4 banks on 8x4)")
    ap.add_argument("--post-grt-repair", action=argparse.BooleanOptionalAction, default=True,
                    help="slew/cap/fanout repair on global-routing parasitics (off in the Classic flow)")
    ap.add_argument("--max-wire", type=int, default=400, help="buffer wires longer than this (um, 0 = off)")
    ap.add_argument("--wire-cap", type=float, default=1.9e-4,
                    help="wire capacitance for PnR estimates, pF/um (0 = the tech LEF's, about half of it)")
    ap.add_argument("--post-grt-timing", action=argparse.BooleanOptionalAction, default=True,
                    help="setup/hold repair on global-routing parasitics (off in the Classic flow)")
    ap.add_argument("--grt-tns", type=int, default=0, help="post-GRT setup repair: %% of violating endpoints")
    ap.add_argument("--pnr-latch-flops", action=argparse.BooleanOptionalAction, default=None,
                    help="PnR SDC times the FIFO latches like flops (no D->Q borrowing); signoff keeps them")
    ap.add_argument("--grt-iters", type=int, default=None,
                    help="global-routing overflow iterations (quick routability experiments: 5)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=JSON",
                    help="override any LibreLane variable (e.g. PL_WIRE_LENGTH_COEF=0.2)")
    ap.add_argument("--clock-fanout-fix", action=argparse.BooleanOptionalAction, default=True,
                    help="opt-in CTS clusters of eight and IHP clock branch repair; requires fresh physical validation")
    ap.add_argument("--pnr-setup-margin", action=argparse.BooleanOptionalAction, default=True,
                    help="PnR SDC with 0.75 ns more setup uncertainty (src/protoemu_pnr_margin.sdc); signoff unchanged")
    ap.add_argument("--final-design-repair", action="store_true",
                    help="repeat post-GRT design repair (slew/cap/fanout) after post-GRT timing repair")
    ap.add_argument("--pnr-electrical-margins", action="store_true",
                    help="with --pnr-setup-margin: PnR fanout limit 6 (src/protoemu_pnr_elec.sdc)")
    ap.add_argument("--sram-pin-buffers", action=argparse.BooleanOptionalAction, default=True,
                    help="buffer each SRAM input pin after post-GRT timing repair (implies --final-design-repair)")
    ap.add_argument("--post-repair-hold", action="store_true",
                    help="with --sram-pin-buffers: repair hold again after the last post-GRT design repair "
                         "(Project.RepairDesignThenHold, flow/hold_repair_postgrt.tcl)")
    ap.add_argument("--last-repair-slew-pct", type=float, default=None, metavar="PCT",
                    help="with --post-repair-hold: the slew margin of the last post-GRT design repair only "
                         "(Project.LastRepairDesignPostGRT); the earlier repairs keep GRT_DESIGN_REPAIR_MAX_SLEW_PCT")
    ap.add_argument("--last-repair-cap-pct", type=float, default=None, metavar="PCT",
                    help="with --post-repair-hold: the capacitance margin of the last post-GRT design repair only")
    ap.add_argument("--post-repair-hold-passes", type=int, choices=(1, 2), default=1,
                    help="with --post-repair-hold: 2 repeats the last design repair and the hold repair once more "
                         "(Project.RepairDesignThenHold2)")
    ap.add_argument("--sram-addr-buf8", action="store_true",
                    help="with --sram-pin-buffers: drive each SRAM A_ADDR net with a buf_8 at its driver "
                         "instead of a _1 -> _2 driver swap (flow/sram_pin_buffers.tcl)")
    ap.add_argument("--pnr-max-transition", action="store_true",
                    help="with --pnr-setup-margin: PnR SDC adds a 1.0 ns design-wide transition limit "
                         "(flow/protoemu_pnr_transition.sdc); signoff unchanged")
    ap.add_argument("--antenna-fanout-repair", action=argparse.BooleanOptionalAction, default=True,
                    help="after detailed routing, buffer nets that antenna diodes took over the fanout limit (flow/antenna_fanout.tcl)")
    ap.add_argument("--late-clock", type=int, default=6, metavar="STAGES",
                    help="with --sram-clock-advance: delay the chip's pin registers' clocks by STAGES buffers")
    ap.add_argument("--late-clock-oe", type=int, default=0, metavar="STAGES",
                    help="with --late-clock: pin_oe gets STAGES buffers instead (it only drives pads)")
    ap.add_argument("--late-clock-core", type=int, default=0, metavar="STAGES",
                    help="with --late-clock: also delay core 0's R0-R3 and CRC registers by STAGES buffers")
    ap.add_argument("--late-clock-core-regs", default=LATE_CORE_REGS, metavar="R,R,...",
                    help=f"with --late-clock-core: which of core 0's registers (default {LATE_CORE_REGS}; "
                         "e.g. r0,r1,r2,r3 to leave the CRC on the tree after the CRC retime)")
    ap.add_argument("--reg-clock-delay", type=int, default=0, metavar="STAGES",
                    help="with --sram-clock-advance: delay every chip-clock branch except the SRAMs' by STAGES "
                         "buffers (the whole register tree against the SRAMs; flow/sram_clock_advance.tcl)")
    ap.add_argument("--reg-clock-delay-gates", type=int, default=None, metavar="STAGES",
                    help="with --reg-clock-delay: the clock-gate branches get STAGES buffers instead (default: the "
                         "same count), to match the flops' shift")
    ap.add_argument("--sram-advance-toward", type=float, default=0.0, metavar="F",
                    help="with --sram-clock-advance: place each SRAM's advance buffer the fraction F (0-1) of "
                         "the way from the clock root driver toward the macro's clock pin (0: at the root)")
    ap.add_argument("--sram-clock-advance", action=argparse.BooleanOptionalAction, default=True,
                    help="clock each SRAM from the clock root (useful skew: reads launch earlier, flow/sram_clock_advance.tcl)")
    ap.add_argument("--tuned", choices=sorted(TUNED),
                    help="a tuned option set from the 8x4 searches (TUNED), given before the other options")
    argv = sys.argv[1:]
    a = ap.parse_args(expand_tuned(argv))
    key = (tile_of(a.info), project_cores())
    if a.preset == "submission" and key in SUBMISSION_TUNED and not any(x.startswith("--tuned") for x in argv):
        # the submission preset of a build the searches tuned: its option set first
        a = ap.parse_args(expand_tuned(["--tuned", SUBMISSION_TUNED[key]] + argv))
    if a.tuned is not None:
        # expand_tuned took every full spelling; argparse matched an abbreviation
        # (--tune, --tu), which would otherwise be ignored silently
        ap.error("spell --tuned in full")
    if a.late_clock_core_regs != LATE_CORE_REGS and not (a.late_clock and a.late_clock_core):
        ap.error("--late-clock-core-regs needs --late-clock and --late-clock-core")
    tile = tile_of(a.info)
    core = core_corner(tile)
    cores = project_cores()
    if a.banks is None:
        a.banks = cores
    if a.layout is None:
        a.layout = "spread" if tile != "6x4" and a.banks >= 3 else "corners-swap"
    if a.pnr_latch_flops is None:
        # PnR times the FIFO latches like flops (protoemu_pnr.sdc); signoff
        # always times the real latch arcs. Same repair edits, ~20x faster
        # repair steps (docs/review.md).
        a.pnr_latch_flops = True
    if a.preset == "submission" and (a.banks != cores or a.grt_iters is not None):
        ap.error(f"submission requires {cores} banks (src/project.v's PE_NCORES) and no truncated routing; "
                 "use --preset experiment")
    c = collections.OrderedDict()
    c["meta"] = {"flow": "Classic", "substituting_steps": {
        "+OpenROAD.GeneratePDN": "Project.ExtendPowerStripes",
        "+OpenROAD.ResizerTimingPostCTS": "Project.DiodesOnSramPins"}}
    if a.sram_clock_advance:
        # one step after CTS; it runs the clock fanout repair too when asked
        c["meta"]["substituting_steps"]["+OpenROAD.CTS"] = "Project.SramClockAdvance"
    elif a.clock_fanout_fix:
        c["meta"]["substituting_steps"]["+OpenROAD.CTS"] = "Project.FixClockFanout"
    if a.post_repair_hold_passes == 2 and not a.post_repair_hold:
        # checked before the SRAM pin buffers: without them there is no hold step to repeat (096 finding 3)
        ap.error("--post-repair-hold-passes 2 needs --post-repair-hold")
    if a.sram_pin_buffers:
        c["meta"]["substituting_steps"]["+OpenROAD.ResizerTimingPostGRT"] = "Project.SramPinBuffers"
        c["meta"]["substituting_steps"]["+Project.SramPinBuffers"] = (
            ("Project.RepairDesignThenHold2" if a.post_repair_hold_passes == 2 else "Project.RepairDesignThenHold")
            if a.post_repair_hold else "OpenROAD.RepairDesignPostGRT")
        if a.sram_addr_buf8:
            c["PROTOEMU_SRAM_ADDR_BUF8"] = True
    elif a.post_repair_hold or a.sram_addr_buf8:
        ap.error("--post-repair-hold and --sram-addr-buf8 act in the steps that --sram-pin-buffers adds")
    elif a.final_design_repair:
        # Timing and hold repair after global routing resize and add cells
        # without slew/cap/fanout margins, and the SRAM pins' limits are tight
        # (0.595 ns transition in, 0.064 pF out): repair them again last.
        c["meta"]["substituting_steps"]["+OpenROAD.ResizerTimingPostGRT"] = "OpenROAD.RepairDesignPostGRT"
    last_repair = {}
    for opt, key in (("last_repair_slew_pct", "PROTOEMU_LAST_REPAIR_MAX_SLEW_PCT"),
                     ("last_repair_cap_pct", "PROTOEMU_LAST_REPAIR_MAX_CAP_PCT")):
        v = getattr(a, opt)
        if v is not None:
            if not a.post_repair_hold or not 0 <= v <= 100:
                ap.error(f"--{opt.replace('_', '-')} (0-100) sets the last repair of --post-repair-hold")
            last_repair[key] = v
    if a.antenna_fanout_repair:
        # Detailed routing's antenna repair runs after every resizer step, and
        # its diodes count as loads: split the nets they take over the limit.
        c["meta"]["substituting_steps"]["OpenROAD.DetailedRouting"] = "Project.DetailedRouting"
    c["//"] = (f"Generated by flow/gen_config.py. TT SG13CMOS5L {tile} with IHP 256x32 SRAM IMEM banks; "
               "SRAM settings follow kdp1965/ihp-um-janestreet-prism.")
    c["//generator"] = {k: v for k, v in vars(a).items() if k not in ("output", "check", "info", "tuned")
                        and (k not in ("clock_fanout_fix", "sram_clock_advance", "final_design_repair", "pnr_electrical_margins", "sram_pin_buffers", "antenna_fanout_repair", "late_clock", "late_clock_oe", "late_clock_core", "reg_clock_delay", "sram_advance_toward", "post_repair_hold", "sram_addr_buf8", "pnr_max_transition", "pnr_setup_margin", "set") or v)
                        and not (k == "late_clock_core_regs" and v == LATE_CORE_REGS)
                        and not (k == "post_repair_hold_passes" and v == 1)
                        and not (k in ("reg_clock_delay_gates", "last_repair_slew_pct", "last_repair_cap_pct")
                                 and v is None)}
    if a.clock_fanout_fix:
        c["CTS_SINK_CLUSTERING_SIZE"] = 8
        if a.sram_clock_advance:
            c["PROTOEMU_CLOCK_FANOUT_FIX"] = True
    if a.late_clock:
        if not a.sram_clock_advance:
            ap.error("--late-clock runs in the --sram-clock-advance step")
        # Q nets of pe_chip's pin_oe and pin_out registers (yosys names the
        # latter after core 0's pin_out_cur port)
        c["PROTOEMU_LATE_CLOCK_REGS"] = [f"u_chip.pin_oe*={a.late_clock_oe or a.late_clock}", "*.pin_out_cur*"]
        if a.late_clock_core:
            regs = a.late_clock_core_regs.split(",")
            if not regs or len(set(regs)) != len(regs) or not all(re.fullmatch(r"[a-z][a-z0-9_]*", r) for r in regs):
                ap.error("--late-clock-core-regs: distinct register names separated by commas")
            c["PROTOEMU_LATE_CLOCK_REGS"] += [f"u_chip.g_core*0*.g_on.u_core.{r}??[0-9]*={a.late_clock_core}"
                                              for r in regs]
        c["PROTOEMU_LATE_CLOCK_STAGES"] = a.late_clock
    if a.sram_advance_toward:
        if not a.sram_clock_advance or not 0 < a.sram_advance_toward <= 1:
            ap.error("--sram-advance-toward F (0 < F <= 1) runs in the --sram-clock-advance step")
        c["PROTOEMU_SRAM_ADVANCE_TOWARD"] = a.sram_advance_toward
    if a.reg_clock_delay:
        if not a.sram_clock_advance or a.reg_clock_delay < 0:
            ap.error("--reg-clock-delay N (N > 0) runs in the --sram-clock-advance step")
        c["PROTOEMU_REG_CLOCK_DELAY"] = a.reg_clock_delay
        if a.reg_clock_delay_gates is not None:
            if a.reg_clock_delay_gates < 0:
                ap.error("--reg-clock-delay-gates must be 0 or more")
            c["PROTOEMU_REG_CLOCK_DELAY_GATES"] = a.reg_clock_delay_gates
    elif a.reg_clock_delay_gates is not None:
        ap.error("--reg-clock-delay-gates needs --reg-clock-delay")
    c["PL_TARGET_DENSITY_PCT"] = a.density
    c["CLOCK_PERIOD"] = 20
    # The FIFOs store into latches that open in the half cycle after a push;
    # their data arrives while open, which STA reports as time borrowed with
    # exactly 0 slack. Any setup margin makes those 512 latch inputs per core
    # look violated and the repair loop never ends, so the margin is 0.
    c["PL_RESIZER_SETUP_SLACK_MARGIN"] = 0
    c["GRT_RESIZER_SETUP_SLACK_MARGIN"] = 0
    # With latches and a 0 margin, repairing every violating endpoint's slack
    # (TNS) at the slow corner took 1.5-2 h per step (it was minutes before).
    # Historical timing does not establish this candidate's signoff.
    c["PL_RESIZER_SETUP_REPAIR_TNS_PCT"] = 20
    # After global routing, repair stalls: WNS settles in ~800 passes and the
    # TNS pass then ran 2000 more (t11s: 1 h 54 m for -2.003 -> -2.003 ns).
    c["GRT_RESIZER_SETUP_REPAIR_TNS_PCT"] = a.grt_tns
    c["PL_RESIZER_HOLD_SLACK_MARGIN"] = 0.05
    # 0.05 left fast-corner hold at +0.04-0.05 ns after extraction, and two of
    # three clean runs missed it by ~10 ps; 0.10 (runs m10, v2a-c) kept it
    # positive; with the SRAM clock advance and late pin clocks, 0.13 (run
    # t48ba) gives +0.095 ns at the fast corner.
    c["GRT_RESIZER_HOLD_SLACK_MARGIN"] = 0.13
    c["RUN_LINTER"] = 1
    c["LINTER_INCLUDE_PDK_MODELS"] = 1
    c["//clocks"] = "clk, and the host port's SCK on ui_in[6]; src/protoemu.sdc constrains both"
    c["CLOCK_PORT"] = ["clk", "ui_in[6]"]
    c["PNR_SDC_FILE"] = "dir::protoemu_pnr.sdc" if a.pnr_latch_flops else "dir::protoemu.sdc"
    if a.pnr_setup_margin:
        if not a.pnr_latch_flops:
            ap.error("--pnr-setup-margin builds on the PnR latch SDC")
        c["PNR_SDC_FILE"] = "dir::protoemu_pnr_margin.sdc"
    if a.pnr_max_transition:
        if not a.pnr_setup_margin or a.pnr_electrical_margins:
            ap.error("--pnr-max-transition builds on --pnr-setup-margin (not --pnr-electrical-margins)")
        c["PNR_SDC_FILE"] = "dir::../flow/protoemu_pnr_transition.sdc"
    if a.pnr_electrical_margins:
        if not a.pnr_setup_margin:
            ap.error("--pnr-electrical-margins builds on --pnr-setup-margin")
        c["PNR_SDC_FILE"] = "dir::protoemu_pnr_elec.sdc"
    c["SIGNOFF_SDC_FILE"] = "dir::protoemu.sdc"
    c["STA_EXTRA_CORNER_TCL_FILE"] = "dir::timing_checks.tcl"
    c["//threads"] = "LibreLane 3.1.0.dev3 passes '-threads None' to OpenROAD when this is unset (single-threaded)"
    c["OPENROAD_THREADS"] = a.threads
    c["//corners"] = ("Repair at all three corners. Fast-corner hold once took ~3200 buffers: the SDC's "
                      "0.25 ns uncertainty applied to hold too (now 0.1 ns, src/protoemu.sdc)")
    c["PNR_CORNERS"] = ["nom_typ_1p20V_25C", "nom_slow_1p08V_125C", "nom_fast_1p32V_m40C"]
    if a.post_grt_repair:
        c["//repair"] = ("Post-placement repair alone left ~3 ns slews on long nets and SRAM-pin slews "
                         "above the macro's 0.476 ns limit")
        c["RUN_POST_GRT_DESIGN_REPAIR"] = 1
    if a.max_wire:
        c["DESIGN_REPAIR_MAX_WIRE_LENGTH"] = a.max_wire
        c["GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH"] = a.max_wire
    if a.wire_cap:
        # final5's extracted SPEF: 0.19 fF/um over all signal nets (least squares on
        # routed length), while the tech LEF says 0.092-0.097; resistance matches
        # (0.515 ohm/um, 20 ohm per via). With the LEF values the resizer saw +1.95 ns
        # of slow-corner slack after CTS that was -6.9 ns after extraction.
        c["//rc"] = "Measured wire capacitance (flow/gen_config.py --wire-cap); LEF resistance"
        m1 = a.wire_cap * 6.8784e-5 / 9.2e-5
        c["LAYERS_RC"] = {"*": {"Metal1": {"res": 8.4375e-4, "cap": round(m1, 8)},
                                **{m: {"res": 5.15e-4, "cap": a.wire_cap} for m in ("Metal2", "Metal3", "Metal4")}}}
    if a.post_grt_timing:
        c["RUN_POST_GRT_RESIZER_TIMING"] = 1
    if a.grt_iters is not None:
        c["GRT_OVERFLOW_ITERS"] = a.grt_iters
    if a.grt_adjust is not None:
        c["GRT_ADJUSTMENT"] = a.grt_adjust
    pl = placements(a.banks, a.layout, core)
    c["MACROS"] = {M: {
        "instances": {inst(b): {"location": [x, y], "orientation": o} for b, (x, y, o) in pl.items()},
        "gds": [f"{P}/gds/{M}.gds"],
        "lef": [f"{P}/lef/{M}.lef"],
        # keyed by the PVT part of the corner name: every STA corner is nom_* (the
        # interconnect corner), so nom_*/min_*/max_* keys gave all of them typ SRAM
        "lib": {"*_typ_*": [f"{P}/lib/{M}_typ_1p20V_25C.lib"],
                "*_fast_*": [f"{P}/lib/{M}_fast_1p32V_m55C.lib"],   # closest: the SRAM has no -40C view
                "*_slow_*": [f"{P}/lib/{M}_slow_1p08V_125C.lib"]},
        "nl": [f"dir::macros/{M}.v"],
        "spice": [f"{P}/cdl/{M}.cdl"],
    }}
    c["PDN_MACRO_CONNECTIONS"] = [x for b in pl for x in (
        f"{esc(b)} VPWR VGND VDD! VSS!", f"{esc(b)} VPWR VGND VDDARRAY! VSS!")]
    c["PDN_CFG"] = "dir::pdn_cfg.tcl"
    c["ERROR_ON_PDN_VIOLATIONS"] = 0
    c["EXTEND_STRIPES_SRAM_ARRAY_EVERY_OTHER"] = a.every_other
    c["EXTEND_STRIPES_PIN_FACE_MARGIN"] = 0
    c["FP_MACRO_HORIZONTAL_HALO"] = 2.0
    c["FP_MACRO_VERTICAL_HALO"] = 0.48
    c["RUN_HEURISTIC_DIODE_INSERTION"] = 0
    c["RUN_ANTENNA_REPAIR"] = 1
    c["DRT_ANTENNA_REPAIR_ITERS"] = 6
    c["DIODE_CELL"] = "sg13cmos5l_antennanp/A"
    c["//magic"] = "Magic sees the SRAM as a LEF abstract; KLayout DRC + LVS are the signoff (as in prism)."
    c["RUN_MAGIC_DRC"] = 0
    c["ERROR_ON_ILLEGAL_OVERLAPS"] = 0
    c["MAGIC_MACRO_STD_CELL_SOURCE"] = "PDK"
    c["MAGIC_EXT_ABSTRACT_CELLS"] = ["RM_IHPSG13_.*"]
    c["RUN_KLAYOUT_XOR"] = 0
    c["RUN_KLAYOUT_DRC"] = 1
    c["DESIGN_REPAIR_BUFFER_OUTPUT_PORTS"] = 0
    c["TOP_MARGIN_MULT"] = 1
    c["BOTTOM_MARGIN_MULT"] = 1
    c["LEFT_MARGIN_MULT"] = 6
    c["RIGHT_MARGIN_MULT"] = 6
    c["FP_SIZING"] = "absolute"
    c["GRT_ALLOW_CONGESTION"] = 1
    c["GRT_MACRO_EXTENSION"] = 2
    c["FP_IO_HLENGTH"] = 2
    c["FP_IO_VLENGTH"] = 2
    c["//pdn"] = "44.96 um = 8 x the SRAM's 5.62 um supply-column pitch"
    c["FP_PDN_VPITCH"] = 44.96
    c["FP_PDN_VWIDTH"] = 2.1
    c["FP_PDN_VSPACING"] = 3.52
    c["FP_PDN_VOFFSET"] = 6.15
    c["RUN_CTS"] = 1
    c["FP_PDN_MULTILAYER"] = 0
    c["MAGIC_DEF_LABELS"] = 0
    c["MAGIC_WRITE_LEF_PINONLY"] = 1
    # Release repair settings (run t48ba, docs/physical.md): electrical limits
    # enforced with margin during repair, so the slow corner ends with no
    # slew/cap violations; antenna repair keeps GRT margin and adds no DRT margin.
    c["MAX_FANOUT_CONSTRAINT"] = 8
    c["GRT_DESIGN_REPAIR_MAX_SLEW_PCT"] = 30
    c["DESIGN_REPAIR_MAX_SLEW_PCT"] = 30
    c["GRT_DESIGN_REPAIR_MAX_CAP_PCT"] = 30
    c["GRT_ANTENNA_REPAIR_MARGIN"] = 30
    c["DRT_ANTENNA_REPAIR_MARGIN"] = 0
    c.update(last_repair)
    for item in a.set:
        key, value = item.split("=", 1)
        c[key] = json.loads(value)
    outside = paths_outside_repo(c)
    if outside:
        ap.error(f"config paths outside the allowlist (dir:: inside the repository without glob characters, a directory only inside src/; pdk_dir:: without ..; the build manifest would refuse them): {outside}")
    rendered = json.dumps(c, indent=2) + "\n"
    if a.check:
        if not a.output.exists() or a.output.read_text() != rendered:
            raise SystemExit(f"{a.output} differs from {a.preset} preset; regenerate explicitly")
        print(f"configuration matches {a.preset} preset")
    else:
        a.output.write_text(rendered)
        print(f"wrote {a.output}: {a.banks} banks at", {b: p[:2] for b, p in pl.items()})


if __name__ == "__main__":
    main()
