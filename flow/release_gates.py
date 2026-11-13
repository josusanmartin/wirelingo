#!/usr/bin/env python3
"""Release gates on a hardened run's clock/reset checks and SRAM power.

    flow/release_gates.py RUN_WORKTREE [--checks-receipt R]    # read only; prints each gate

flow/release_check.py calls the same functions. Each gate fails on a
violation and on missing evidence; the only exceptions are the narrow ones
listed here, each with its reason, and none of them excuses a violation.

* Clock and reset checks: the run's own gating_pulse_reset.rpt (src/timing_checks.tcl,
  violators only; read from each corner's sta.log, a candidate file) must be
  present and clean, and a checks receipt (flow/check_rerun.py) must show, at
  every corner, a met worst check of minimum pulse width, a pulse width check
  on every register clock pin of every clock and on every SRAM clock pin, met
  recovery and removal checks, and no violator of any kind; each report's
  corner header must be its directory's.
* ExtendPowerStripes: its log (flow/odb_stripes.py) must have finished both
  nets, give every SRAM power stripes in each of its three regions, and print
  no warning or error beyond the listed exceptions.
* Macro power: in the final DEF, every power pin of every macro instance is on
  the net PDN_MACRO_CONNECTIONS names and is touched, on its own layer, by a
  stripe of that net in each half of the macro, and by no stripe of another net.
"""
import argparse
import os
from pathlib import Path
import re
import sys

FLOW = Path(__file__).resolve().parent
sys.path.insert(0, str(FLOW))
import release_receipts as receipts  # noqa: E402

CHECK_KINDS = ("min_pulse_width", "min_period", "recovery", "removal", "clock_gating_setup", "clock_gating_hold")
REQUIRED_KINDS = ("min_pulse_width", "recovery", "removal")
_GATING = ("the clock gates are the library's integrated clock-gating cells (sg13cmos5l_lgcp_1 and slgcp_1 "
           "are clock_gating_integrated_cell), whose enable is timed by their setup/hold arcs with every "
           "other register; OpenSTA infers clock-gating checks only for gating built from plain logic")
# A kind of check that may have no check at all, and why. Never a violation.
NO_CHECK_EXCEPTIONS = {
    "min_period": ("no library the chip uses (the IHP SG13CMOS5L standard cell, SRAM and I/O .lib files) "
                   "defines a minimum_period arc or a min_period attribute, so OpenSTA has no minimum-period "
                   "check; the clock period is bounded by setup/hold and pulse width, which are gated"),
    "clock_gating_setup": _GATING,
    "clock_gating_hold": _GATING,
}
SLACK_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s+slack \((MET|VIOLATED)\)\s*$", re.M)
SRAM_PREFIX = "RM_IHPSG13_"
SRAM_CLOCK_PIN = "A_CLK"
STRIPES_STEP = "*-project-extendpowerstripes"
# Warnings or errors in the ExtendPowerStripes log that do not fail the gate.
STRIPE_LOG_EXCEPTIONS = [
    (r"\[WARNING ORD-0039\] \.openroad ignored with -python",
     "OpenROAD's notice that it skips the user's .openroad start-up file when it runs a Python script; "
     "it says nothing about the power grid"),
]
STRIPE_REGIONS_RE = re.compile(r"^\[INFO\] (\S+): array L: (\d+) pairs, band: (\d+) pairs, array R: (\d+) pairs",
                               re.M)


def archive_shaped(worktree):
    """An evidence archive (as for an STA receipt): nothing at all stands at the
    final STA step's state_in.json and config.json."""
    step = receipts.sta_step(worktree)
    return not any(os.path.lexists(step / name) for name in ("state_in.json", "config.json"))


def report_section(text, name):
    """The body of one %OL_CREATE_REPORT section printed in an STA log, or None."""
    m = re.search(rf"^%OL_CREATE_REPORT {re.escape(name)}\n(.*?)^%OL_END_REPORT$", text, re.M | re.S)
    return m[1] if m else None


def sections(text):
    """'== title' sections of a check report."""
    out, title = {}, None
    for line in text.splitlines():
        if line.startswith("== "):
            title = line[3:].strip()
            out[title] = []
        elif title is not None:
            out[title].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def slacks(text):
    return [(float(value), state) for value, state in SLACK_RE.findall(text)]


def violated(text):
    """Lines of a report that show a violation."""
    return [line.strip() for line in text.splitlines()
            if "VIOLATED" in line or any(v < 0 for v, _ in slacks(line))]


def worst(text):
    found = slacks(text)
    return min(v for v, _ in found) if found else None


def sram_clock_pins(resolved):
    pins = []
    for macro, spec in (resolved.get("MACROS") or {}).items():
        if macro.startswith(SRAM_PREFIX):
            pins += [f"{inst}/{SRAM_CLOCK_PIN}" for inst in (spec.get("instances") or {})]
    return sorted(pins)


def pin_blocks(text):
    blocks, pin = {}, None
    for line in text.splitlines():
        if line.startswith("pin "):
            pin = line[4:].strip()
            blocks[pin] = []
        elif pin is not None:
            blocks[pin].append(line)
    return {k: "\n".join(v) for k, v in blocks.items()}


PULSE_ROW_RE = re.compile(r"^(\S+) \((?:high|low)\)\s+-?[\d.]+\s+-?[\d.]+\s+-?[\d.]+ \((?:MET|VIOLATED)\)", re.M)


def clock_register_pins(text):
    """{clock: [register clock pins]} from pulse_checks.tcl's 'clock register pins'."""
    out, clock = {}, None
    for line in text.splitlines():
        if line.startswith("clock "):
            clock = line.split()[1]
            out[clock] = []
        elif line.strip() and clock is not None:
            out[clock].append(line.strip())
    return out


def check_reports(checks_dir, corner):
    d = Path(checks_dir)
    return {name: (d / name / "sta" / corner / receipts.CHECK_REPORTS[name]).read_text(errors="replace")
            for name in receipts.CHECK_PASSES}


def timing_check_failures(worktree, checks_dir):
    """(failures, notes): the run's own violator report at each corner, and the
    checks receipt's evidence (checks_dir; None when there is no receipt)."""
    wt = Path(worktree)
    failures, notes = [], []
    sta = receipts.sta_step(wt)
    resolved = receipts.read_json(wt / "runs/wokwi/resolved.json")
    pins = sram_clock_pins(resolved)
    for corner in receipts.CORNERS:
        own = report_section((sta / corner / "sta.log").read_text(errors="replace"), "gating_pulse_reset.rpt")
        if own is None:
            failures.append(f"{corner}: the run's gating_pulse_reset.rpt (src/timing_checks.tcl) is not in its sta.log")
        elif violated(own):
            failures.append(f"{corner}: the run's gating_pulse_reset.rpt shows violations: {violated(own)[:5]}")
    if checks_dir is None:
        failures.append("no clock/reset check receipt: the run's own report lists violators only, and OpenSTA "
                        "reports no pulse width check under the signoff SDC; run flow/check_rerun.py "
                        "and pass --checks-receipt")
        return failures, notes
    excused = {}
    for corner in receipts.CORNERS:
        reports = check_reports(checks_dir, corner)
        signoff, pulse = sections(reports["signoff"]), sections(reports["pulse"])
        for name, found in (("signoff", signoff), ("pulse", pulse)):
            if f"corner {corner}" not in found:
                failures.append(f"{corner}: the {name} pass's report is not this corner's")
        # every register clock pin of every clock has a pulse width check: a
        # partial loss of checks (an exception's -from clock) shows here
        registers = clock_register_pins(pulse.get("clock register pins", ""))
        table = pulse.get("all pulse width checks")
        if not registers or table is None:
            failures.append(f"{corner}: the pulse pass's report lists no clock register pins or no pulse width checks")
        else:
            checked = set(PULSE_ROW_RE.findall(table))
            if violated(table):
                failures.append(f"{corner}: pulse width violated: {violated(table)[:5]}")
            for clock, clock_pins in sorted(registers.items()):
                unchecked = [pin for pin in clock_pins if pin not in checked]
                if unchecked:
                    failures.append(f"{corner}: clock {clock}: {len(unchecked)} of {len(clock_pins)} register clock pins "
                                    f"have no pulse width check (first: {unchecked[0]})")
                else:
                    notes.append(f"{corner}: clock {clock}: all {len(clock_pins)} register clock pins have a pulse "
                                 "width check")
        for kind in CHECK_KINDS:
            source = pulse if kind == "min_pulse_width" else signoff
            pass_name = "pulse" if kind == "min_pulse_width" else "signoff"
            body, bad = source.get(f"worst {kind}"), source.get(f"violators {kind}")
            if body is None or bad is None:
                failures.append(f"{corner}: {kind}: missing from the {pass_name} pass's report")
                continue
            if violated(body) or slacks(bad) or violated(bad):
                failures.append(f"{corner}: {kind} violated: {(violated(body) + violated(bad))[:5]}")
            elif worst(body) is None:
                if kind in NO_CHECK_EXCEPTIONS:
                    excused.setdefault(kind, []).append(corner)
                else:
                    failures.append(f"{corner}: {kind}: no check found ({pass_name} pass)")
            else:
                notes.append(f"{corner}: {kind}: worst slack {worst(body):g} ns (met)")
        # the full SDC's pulse width report: empty under OpenSTA 2.7.0, never a violation
        if violated(signoff.get("worst min_pulse_width", "")) or slacks(signoff.get("violators min_pulse_width", "")):
            failures.append(f"{corner}: min_pulse_width violated under the signoff SDC")
        blocks = pin_blocks(pulse.get("sram clock pins", ""))
        for pin in pins:
            found = slacks(blocks.get(pin, ""))
            if not found:
                failures.append(f"{corner}: SRAM clock pin {pin}: no pulse width check")
            elif any(v < 0 or s != "MET" for v, s in found):
                failures.append(f"{corner}: SRAM clock pin {pin}: pulse width violated")
            else:
                notes.append(f"{corner}: SRAM clock pin {pin}: {len(found)} pulse width checks, "
                             f"worst slack {min(v for v, _ in found):g} ns (met)")
        if not pins:
            failures.append(f"{corner}: resolved.json names no SRAM macro instance to check")
    for kind, corners in excused.items():
        notes.append(f"{kind}: no check at {', '.join(corners)} (exception: {NO_CHECK_EXCEPTIONS[kind]})")
    return failures, notes


def check_summary(checks_dir):
    """One line per corner and kind, for the operator (flow/check_rerun.py)."""
    lines = []
    for corner in receipts.CORNERS:
        reports = check_reports(checks_dir, corner)
        for pass_name, text in reports.items():
            for title, body in sections(text).items():
                w = worst(body)
                if title.startswith("worst "):
                    lines.append(f"{corner} {pass_name} {title}: " + ("no check" if w is None else f"slack {w:g} ns"))
                elif title.startswith("violators "):
                    lines.append(f"{corner} {pass_name} {title}: {max(len(slacks(body)), len(violated(body)))}")
                elif title == "sram clock pins":
                    for pin, block in pin_blocks(body).items():
                        lines.append(f"{corner} {pass_name} {pin}: "
                                     + ("no check" if worst(block) is None else f"worst slack {worst(block):g} ns"))
    return lines


def stripes_failures(worktree, instances):
    """(failures, notes) from the ExtendPowerStripes step log (flow/odb_stripes.py)."""
    run = Path(worktree) / "runs/wokwi"
    failures, notes = [], []
    steps = sorted(run.glob(STRIPES_STEP))
    if len(steps) != 1:
        return [f"expected one ExtendPowerStripes step, found {len(steps)}"], notes
    log = steps[0] / "project-extendpowerstripes.log"
    if not log.is_file():
        return [f"no ExtendPowerStripes log: {log.relative_to(Path(worktree))}"], notes
    text = log.read_text(errors="replace")
    excused = {}
    for line in text.splitlines():
        if "[WARNING" not in line and "[ERROR" not in line and "Traceback" not in line:
            continue
        reason = next((r for pattern, r in STRIPE_LOG_EXCEPTIONS if re.search(pattern, line)), None)
        if reason is None:
            failures.append(f"ExtendPowerStripes: {line.strip()}")
        else:
            excused.setdefault(reason, []).append(line.strip())
    for reason, lines in excused.items():
        notes.append(f"ExtendPowerStripes: {len(lines)} x {lines[0]} (exception: {reason})")
    for net in ("VPWR", "VGND"):
        if not re.search(rf"^\[INFO\] {net}: tidy: ", text, re.M):
            failures.append(f"ExtendPowerStripes did not finish {net} (no tidy line)")
    regions = {name.replace("\\", ""): tuple(map(int, counts))
               for name, *counts in STRIPE_REGIONS_RE.findall(text)}
    for inst in instances:
        if inst not in regions:
            failures.append(f"ExtendPowerStripes: no stripe allocation for {inst}")
        elif min(regions[inst]) < 1:
            failures.append(f"ExtendPowerStripes: {inst} has a region without a VPWR/VGND pair: "
                            f"array L {regions[inst][0]}, band {regions[inst][1]}, array R {regions[inst][2]}")
        else:
            notes.append(f"ExtendPowerStripes: {inst}: array L {regions[inst][0]}, band {regions[inst][1]}, "
                         f"array R {regions[inst][2]} VPWR/VGND pairs")
    return failures, notes


# ---- the final DEF and the macro LEF

def def_tokens(text):
    return text.replace("(", " ( ").replace(")", " ) ").replace(";", " ; ").split()


def parse_def(path):
    """Units, components {name: (macro, x, y, orient)} and special nets
    {net: {"conns": [(inst, pin)], "wires": [(layer, x0, y0, x1, y1)]}}."""
    text = Path(path).read_text(errors="replace")
    units = int(re.search(r"^UNITS DISTANCE MICRONS (\d+) ;", text, re.M)[1])
    comps = {}
    body = re.search(r"^COMPONENTS \d+ ;\n(.*?)^END COMPONENTS", text, re.M | re.S)
    for m in re.finditer(r"^\s*- (\S+) (\S+)[^;]*?\+ (?:FIXED|PLACED) \( (-?\d+) (-?\d+) \) (\w+)", body[1], re.M):
        comps[m[1]] = (m[2], int(m[3]), int(m[4]), m[5])
    nets = {}
    body = re.search(r"^SPECIALNETS \d+ ;\n(.*?)^END SPECIALNETS", text, re.M | re.S)
    for chunk in re.split(r"^\s*- ", body[1], flags=re.M)[1:]:
        statement = chunk.split(";")[0]
        name = statement.split()[0]
        head = statement.split("+", 1)[0]
        conns = re.findall(r"\( (\S+) (\S+) \)", head)
        wires = []
        for seg in re.split(r"\+ ROUTED|\bNEW\b", statement)[1:]:
            t = def_tokens(seg)
            if len(t) < 2 or not t[1].lstrip("-").isdigit():
                continue
            layer, width = t[0], int(t[1])
            points, last = [], None
            i = 0
            while i < len(t):
                if t[i] == "(" and i + 3 < len(t) and t[i + 3] == ")":
                    x = last[0] if t[i + 1] == "*" else int(t[i + 1])
                    y = last[1] if t[i + 2] == "*" else int(t[i + 2])
                    points.append((x, y))
                    last = (x, y)
                    i += 4
                else:
                    i += 1
            if len(points) == 2 and width > 0:
                (x0, y0), (x1, y1) = points
                h = width // 2
                if x0 == x1:
                    wires.append((layer, x0 - h, min(y0, y1), x0 + h, max(y0, y1)))
                elif y0 == y1:
                    wires.append((layer, min(x0, x1), y0 - h, max(x0, x1), y0 + h))
        nets[name] = dict(conns=[c for c in conns if c[0] != "PIN"], wires=wires)
    return units, comps, nets


def parse_lef_power_pins(path, macro):
    """(width, height, {pin: [(layer, x0, y0, x1, y1)]}) in microns for a macro's power pins."""
    text = Path(path).read_text(errors="replace")
    m = re.search(rf"^MACRO {re.escape(macro)}\s*$(.*?)^END {re.escape(macro)}\s*$", text, re.M | re.S)
    if m is None:
        raise ValueError(f"{path}: no MACRO {macro}")
    size = re.search(r"^\s*SIZE ([\d.]+) BY ([\d.]+) ;", m[1], re.M)
    pins = {}
    for p in re.finditer(r"^\s*PIN (\S+)\s*$(.*?)^\s*END \1\s*$", m[1], re.M | re.S):
        if not re.search(r"^\s*USE (POWER|GROUND) ;", p[2], re.M):
            continue
        rects, layer = [], None
        for line in p[2].splitlines():
            ls = re.match(r"^\s*LAYER (\S+) ;", line)
            if ls:
                layer = ls[1]
            r = re.match(r"^\s*RECT (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) ;", line)
            if r:
                rects.append((layer, *map(float, r.groups())))
        pins[p[1]] = rects
    return float(size[1]), float(size[2]), pins


def place(rect, origin, orient, size, units):
    """A macro-local rectangle (microns) in DEF units for a placed instance."""
    layer, x0, y0, x1, y1 = rect
    w, h = size
    ox, oy = origin
    if orient == "N":
        xs, ys = (x0, x1), (y0, y1)
    elif orient == "FS":
        xs, ys = (x0, x1), (h - y1, h - y0)
    elif orient == "S":
        xs, ys = (w - x1, w - x0), (h - y1, h - y0)
    elif orient == "FN":
        xs, ys = (w - x1, w - x0), (y0, y1)
    else:
        raise ValueError(f"unsupported macro orientation {orient}")
    return (layer, ox + round(xs[0] * units), oy + round(ys[0] * units),
            ox + round(xs[1] * units), oy + round(ys[1] * units))


def overlaps(a, b):
    return a[0] == b[0] and min(a[3], b[3]) > max(a[1], b[1]) and min(a[4], b[4]) > max(a[2], b[2])


def macro_power_failures(worktree):
    """(failures, notes): every macro power pin connected, from the final DEF."""
    wt = Path(worktree)
    run = wt / "runs/wokwi"
    failures, notes = [], []
    resolved = receipts.read_json(run / "resolved.json")
    macros = resolved.get("MACROS") or {}
    if not macros:
        return ["resolved.json names no macro"], notes
    rules = [r.split() for r in resolved.get("PDN_MACRO_CONNECTIONS") or []]
    units, comps, nets = parse_def(receipts.one((run / "final/def").glob("*.def"), "final DEF"))
    for macro, spec in sorted(macros.items()):
        lefs = spec.get("lef") or []
        if len(lefs) != 1:
            failures.append(f"{macro}: expected one LEF, found {len(lefs)}")
            continue
        w, h, pins = parse_lef_power_pins(lefs[0], macro)
        if not pins:
            failures.append(f"{macro}: its LEF has no power or ground pin")
            continue
        placed = {name: c for name, c in comps.items() if c[0] == macro}
        expected = set(spec.get("instances") or {})
        found = {name.replace("\\", "") for name in placed}
        if found != expected:
            failures.append(f"{macro}: DEF instances {sorted(found)} are not resolved.json's {sorted(expected)}")
        for inst, (_, x, y, orient) in sorted(placed.items()):
            for pin, rects in sorted(pins.items()):
                nets_for = [r[1] if pin == r[3] else r[2] for r in rules if len(r) == 5
                            and pin in (r[3], r[4]) and re.fullmatch(r[0], inst)]
                if len(set(nets_for)) != 1:
                    failures.append(f"{inst} {pin}: PDN_MACRO_CONNECTIONS gives {sorted(set(nets_for)) or 'no net'}")
                    continue
                net = nets_for[0]
                conns = nets.get(net, {}).get("conns", [])
                if (inst, pin) not in conns and ("*", pin) not in conns:
                    failures.append(f"{inst} {pin}: not connected to {net} in the DEF's special nets")
                    continue
                try:
                    shapes = [place(r, (x, y), orient, (w, h), units) for r in rects]
                except ValueError as e:
                    failures.append(f"{inst}: {e}")
                    break
                mid = x + round(w * units / 2)
                halves = {"left": [s for s in shapes if (s[1] + s[3]) / 2 < mid],
                          "right": [s for s in shapes if (s[1] + s[3]) / 2 >= mid]}
                counts = {}
                for half, group in halves.items():
                    if group:
                        counts[half] = sum(1 for s in nets[net]["wires"] if any(overlaps(s, g) for g in group))
                shorts = [other for other, n in nets.items() if other != net
                          and any(overlaps(s, g) for s in n["wires"] for g in shapes)]
                if shorts:
                    failures.append(f"{inst} {pin}: touched by stripes of {shorts}")
                missing = [half for half, n in counts.items() if n == 0]
                if missing:
                    failures.append(f"{inst} {pin} ({net}): no {net} stripe on the pin in the macro's "
                                    f"{' and '.join(missing)} half")
                elif not shorts:
                    notes.append(f"{inst} {pin} -> {net}: "
                                 + ", ".join(f"{n} stripes ({half} half)" for half, n in counts.items()))
    return failures, notes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("worktree")
    ap.add_argument("--checks-receipt", help="a checks receipt for this run (flow/check_rerun.py)")
    a = ap.parse_args(argv)
    wt = Path(a.worktree).resolve()
    bad = 0
    resolved = receipts.read_json(wt / "runs/wokwi/resolved.json")
    instances = sorted(pin.rsplit("/", 1)[0] for pin in sram_clock_pins(resolved))
    checks_dir = None
    if a.checks_receipt:
        data = receipts.verify(a.checks_receipt, wt, "checks")
        checks_dir = Path(a.checks_receipt).parent
        for line in data["skipped"]:
            print(f"checks receipt, SKIPPED: {line}")
    for title, run in (("clock and reset checks", lambda: timing_check_failures(wt, checks_dir)),
                       ("ExtendPowerStripes log", lambda: stripes_failures(wt, instances)),
                       ("macro power", lambda: macro_power_failures(wt))):
        try:
            failures, notes = run()
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            failures, notes = [f"invalid evidence: {e}"], []
        print(f"== {title}: {'FAIL' if failures else 'PASS'}")
        for line in notes:
            print(f"  {line}")
        for line in failures:
            print(f"  FAIL: {line}")
        bad += bool(failures)
    return int(bool(bad))


if __name__ == "__main__":
    sys.exit(main())
