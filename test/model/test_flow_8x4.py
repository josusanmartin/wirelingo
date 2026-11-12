"""flow/gen_config.py on Tiny Tapeout's 6x4 and 8x4 IHP tiles, and N SRAM banks.

The tile comes from info.yaml. A 6x4 info.yaml with a 2-core project.v must
still give the signed-off run fo6's configuration byte for byte; on 8x4, with
a 2-core project.v, only the right-hand bank moves."""
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


def test_6x4_info_gives_fo6_config_byte_for_byte(tmp_path):
    r, dst = generate(tmp_path, "--info", str(info_with(tmp_path, "6x4")), cores=2)
    assert r.returncode == 0, r.stderr
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == FO6_CONFIG


def test_8x4_moves_only_the_right_bank(tmp_path):
    r6, c6 = generate(tmp_path, "--info", str(info_with(tmp_path, "6x4")), name="c6.json", cores=2)
    r8, c8 = generate(tmp_path, "--info", str(info_with(tmp_path, "8x4")), name="c8.json", cores=2)
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
    for bad in (["--reg-clock-delay", "4", "--no-sram-clock-advance", "--late-clock", "0"], ["--reg-clock-delay", "-1"]):
        r, d = generate(tmp_path, "--preset", "experiment", *bad, name="bad.json")
        assert r.returncode != 0 and not d.exists()
    # the step runs it after the SRAM advance and before the late clocks; the plugin declares it
    step = (ROOT / "flow/sram_clock_advance_step.tcl").read_text()
    assert step.index("protoemu_sram_clk::run") < step.index("protoemu_reg_clk::run") < step.index("protoemu_late_clk::run")
    assert 'Variable("PROTOEMU_REG_CLOCK_DELAY", int,' in (ROOT / "librelane_plugin_protoemu.py").read_text()
    assert "namespace eval protoemu_reg_clk" in (ROOT / "flow/sram_clock_advance.tcl").read_text()
