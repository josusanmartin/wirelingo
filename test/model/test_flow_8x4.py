"""flow/gen_config.py on Tiny Tapeout's 6x4 and 8x4 IHP tiles, and N SRAM banks.

The tile comes from info.yaml. A 6x4 info.yaml with a 2-core project.v must
still give the signed-off run fo6's configuration byte for byte; on 8x4, with
a 2-core project.v and the same options, only the right-hand bank moves (the
2-core 8x4 submission preset itself is the tuned fallback set)."""
import hashlib
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GEN = ROOT / "flow/gen_config.py"
# sha256 of fo6's src/config.json (the run directory on the compute runner,
# and src/config.json at public main d2ed344)
FO6_CONFIG = "fe078128cda606cc7c1182c7e495c9140369297e0bf487e632444029dad24c50"
M = "RM_IHPSG13_1P_256x32_c2_bm_bist"


def info_with(tmp_path, tiles):
    text = (ROOT / "info.yaml").read_text()
    lines = [l for l in text.splitlines() if l.startswith("  tiles: ")]
    assert len(lines) == 1
    p = tmp_path / f"info-{tiles}.yaml"
    p.write_text(text.replace(lines[0], f'  tiles: "{tiles}"'))
    return p


def generate(tmp_path, *options, name="config.json", cores=None):
    """Run the generator; with `cores`, a copy of it beside a src/project.v of that
    many cores (the generator reads PE_NCORES next to itself), so the 2-core
    cases below hold whatever this repository's project.v says (3 since AO)."""
    gen = GEN
    if cores is not None:
        root = tmp_path / f"root-{cores}"
        if not root.exists():
            (root / "flow").mkdir(parents=True)
            (root / "src").mkdir()
            (root / "flow/gen_config.py").write_text(GEN.read_text())
            (root / "info.yaml").write_text((ROOT / "info.yaml").read_text())
            project = (ROOT / "src/project.v").read_text()
            n = len(re.findall(r"^`define PE_NCORES [0-9]$", project, re.M))
            assert n == 1
            (root / "src/project.v").write_text(
                re.sub(r"^`define PE_NCORES [0-9]$", f"`define PE_NCORES {cores}", project, flags=re.M))
        gen = root / "flow/gen_config.py"
    dst = tmp_path / name
    r = subprocess.run([sys.executable, str(gen), *options, "--output", str(dst)], capture_output=True, text=True)
    return r, dst


def project_cores():
    return int(re.search(r"^`define PE_NCORES ([0-9])$", (ROOT / "src/project.v").read_text(), re.M).group(1))


def banks(path):
    inst = json.loads(path.read_text())["MACROS"][M]["instances"]
    return {int(k.split("[")[1][0]): (v["location"], v["orientation"]) for k, v in inst.items()}


def plugin_tree():
    import ast
    return ast.parse((ROOT / "librelane_plugin_protoemu.py").read_text())


def plugin_defaults():
    """{(class, variable): default} for every Variable(...) in a class's config_vars,
    read from the plugin's syntax tree, not by matching text (097 finding 1)."""
    import ast
    from decimal import Decimal
    out = {}
    for cls in plugin_tree().body:
        if not isinstance(cls, ast.ClassDef):
            continue
        for stmt in cls.body:
            if not (isinstance(stmt, ast.Assign) and any(getattr(t, "id", "") == "config_vars" for t in stmt.targets)):
                continue
            for call in ast.walk(stmt.value):
                if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "Variable":
                    kw = {k.arg: k.value for k in call.keywords}
                    d = kw.get("default")
                    if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "Decimal":
                        value = Decimal(ast.literal_eval(d.args[0]))
                    else:
                        value = ast.literal_eval(d) if d is not None else "no default"
                    out[(cls.name, ast.literal_eval(call.args[0]))] = value
    return out


def test_6x4_info_gives_fo6_config_byte_for_byte(tmp_path):
    r, dst = generate(tmp_path, "--info", str(info_with(tmp_path, "6x4")), cores=2)
    assert r.returncode == 0, r.stderr
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == FO6_CONFIG


def test_8x4_moves_only_the_right_bank(tmp_path):
    # the same options on both tiles (the 2-core 8x4 submission preset is tuned: below)
    r6, c6 = generate(tmp_path, "--preset", "experiment", "--info", str(info_with(tmp_path, "6x4")), name="c6.json", cores=2)
    r8, c8 = generate(tmp_path, "--preset", "experiment", "--info", str(info_with(tmp_path, "8x4")), name="c8.json", cores=2)
    assert r6.returncode == r8.returncode == 0
    a, b = json.loads(c6.read_text()), json.loads(c8.read_text())
    assert {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)} == {"//", "MACROS"}
    assert "6x4" in a["//"] and "8x4" in b["//"]
    assert banks(c6) == {0: ([4.32, 3.78], "FS"), 1: ([868.32, 3.78], "FS")}
    assert banks(c8) == {0: ([4.32, 3.78], "FS"), 1: ([1302.72, 3.78], "FS")}


def test_committed_config_is_the_8x4_submission(tmp_path):
    assert '  tiles: "8x4"' in (ROOT / "info.yaml").read_text().splitlines()
    r, dst = generate(tmp_path, "--preset", "submission")
    assert r.returncode == 0, r.stderr
    assert dst.read_bytes() == (ROOT / "src/config.json").read_bytes()
    assert banks(dst)[1] == ([1302.72, 3.78], "FS")


@pytest.mark.parametrize("tiles", ["6x4", "8x4"])
def test_spread_with_two_banks_is_corners_swap(tmp_path, tiles):
    info = str(info_with(tmp_path, tiles))
    _, swap = generate(tmp_path, "--info", info, "--layout", "corners-swap", name="swap.json", cores=2)
    _, spread = generate(tmp_path, "--info", info, "--layout", "spread", name="spread.json", cores=2)
    a, b = json.loads(swap.read_text()), json.loads(spread.read_text())
    assert a["//generator"].pop("layout") == "corners-swap" and b["//generator"].pop("layout") == "spread"
    assert a == b


@pytest.mark.parametrize("tiles,die_x,n", [("8x4", 1724.16, 3), ("8x4", 1724.16, 4), ("6x4", 1289.28, 3)])
def test_three_and_four_banks_spread_along_the_bottom(tmp_path, tiles, die_x, n):
    layout = [] if tiles == "8x4" else ["--layout", "spread"]
    r, dst = generate(tmp_path, "--info", str(info_with(tmp_path, tiles)), "--preset", "experiment",
                      "--banks", str(n), *layout)
    assert r.returncode == 0, r.stderr
    c = json.loads(dst.read_text())
    assert c["//generator"]["layout"] == "spread"          # on 8x4, the default for 3-4 banks
    pl = banks(dst)
    assert sorted(pl) == list(range(n))
    xs = sorted((loc[0], b) for b, (loc, o) in pl.items())
    assert all(o == "FS" and loc[1] == 3.78 for loc, o in pl.values())
    assert xs[0] == (4.32, 0) and xs[-1][1] == 1                    # bank 0 leftmost, bank 1 rightmost
    width = 416.64
    for (x0, _), (x1, _) in zip(xs, xs[1:]):
        assert x1 - x0 >= width + 2 * c["FP_MACRO_HORIZONTAL_HALO"]    # no overlap, halos clear
    assert xs[-1][0] + width <= die_x - 2.88                          # inside the core area
    assert len(c["PDN_MACRO_CONNECTIONS"]) == 2 * n
    if tiles == "8x4" and n == 3:   # the study's grt3/pkg3 positions (docs/8x4-study.md)
        assert {b: loc for b, (loc, _) in pl.items()} == {0: [4.32, 3.78], 1: [1302.72, 3.78], 2: [653.28, 3.78]}
    if tiles == "8x4" and n == 4:   # grt4f2's
        assert {b: loc for b, (loc, _) in pl.items()} == {0: [4.32, 3.78], 1: [1302.72, 3.78],
                                                          2: [436.8, 3.78], 3: [869.76, 3.78]}


@pytest.mark.parametrize("n", [1, 3, 4])
def test_6x4_defaults_unchanged_for_other_bank_counts(tmp_path, n):
    # the pre-8x4 generator's default for every bank count was corners-swap
    r, dst = generate(tmp_path, "--info", str(info_with(tmp_path, "6x4")), "--preset", "experiment", "--banks", str(n))
    assert r.returncode == 0, r.stderr
    assert json.loads(dst.read_text())["//generator"]["layout"] == "corners-swap"
    r, _ = generate(tmp_path, "--info", str(info_with(tmp_path, "6x4")), "--preset", "experiment",
                    "--banks", "4", "--layout", "spread", name="no-fit.json")
    assert r.returncode != 0 and "do not fit" in r.stderr


@pytest.mark.parametrize("cores", [None, 2, 3])
def test_submission_bank_count_follows_project_cores(tmp_path, cores):
    n = project_cores() if cores is None else cores          # None: this repository's project.v
    other = 3 if n == 2 else 2
    r, _ = generate(tmp_path, "--preset", "submission", "--banks", str(other), cores=cores)
    assert r.returncode != 0 and "PE_NCORES" in r.stderr
    r, dst = generate(tmp_path, "--preset", "submission", cores=cores)
    assert r.returncode == 0 and len(banks(dst)) == n


@pytest.mark.parametrize("line", ['  tiles: "7x4"', '  tiles: 8x4', ''])
def test_unknown_or_missing_tile_is_refused(tmp_path, line):
    text = (ROOT / "info.yaml").read_text()
    old = [l for l in text.splitlines() if l.startswith("  tiles: ")][0]
    p = tmp_path / "info.yaml"
    p.write_text(text.replace(old, line))
    r, dst = generate(tmp_path, "--info", str(p))
    assert r.returncode != 0 and not dst.exists()


def test_tile_size_is_checked_against_tt_support_tools(tmp_path):
    spec = importlib.util.spec_from_file_location("gen_config", GEN)
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    assert g.core_corner("6x4", tt=tmp_path) == (1286.40, 706.86)   # fo6's literal corner
    assert g.core_corner("8x4", tt=tmp_path) == (1721.28, 706.86)   # the study copy's
    sizes = tmp_path / "tech/ihp-sg13cmos5l/tile_sizes.yaml"
    sizes.parent.mkdir(parents=True)
    sizes.write_text('6x4: "0 0 1289.28 710.64"\n8x4: "0 0 1724.16 710.64"\n')
    assert g.core_corner("8x4", tt=tmp_path) == (1721.28, 706.86)
    sizes.write_text('6x4: "0 0 1289.28 710.64"\n8x4: "0 0 1724.16 713.00"\n')
    with pytest.raises(SystemExit):
        g.core_corner("8x4", tt=tmp_path)


def test_harden_reads_tile_and_core_count():
    text = (ROOT / "flow/harden.sh").read_text()
    assert 'tt_block_${TILE}_pgvdd.def' in text
    assert 'if [ "$NC" != "$NC0" ] || [ -n "${STOP:-}" ]; then PRESET=experiment; fi' in text


# The 2-core fallback's search candidate (docs/physical.md, "8x4 settings
# searches"): its run's src/config.json, as flow/harden.sh NAME 2 generated it.
FALLBACK_RUN = ROOT / "evidence/8x4-search-20261111/fallback/aqf-d44l8lcch15"


def test_tuned_fallback_gives_the_searched_runs_config_byte_for_byte(tmp_path):
    # harden.sh NAME 2 runs the generator before setting PE_NCORES 2, so it sees
    # the committed core count and needs --preset experiment --banks 2
    r, dst = generate(tmp_path, "--preset", "experiment", "--banks", "2", "--tuned", "fallback-8x4")
    assert r.returncode == 0, r.stderr
    assert dst.read_bytes() == (FALLBACK_RUN / "config.json").read_bytes()
    spec = (FALLBACK_RUN / "spec.txt").read_text().split()
    assert spec[:3] == ["aqf-d44l8lcch15", "2", "--"]
    r2, dst2 = generate(tmp_path, "--preset", "experiment", "--banks", "2", *spec[3:], name="explicit.json")
    assert r2.returncode == 0 and dst2.read_bytes() == dst.read_bytes()


def test_tuned_options_come_first_and_names_are_checked(tmp_path):
    r, dst = generate(tmp_path, "--preset", "experiment", "--banks", "2", "--tuned=fallback-8x4", "--density", "40")
    assert r.returncode == 0, r.stderr
    c = json.loads(dst.read_text())
    assert c["PL_TARGET_DENSITY_PCT"] == 40 and c["GRT_RESIZER_HOLD_SLACK_MARGIN"] == 0.15
    assert "tuned" not in c["//generator"]
    for bad in (["--tuned", "nope"], ["--tuned", "fallback-8x4", "--tuned", "fallback-8x4"], ["--tuned"],
                ["--tune", "fallback-8x4"], ["--tu", "fallback-8x4"], ["--tune=fallback-8x4"]):
        r, dst = generate(tmp_path, *bad, name="bad.json")
        assert r.returncode != 0 and not dst.exists()


@pytest.mark.parametrize("layout,bank2", [("spread-right", ([869.76, 3.78], "FS")), ("corners-top", ([1302.72, 578.34], "N"))])
def test_three_bank_layouts_keep_core_0_side_free(tmp_path, layout, bank2):
    # bank 2 on the right half, so core 0 keeps the left and centre (screen T5)
    r, dst = generate(tmp_path, "--preset", "experiment", "--banks", "3", "--layout", layout)
    assert r.returncode == 0, r.stderr
    c = json.loads(dst.read_text())
    pl = banks(dst)
    assert pl[0] == ([4.32, 3.78], "FS") and pl[1] == ([1302.72, 3.78], "FS") and pl[2] == bank2
    W, H, halo_x, halo_y = 416.64, 118.78, c["FP_MACRO_HORIZONTAL_HALO"], c["FP_MACRO_VERTICAL_HALO"]
    boxes = [(x, y, x + W, y + H) for (x, y), _ in pl.values()]
    for i, a in enumerate(boxes):
        assert a[2] <= 1721.28 and a[3] <= 706.86                          # inside the core area
        for b in boxes[i + 1:]:
            assert (a[2] + 2 * halo_x <= b[0] or b[2] + 2 * halo_x <= a[0]
                    or a[3] + 2 * halo_y <= b[1] or b[3] + 2 * halo_y <= a[1])   # no overlap, halos clear
    assert len(c["PDN_MACRO_CONNECTIONS"]) == 6
    r, dst = generate(tmp_path, "--preset", "experiment", "--banks", "2", "--layout", layout, name="two.json")
    assert r.returncode != 0 and not dst.exists()


def test_reg_clock_delay_option(tmp_path):
    r, dst = generate(tmp_path, "--preset", "experiment", "--reg-clock-delay", "10")
    assert r.returncode == 0, r.stderr
    c = json.loads(dst.read_text())
    assert c["PROTOEMU_REG_CLOCK_DELAY"] == 10 and c["//generator"]["reg_clock_delay"] == 10
    assert c["meta"]["substituting_steps"]["+OpenROAD.CTS"] == "Project.SramClockAdvance"
    r0, d0 = generate(tmp_path, "--preset", "experiment", name="zero.json")
    assert "PROTOEMU_REG_CLOCK_DELAY" not in json.loads(d0.read_text())
    assert "reg_clock_delay" not in json.loads(d0.read_text())["//generator"]
    r, dg = generate(tmp_path, "--preset", "experiment", "--reg-clock-delay", "4", "--reg-clock-delay-gates", "6", name="gates.json")
    cg = json.loads(dg.read_text())
    assert r.returncode == 0 and cg["PROTOEMU_REG_CLOCK_DELAY"] == 4 and cg["PROTOEMU_REG_CLOCK_DELAY_GATES"] == 6
    assert "PROTOEMU_REG_CLOCK_DELAY_GATES" not in c and "reg_clock_delay_gates" not in c["//generator"]
    for bad in (["--reg-clock-delay", "4", "--no-sram-clock-advance", "--late-clock", "0"], ["--reg-clock-delay", "-1"],
                ["--reg-clock-delay-gates", "6"], ["--reg-clock-delay", "4", "--reg-clock-delay-gates", "-2"]):
        r, d = generate(tmp_path, "--preset", "experiment", *bad, name="bad.json")
        assert r.returncode != 0 and not d.exists()
    # the step runs it after the SRAM advance and before the late clocks; the plugin declares it
    step = (ROOT / "flow/sram_clock_advance_step.tcl").read_text()
    assert step.index("protoemu_sram_clk::run") < step.index("protoemu_reg_clk::run") < step.index("protoemu_late_clk::run")
    plugin = (ROOT / "librelane_plugin_protoemu.py").read_text()
    assert 'Variable("PROTOEMU_REG_CLOCK_DELAY", int,' in plugin and 'Variable("PROTOEMU_REG_CLOCK_DELAY_GATES", int,' in plugin
    assert "PROTOEMU_REG_CLOCK_DELAY_GATES" in step
    assert "namespace eval protoemu_reg_clk" in (ROOT / "flow/sram_clock_advance.tcl").read_text()


def test_late_clock_core_register_list(tmp_path):
    base = ["--preset", "experiment", "--banks", "2", "--tuned", "fallback-8x4"]
    r, full = generate(tmp_path, *base, name="full.json")
    r2, named = generate(tmp_path, *base, "--late-clock-core-regs", "r0,r1,r2,r3,crc", name="named.json")
    assert r.returncode == r2.returncode == 0
    assert full.read_bytes() == named.read_bytes() == (FALLBACK_RUN / "config.json").read_bytes()   # default: unchanged
    r, nocrc = generate(tmp_path, *base, "--late-clock-core-regs", "r0,r1,r2,r3", name="nocrc.json")
    assert r.returncode == 0, r.stderr
    c = json.loads(nocrc.read_text())
    regs = c["PROTOEMU_LATE_CLOCK_REGS"]
    assert not any(".crc" in p for p in regs) and sum(".u_core.r" in p for p in regs) == 4
    assert c["//generator"]["late_clock_core_regs"] == "r0,r1,r2,r3"
    # (the experiment preset: at 2 cores the submission preset itself brings the late clocks)
    for bad in (["--preset", "experiment", "--late-clock-core-regs", "r0"], [*base, "--late-clock-core-regs", "r0,,r1"],
                [*base, "--late-clock-core-regs", "r0,r0"], [*base, "--late-clock-core-regs", "R0"]):
        r, d = generate(tmp_path, *bad, name="bad.json")
        assert r.returncode != 0 and not d.exists()


def test_two_core_8x4_submission_is_the_tuned_fallback(tmp_path):
    # a fallback shipping commit (PE_NCORES 2) regenerates src/config.json with the
    # plain submission preset, which CI's --check and the release gate reproduce
    r, sub = generate(tmp_path, name="sub.json", cores=2)
    r2, tuned = generate(tmp_path, "--preset", "submission", "--tuned", "fallback-8x4", name="tuned.json", cores=2)
    assert r.returncode == r2.returncode == 0, r.stderr + r2.stderr
    assert sub.read_bytes() == tuned.read_bytes()
    c = json.loads(sub.read_text())
    assert c["//generator"]["preset"] == "submission" and c["PL_TARGET_DENSITY_PCT"] == 44
    assert c["GRT_RESIZER_HOLD_SLACK_MARGIN"] == 0.15 and c["PROTOEMU_LATE_CLOCK_STAGES"] == 8
    # with the searched run's options it is that run's config apart from the preset label
    run = json.loads((FALLBACK_RUN / "config.json").read_text())
    assert run["//generator"].pop("preset") == "experiment" and c["//generator"].pop("preset") == "submission"
    assert run == c
    # 6x4 and the experiment preset keep the plain defaults
    _, e = generate(tmp_path, "--preset", "experiment", name="exp.json", cores=2)
    assert json.loads(e.read_text())["PL_TARGET_DENSITY_PCT"] == 34
    # the tuned set comes first: options given to the submission preset override it
    r, o = generate(tmp_path, "--density", "40", "--late-clock", "6", name="override.json", cores=2)
    co = json.loads(o.read_text())
    assert r.returncode == 0 and co["PL_TARGET_DENSITY_PCT"] == 40 and co["PROTOEMU_LATE_CLOCK_STAGES"] == 6
    assert co["GRT_RESIZER_HOLD_SLACK_MARGIN"] == 0.15


# The 3-core candidate (evidence/8x4-search-20261114): run aqp-rt10's generated
# src/config.json and the sha256 of the plugin it ran.
RT10_RUN = ROOT / "evidence/8x4-search-20261114/3core/aqp-rt10"


def test_three_core_8x4_submission_is_rt10_byte_for_byte(tmp_path):
    # the shipping commit: the plain submission preset at 3 cores is rt10's config,
    # and src/config.json is that file; rt10 hardened with the submission preset
    # (flow/harden.sh aqp-rt10 -- <options>) at this plugin
    r, sub = generate(tmp_path, name="sub.json", cores=3)
    assert r.returncode == 0, r.stderr
    assert sub.read_bytes() == (RT10_RUN / "config.json").read_bytes() == (ROOT / "src/config.json").read_bytes()
    r, tuned = generate(tmp_path, "--preset", "submission", "--tuned", "rt10-8x4", name="tuned.json", cores=3)
    assert r.returncode == 0 and tuned.read_bytes() == sub.read_bytes()
    spec = (RT10_RUN / "spec.txt").read_text().split()
    assert spec[:2] == ["aqp-rt10", "--"]
    g = importlib.util.spec_from_file_location("gen_config_rt10", GEN)
    gen = importlib.util.module_from_spec(g)
    g.loader.exec_module(gen)
    assert [x.replace('"', "") for x in gen.TUNED["rt10-8x4"]] == [x.replace('"', "") for x in spec[2:]]
    assert gen.SUBMISSION_TUNED[("8x4", 3)] == "rt10-8x4"
    # its options as rt10 gave them, through the experiment preset: the same config but the label
    r, exp = generate(tmp_path, "--preset", "experiment", *gen.TUNED["rt10-8x4"], name="exp.json", cores=3)
    c, run = json.loads(exp.read_text()), json.loads(sub.read_text())
    assert r.returncode == 0 and c["//generator"].pop("preset") == "experiment" and run["//generator"].pop("preset") == "submission"
    assert c == run
    plugin = hashlib.sha256((ROOT / "librelane_plugin_protoemu.py").read_bytes()).hexdigest()
    assert (RT10_RUN / "plugin.sha256").read_text().split() == [plugin, "librelane_plugin_protoemu.py"]


def test_the_tuned_sets_leave_6x4_and_the_other_core_counts_alone(tmp_path):
    # 6x4 stays the plain submission preset at every core count: the two tuned sets
    # apply to their own (8x4, cores) only
    for cores in (1, 2, 3, 4):
        info = info_with(tmp_path, "6x4")
        r, d = generate(tmp_path, "--info", str(info), name=f"6x4-{cores}.json", cores=cores)
        r2, e = generate(tmp_path, "--info", str(info), "--preset", "experiment", name=f"6x4-{cores}-exp.json", cores=cores)
        assert r.returncode == r2.returncode == 0, r.stderr + r2.stderr
        a, b = json.loads(d.read_text()), json.loads(e.read_text())
        assert a["//generator"].pop("preset") == "submission" and b["//generator"].pop("preset") == "experiment"
        assert a == b


def test_sram_advance_toward_option(tmp_path):
    r, d = generate(tmp_path, "--preset", "experiment", "--sram-advance-toward", "0.5", name="toward.json")
    c = json.loads(d.read_text())
    assert r.returncode == 0 and c["PROTOEMU_SRAM_ADVANCE_TOWARD"] == 0.5 and c["//generator"]["sram_advance_toward"] == 0.5
    r0, d0 = generate(tmp_path, "--preset", "experiment", name="plain.json")
    c0 = json.loads(d0.read_text())
    assert "PROTOEMU_SRAM_ADVANCE_TOWARD" not in c0 and "sram_advance_toward" not in c0["//generator"]
    for bad in (["--sram-advance-toward", "1.5"], ["--sram-advance-toward", "-0.5"],
                ["--sram-advance-toward", "0.5", "--no-sram-clock-advance", "--late-clock", "0"]):
        r, d = generate(tmp_path, "--preset", "experiment", *bad, name="bad.json")
        assert r.returncode != 0 and not d.exists()
    tcl = (ROOT / "flow/sram_clock_advance.tcl").read_text()
    assert "if {[info exists ::env(PROTOEMU_SRAM_ADVANCE_TOWARD)]} { set f $::env(PROTOEMU_SRAM_ADVANCE_TOWARD) }" in tcl
    assert "set f 0.0" in tcl


def test_reg_clock_delay_is_off_unless_asked():
    # a default harden must run the SRAM clock step exactly as before the option existed
    plugin = (ROOT / "librelane_plugin_protoemu.py").read_text()
    d = plugin_defaults()
    assert d[("SramClockAdvanceTcl", "PROTOEMU_SRAM_ADVANCE_TOWARD")] == 0.0
    assert d[("SramClockAdvanceTcl", "PROTOEMU_REG_CLOCK_DELAY")] == 0
    assert d[("SramClockAdvanceTcl", "PROTOEMU_REG_CLOCK_DELAY_GATES")] == -1
    step = (ROOT / "flow/sram_clock_advance_step.tcl").read_text()
    assert "[info exists ::env(PROTOEMU_REG_CLOCK_DELAY)] && $::env(PROTOEMU_REG_CLOCK_DELAY) > 0" in step


def test_post_repair_hold_option(tmp_path):
    # opt-in: the inserted last design repair becomes design repair then hold repair
    r, d = generate(tmp_path, "--preset", "experiment", "--post-repair-hold", name="hold.json")
    c = json.loads(d.read_text())
    assert r.returncode == 0 and c["//generator"]["post_repair_hold"] is True
    assert c["meta"]["substituting_steps"]["+Project.SramPinBuffers"] == "Project.RepairDesignThenHold"
    r0, d0 = generate(tmp_path, "--preset", "experiment", name="plain.json")
    c0 = json.loads(d0.read_text())
    assert c0["meta"]["substituting_steps"]["+Project.SramPinBuffers"] == "OpenROAD.RepairDesignPostGRT"
    assert "post_repair_hold" not in c0["//generator"]
    r, d = generate(tmp_path, "--preset", "experiment", "--post-repair-hold", "--no-sram-pin-buffers", name="bad.json")
    assert r.returncode != 0 and not d.exists()
    # the plugin: the hold step runs its own script, and the composite chains exactly
    # the design repair and that step (not LibreLane's full setup+hold script)
    plugin = (ROOT / "librelane_plugin_protoemu.py").read_text()
    blocks = {m.group(1): m.group(2) for m in re.finditer(r"^class (\w+)\(.*?\):\n((?:(?:    .*)?\n)+)", plugin, re.M)}
    hold = blocks["HoldRepairPostGRT"]
    assert 'id = "Project.HoldRepairPostGRT"' in hold
    assert re.search(r'def get_script_path\(self\):\n\s+return os\.path\.join\(HERE, "hold_repair_postgrt\.tcl"\)', hold)
    both = blocks["RepairDesignThenHold"]
    assert 'id = "Project.RepairDesignThenHold"' in both
    assert re.findall(r"^\s+Steps = \[(.*)\]$", both, re.M) == ["LastRepairDesignPostGRT, HoldRepairPostGRT"]
    last = blocks["LastRepairDesignPostGRT"]
    assert last.startswith('    """') and "def get_script_path" not in last    # LibreLane's design repair script
    assert re.search(r"^class LastRepairDesignPostGRT\(RepairDesignPostGRT\):", plugin, re.M)
    # the script: one repair_timing call, on hold_args, which carry -hold and no setup pass
    tcl = (ROOT / "flow/hold_repair_postgrt.tcl").read_text()
    code = [l.split("#", 1)[0].strip() for l in tcl.splitlines()]
    assert [l for l in code if "repair_timing" in l] == ["log_cmd repair_timing {*}$hold_args"]
    assert "lappend hold_args -hold" in code
    assert not [l for l in code if re.search(r"(^|\s)-setup(\s|$)", l)]
    assert "setup_args" not in tcl
    assert "estimate_parasitics -global_routing" in code


def test_last_repair_margins_apply_to_the_last_repair_only(tmp_path):
    r, d = generate(tmp_path, "--preset", "experiment", "--post-repair-hold", "--last-repair-slew-pct", "50",
                    "--last-repair-cap-pct", "40", name="last.json")
    c = json.loads(d.read_text())
    assert r.returncode == 0, r.stderr
    assert c["PROTOEMU_LAST_REPAIR_MAX_SLEW_PCT"] == 50 and c["PROTOEMU_LAST_REPAIR_MAX_CAP_PCT"] == 40
    assert c["GRT_DESIGN_REPAIR_MAX_SLEW_PCT"] == 30 and c["GRT_DESIGN_REPAIR_MAX_CAP_PCT"] == 30   # the earlier repairs
    r0, d0 = generate(tmp_path, "--preset", "experiment", "--post-repair-hold", name="plain.json")
    c0 = json.loads(d0.read_text())
    assert not [k for k in c0 if k.startswith("PROTOEMU_LAST_REPAIR")]
    assert not [k for k in c0["//generator"] if k.startswith("last_repair")]
    for bad in (["--last-repair-slew-pct", "50"], ["--post-repair-hold", "--last-repair-slew-pct", "150"],
                ["--post-repair-hold", "--last-repair-cap-pct", "-1"]):
        r, b = generate(tmp_path, "--preset", "experiment", *bad, name="bad.json")
        assert r.returncode != 0 and not b.exists()
    # the step overrides LibreLane's margin variables in its own environment only
    plugin = (ROOT / "librelane_plugin_protoemu.py").read_text()
    for own, base in (("PROTOEMU_LAST_REPAIR_MAX_SLEW_PCT", "GRT_DESIGN_REPAIR_MAX_SLEW_PCT"),
                      ("PROTOEMU_LAST_REPAIR_MAX_CAP_PCT", "GRT_DESIGN_REPAIR_MAX_CAP_PCT")):
        assert f'("{own}", "{base}")' in plugin
    assert "env[base] = str(self.config[own])" in plugin


def test_option_1_address_buffers_second_pass_transition_limit(tmp_path):
    # window 5's option 1, each opt-in: buf_8-driven SRAM address nets, a second
    # design-repair + hold pass, a PnR-only 1.0 ns transition limit
    on = ["--preset", "experiment", "--post-repair-hold", "--post-repair-hold-passes", "2", "--sram-addr-buf8",
          "--pnr-max-transition"]
    r, d = generate(tmp_path, *on, name="opt1.json")
    c = json.loads(d.read_text())
    assert r.returncode == 0, r.stderr
    assert c["meta"]["substituting_steps"]["+Project.SramPinBuffers"] == "Project.RepairDesignThenHold2"
    assert c["PROTOEMU_SRAM_ADDR_BUF8"] is True
    assert c["PNR_SDC_FILE"] == "dir::../flow/protoemu_pnr_transition.sdc"
    g = c["//generator"]
    assert g["post_repair_hold_passes"] == 2 and g["sram_addr_buf8"] is True and g["pnr_max_transition"] is True
    r0, d0 = generate(tmp_path, "--preset", "experiment", "--post-repair-hold", name="hold.json")
    c0 = json.loads(d0.read_text())
    assert c0["meta"]["substituting_steps"]["+Project.SramPinBuffers"] == "Project.RepairDesignThenHold"
    assert "PROTOEMU_SRAM_ADDR_BUF8" not in c0 and c0["PNR_SDC_FILE"] == "dir::protoemu_pnr_margin.sdc"
    assert not {"post_repair_hold_passes", "sram_addr_buf8", "pnr_max_transition"} & set(c0["//generator"])
    for bad in (["--post-repair-hold-passes", "2"], ["--sram-addr-buf8", "--no-sram-pin-buffers"],
                ["--pnr-max-transition", "--no-pnr-setup-margin"], ["--pnr-max-transition", "--pnr-electrical-margins"],
                ["--post-repair-hold", "--post-repair-hold-passes", "3"]):
        r, b = generate(tmp_path, "--preset", "experiment", *bad, name="bad.json")
        assert r.returncode != 0 and not b.exists()
    plugin = (ROOT / "librelane_plugin_protoemu.py").read_text()
    blocks = {m.group(1): m.group(2) for m in re.finditer(r"^class (\w+)\(.*?\):\n((?:(?:    .*)?\n)+)", plugin, re.M)}
    assert re.findall(r"^\s+Steps = \[(.*)\]$", blocks["RepairDesignThenHold2"], re.M) == [
        "LastRepairDesignPostGRT, HoldRepairPostGRT, LastRepairDesignPostGRT, HoldRepairPostGRT"]
    assert plugin_defaults()[("SramPinBuffersTcl", "PROTOEMU_SRAM_ADDR_BUF8")] is False
    tcl = (ROOT / "flow/sram_pin_buffers.tcl").read_text()
    assert "[info exists ::env(PROTOEMU_SRAM_ADDR_BUF8)]" in tcl and "-buffer_cell sg13cmos5l_buf_8" in tcl
    sdc = [l for l in (ROOT / "flow/protoemu_pnr_transition.sdc").read_text().splitlines() if l and not l.startswith("#")]
    assert sdc[-1] == "set_max_transition 1.0 [current_design]"
    assert any("src protoemu_pnr_margin.sdc" in l for l in sdc) and "source $::env(PNR_SDC_FILE)" in sdc
    assert sdc.index("source $::env(PNR_SDC_FILE)") < sdc.index("set ::env(PNR_SDC_FILE) $_protoemu_pnr_sdc")


def test_096_option_1_mutants_and_refusals(tmp_path):
    # 096 finding 3: two passes without the SRAM pin buffers (no hold step) is refused
    r, d = generate(tmp_path, "--preset", "experiment", "--no-sram-pin-buffers", "--post-repair-hold-passes", "2",
                    name="bad.json")
    assert r.returncode != 0 and not d.exists()
    # finding 5: the transition SDC is PnR-only; signoff keeps the plain SDC
    r, d = generate(tmp_path, "--preset", "experiment", "--pnr-max-transition", name="tr.json")
    c = json.loads(d.read_text())
    assert c["SIGNOFF_SDC_FILE"] == "dir::protoemu.sdc" and c["STA_EXTRA_CORNER_TCL_FILE"] == "dir::timing_checks.tcl"
    assert [k for k, v in c.items() if isinstance(v, str) and "protoemu_pnr_transition" in v] == ["PNR_SDC_FILE"]
    # finding 5 (097 finding 1): LastRepairDesignPostGRT is LibreLane's design repair
    # unless its margins are set: both default to None, and prepare_env overrides
    # exactly those two of LibreLane's variables, nothing else
    import ast
    d = plugin_defaults()
    assert d[("LastRepairDesignPostGRT", "PROTOEMU_LAST_REPAIR_MAX_SLEW_PCT")] is None
    assert d[("LastRepairDesignPostGRT", "PROTOEMU_LAST_REPAIR_MAX_CAP_PCT")] is None
    cls = next(n for n in plugin_tree().body if isinstance(n, ast.ClassDef) and n.name == "LastRepairDesignPostGRT")
    env = next(f for f in cls.body if isinstance(f, ast.FunctionDef) and f.name == "prepare_env")
    loop = next(n for n in ast.walk(env) if isinstance(n, ast.For))
    assert ast.literal_eval(loop.iter) == (("PROTOEMU_LAST_REPAIR_MAX_SLEW_PCT", "GRT_DESIGN_REPAIR_MAX_SLEW_PCT"),
                                           ("PROTOEMU_LAST_REPAIR_MAX_CAP_PCT", "GRT_DESIGN_REPAIR_MAX_CAP_PCT"))
    writes = [n for n in ast.walk(env) if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Subscript)]
    assert [ast.unparse(n) for n in writes] == ["env[base] = str(self.config[own])"]
    guard = next(n for n in ast.walk(loop) if isinstance(n, ast.If))
    assert ast.unparse(guard.test) == "self.config[own] is not None"
    # finding 5 (097 finding 1): the opt-in address branch, line for line (comments
    # aside): its condition, the buffer-driver sizing, the buf_8 at the driver with
    # the pin and its diode as loads, and continue before the old _1 -> _2 swap
    tcl = (ROOT / "flow/sram_pin_buffers.tcl").read_text()
    lines = [l.strip() for l in tcl.splitlines()]
    i = lines.index("if {[string match A_ADDR* $m] && [info exists ::env(PROTOEMU_SRAM_ADDR_BUF8)]")
    j = lines.index("if {[string match A_ADDR* $m]} {", i)
    code = [l for l in lines[i:j] if l and not l.startswith("#")]
    assert code == ADDR_BUF8_BRANCH
    # nothing outside the golden lines reads the variable (097b: an early continue before the branch)
    assert [l for l in lines if "PROTOEMU_SRAM_ADDR_BUF8" in l] == ADDR_BUF8_BRANCH[:2]
    # the variable is a bool, as its LibreLane type (097b: typed str would pass the Tcl's "True")
    import ast
    cls = next(n for n in plugin_tree().body if isinstance(n, ast.ClassDef) and n.name == "SramPinBuffersTcl")
    var = next(c for c in ast.walk(cls) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "Variable"
               and ast.literal_eval(c.args[0]) == "PROTOEMU_SRAM_ADDR_BUF8")
    assert ast.unparse(var.args[1]) == "bool"


ADDR_BUF8_BRANCH = [
    "if {[string match A_ADDR* $m] && [info exists ::env(PROTOEMU_SRAM_ADDR_BUF8)]",
    "&& $::env(PROTOEMU_SRAM_ADDR_BUF8) in {1 true True}} {",
    "if {[string match sg13cmos5l_buf_* $dm]} {",
    "if {$dm ni {sg13cmos5l_buf_8 sg13cmos5l_buf_16}} {",
    'set cell [get_cells [string map [list "\\\\\\[" ? "\\\\\\]" ? "\\[" ? "\\]" ?] [[$drv getInst] getName]]]',
    'if {[llength $cell] != 1} { error "cell lookup failed: [[$drv getInst] getName]" }',
    "replace_cell $cell [lindex [get_lib_cells */sg13cmos5l_buf_8] 0]",
    'puts "SRAM_PIN_BUFFERS [$inst getName]/$m: address driver $dm -> sg13cmos5l_buf_8"',
    "incr nup",
    "}",
    "continue",
    "}",
    "set loads [list [pname $it]]",
    "foreach o [$net getITerms] {",
    "set on [[$o getInst] getName]",
    'if {[$o getIoType] eq "INPUT" && [[[$o getInst] getMaster] getName] eq "sg13cmos5l_antennanp"',
    '&& [string first "ANTENNA_" $on] == 0 && [string first "sram" $on] >= 0} {',
    "lappend loads [pname $o]",
    "}",
    "}",
    "set pins [get_pins $loads]",
    'if {[llength $pins] != [llength $loads]} { error "pin lookup failed: $loads" }',
    "lassign [[$drv getInst] getLocation] dx dy",
    "insert_buffer -buffer_cell sg13cmos5l_buf_8 -load_pins $pins \\",
    "-buffer_name sram_addrbuf_ -net_name sram_addrnet_ \\",
    "-location [list [ord::dbu_to_microns $dx] [ord::dbu_to_microns $dy]]",
    'puts "SRAM_PIN_BUFFERS [$inst getName]/$m: buf_8 at its $dm driver"',
    "incr nin",
    "continue",
    "}",
]
