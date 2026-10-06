#!/usr/bin/env python3
"""Generate src/config.json for the SRAM build.

    python3 flow/gen_config.py --banks 2 [--density 44] [--grt-adjust 0.10]

The submission preset uses swapped bottom corners; signoff times the real latch arcs.
Experimental overrides must be named and recorded separately from submission.
"""
import argparse
import collections
import json
import pathlib

M = "RM_IHPSG13_1P_256x32_c2_bm_bist"
P = "pdk_dir::libs.ref/sg13cmos5l_sram"
W, H = 416.64, 118.78
CORE_X1, CORE_Y1 = 1286.40, 706.86  # core area top-right (6x4 tile)
ROW = 3.78
SITE = 0.48


def inst(b):
    return f"u_chip.u_imem.g_bank[{b}].g_on.u_bank.sram"


def esc(b):
    return f"u_chip\\.u_imem\\.g_bank\\\\?\\[{b}\\\\?\\]\\.g_on\\.u_bank\\.sram"


def snap(v, g):
    return round(int(v / g) * g, 2)


def placements(nbanks, layout="top"):
    """top:   banks 0-1 along the top edge from the right (pins down), banks 2-3
              along the bottom edge (mirrored, pins up).
    sides: macros rotated 90 degrees on the right and left die edges, pins facing
           inward, starting at the bottom (bank 0 right, 1 left, 2 right, 3 left);
           the top band stays free for the I/O pins (all at the top-left).
    The top-left corner, where all the TT I/O pins are, stays free either way."""
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
    ap.add_argument("--banks", type=int, choices=range(1, 5), default=2)
    # Signed off (run v2c, 2026-10-06). At GRT_ADJUSTMENT 0.15 global routing
    # reserved too much Metal3 and ended with ~100 overflows that detailed
    # routing couldn't clear; 0.10 leaves ~10-40, which it does. Density also
    # steers routability-driven placement here (docs/physical.md).
    ap.add_argument("--density", type=int, default=34)
    ap.add_argument("--grt-adjust", type=float, default=0.10)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--every-other", action=argparse.BooleanOptionalAction, default=True,
                    help="extra SRAM array stripe pairs (prism's setting)")
    ap.add_argument("--layout", choices=["top", "sides", "corners", "corners-swap", "row"], default="corners-swap")
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
    ap.add_argument("--clock-fanout-fix", action="store_true",
                    help="opt-in CTS clusters of eight and IHP clock branch repair; requires fresh physical validation")
    ap.add_argument("--pnr-setup-margin", action="store_true",
                    help="PnR SDC with 0.75 ns more setup uncertainty (src/protoemu_pnr_margin.sdc); signoff unchanged")
    ap.add_argument("--final-design-repair", action="store_true",
                    help="repeat post-GRT design repair (slew/cap/fanout) after post-GRT timing repair")
    ap.add_argument("--late-clock", type=int, default=0, metavar="STAGES",
                    help="with --sram-clock-advance: delay the chip's pin registers' clocks by STAGES buffers")
    ap.add_argument("--late-clock-oe", type=int, default=0, metavar="STAGES",
                    help="with --late-clock: pin_oe gets STAGES buffers instead (it only drives pads)")
    ap.add_argument("--late-clock-core", type=int, default=0, metavar="STAGES",
                    help="with --late-clock: also delay core 0's R0-R3 and CRC registers by STAGES buffers")
    ap.add_argument("--sram-clock-advance", action="store_true",
                    help="clock each SRAM from the clock root (useful skew: reads launch earlier, flow/sram_clock_advance.tcl)")
    a = ap.parse_args()
    if a.pnr_latch_flops is None:
        # PnR times the FIFO latches like flops (protoemu_pnr.sdc); signoff
        # always times the real latch arcs. Same repair edits, ~20x faster
        # repair steps (docs/review.md).
        a.pnr_latch_flops = True
    if a.preset == "submission" and (a.banks != 2 or a.grt_iters is not None):
        ap.error("submission requires 2 banks and no truncated routing; use --preset experiment")
    c = collections.OrderedDict()
    c["meta"] = {"flow": "Classic", "substituting_steps": {
        "+OpenROAD.GeneratePDN": "Project.ExtendPowerStripes",
        "+OpenROAD.ResizerTimingPostCTS": "Project.DiodesOnSramPins"}}
    if a.sram_clock_advance:
        # one step after CTS; it runs the clock fanout repair too when asked
        c["meta"]["substituting_steps"]["+OpenROAD.CTS"] = "Project.SramClockAdvance"
    elif a.clock_fanout_fix:
        c["meta"]["substituting_steps"]["+OpenROAD.CTS"] = "Project.FixClockFanout"
    if a.final_design_repair:
        # Timing and hold repair after global routing resize and add cells
        # without slew/cap/fanout margins, and the SRAM pins' limits are tight
        # (0.595 ns transition in, 0.064 pF out): repair them again last.
        c["meta"]["substituting_steps"]["+OpenROAD.ResizerTimingPostGRT"] = "OpenROAD.RepairDesignPostGRT"
    c["//"] = ("Generated by flow/gen_config.py. TT SG13CMOS5L 6x4 with IHP 256x32 SRAM IMEM banks; "
               "SRAM settings follow kdp1965/ihp-um-janestreet-prism.")
    c["//generator"] = {k: v for k, v in vars(a).items() if k not in ("output", "check")
                        and (k not in ("clock_fanout_fix", "sram_clock_advance", "final_design_repair", "late_clock", "late_clock_oe", "late_clock_core", "pnr_setup_margin", "set") or v)}
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
            c["PROTOEMU_LATE_CLOCK_REGS"] += [f"u_chip.g_core*0*.g_on.u_core.{r}??[0-9]*={a.late_clock_core}"
                                              for r in ("r0", "r1", "r2", "r3", "crc")]
        c["PROTOEMU_LATE_CLOCK_STAGES"] = a.late_clock
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
    # three clean runs missed it by ~10 ps; 0.10 (runs m10, v2a-c) keeps it
    # positive and cleared the typ slew exceptions.
    c["GRT_RESIZER_HOLD_SLACK_MARGIN"] = 0.10
    c["RUN_LINTER"] = 1
    c["LINTER_INCLUDE_PDK_MODELS"] = 1
    c["//clocks"] = "clk, and the host port's SCK on ui_in[6]; src/protoemu.sdc constrains both"
    c["CLOCK_PORT"] = ["clk", "ui_in[6]"]
    c["PNR_SDC_FILE"] = "dir::protoemu_pnr.sdc" if a.pnr_latch_flops else "dir::protoemu.sdc"
    if a.pnr_setup_margin:
        if not a.pnr_latch_flops:
            ap.error("--pnr-setup-margin builds on the PnR latch SDC")
        c["PNR_SDC_FILE"] = "dir::protoemu_pnr_margin.sdc"
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
    pl = placements(a.banks, a.layout)
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
    for item in a.set:
        key, value = item.split("=", 1)
        c[key] = json.loads(value)
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
