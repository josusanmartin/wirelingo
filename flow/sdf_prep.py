#!/usr/bin/env python3
"""Prepare a LibreLane netlist + OpenSTA SDF pair for Icarus Verilog's
$sdf_annotate (flow/gl_sdf_test.sh). Writes private copies; never touches the
inputs.

    flow/sdf_prep.py <netlist.v> <in.sdf> <out_netlist.v> <out.sdf>

Icarus Verilog's SDF reader (14.0 devel) has these limitations; each is
worked around by renaming, dropping or (4, 6, 7) combining delay values,
never by inventing one:

1. Escaped identifiers. The flattened netlist keeps hierarchical names as
   escaped identifiers (`\\u_chip.u_imem.g_bank[0].g_on.u_bank.sram `), which
   the SDF writes as `u_chip\\.u_imem\\.g_bank\\[0\\]...`. Icarus's SDF reader
   splits at the escaped dots ("Cannot find clkbuf_0_u_chip in scope"), so the
   SRAMs, clock gates and the gated-clock buffers would silently keep zero
   delay, and an INTERCONNECT to such a pin aborts vvp (NULL vpi_scan). Every
   escaped identifier is renamed to a plain one (non-[A-Za-z0-9_] -> `_`,
   prefixed `esc_`) in both files, consistently.
2. Vector nets. The clock tree of the SCK clock (ui_in[6]) is written as
   `wire [6:0] clknet_0_ui_in;` with only bit 6 used; Icarus cannot insert a
   net delay into one bit of a vector ("Could not insert intermodpath", 200
   SCK-tree entries in g4 left at zero). Internal vector wires that are only
   ever bit-selected are split into scalar wires (`clknet_0_ui_in_6_`).
3. INTERCONNECT entries whose sink is a pin with nothing behind it in the cell
   model (antenna diodes: sg13cmos5l_antennanp has one input and no logic):
   Icarus cannot insert a net delay there ("Could not insert intermodpath")
   and aborts. These dead-end entries are dropped; the driver's delay, which
   includes the diode's load, is kept.
4. Bit-selected IOPATH ports. The SRAM's clock-to-output delay is written
   per output bit (`IOPATH A_CLK A_DOUT[0] ...`), which the reader rejects
   with a syntax error that ends the whole annotation (everything after the
   first SRAM, in g4 the SRAMs and 832 buffers, would keep zero or default
   delay). The SRAM model's specify block has one vector path
   (`posedge A_CLK *> A_DOUT`), so the 32 entries become one IOPATH to the
   whole bus with the per-bit maximum of each rise/fall triple (g4 typ: bits
   spread by <= 0.13 ns around 3.1 ns; the max is the pessimistic choice).
5. TIMINGCHECK blocks. Icarus does not implement timing checks (it warns
   "TIMINGCHECK not supported" for each) and the SRAM's conditional,
   bit-selected checks are another syntax error; they are dropped. Setup,
   hold, recovery/removal and pulse width stay STA's job (see
   docs/physical.md, Timing-annotated gate level).
6. Lookup cost. The reader finds every name by a linear scan of the
   (flat, ~60k cells and ~75k nets) top scope, and an INTERCONNECT costs two
   such lookups over cells and nets: g4's 62k net delays alone took over 15
   minutes to annotate. A net delay into a cell input that has IOPATHs is
   folded into those IOPATHs instead (wire + cell delay = the arrival at the
   cell's output either way); the larger of the wire's rise and fall triple
   is added to both of the IOPATH's rise and fall, because the output edge
   of an inverting or non-unate arc doesn't say which input edge caused it
   (pessimistic by at most the wire's rise/fall difference, which sdf_prep
   reports). Net delays into inputs without an IOPATH (flip-flop D, clock
   gate GATE, SRAM address/data/control, top-level outputs) stay
   INTERCONNECT entries, so late data still arrives late at a flop. g4:
   59942 of 62313 folded, 1997 kept, 374 diode entries dropped; annotation
   takes ~3.5 minutes.
7. COND entries. Icarus ignores `(COND expr (IOPATH ...))` entirely
   (checked on an xor2 with distinct COND and plain delays): each arc takes
   the unconditional IOPATH's delay, which OpenSTA writes for every arc as
   one of the variants, not the slowest (g4: up to 0.11 ns below the slowest
   variant, on mux select arcs). The unconditional entry gets the element-
   wise max of itself and its COND variants (pessimistic, as in 4) and the
   COND entries are dropped.

Any SDF syntax error stops Icarus's reader mid-file, so flow/gl_sdf_test.sh
fails if the simulator log has any "SDF ERROR" besides the header's empty
typ VOLTAGE/TEMPERATURE fields, or any "VPI error".
"""
import re
import sys

DEAD_CELLS = ("sg13cmos5l_antennanp",)


def main(nl_in, sdf_in, nl_out, sdf_out):
    nl = open(nl_in).read()
    plain = set(re.findall(r"(?<![\\\w$])[A-Za-z_][\w$]*", nl))
    rename = {}

    def sub_esc(m):
        name = m[1]
        if name not in rename:
            new = "esc_" + re.sub(r"[^A-Za-z0-9_]", "_", name)
            while new in plain:
                new += "_"
            plain.add(new)
            rename[name] = new
        return rename[name] + m[2]
    nl2 = re.sub(r"\\(\S+)(\s)", sub_esc, nl)

    nvec = 0
    for hi, lo, name in re.findall(r"^\s*wire\s+\[(\d+):(\d+)\]\s+(\w+)\s*;", nl2, re.M):
        word = r"(?<![\w$])" + re.escape(name) + r"(?![\w$])"
        if len(re.findall(word + r"(?!\s*\[)", nl2)) != 1:      # used whole somewhere: keep
            continue
        bits = range(min(int(hi), int(lo)), max(int(hi), int(lo)) + 1)
        decl = "".join(f" wire {name}_{b}_;\n" for b in bits)
        nl2 = re.sub(r"^\s*wire\s+\[\d+:\d+\]\s+" + re.escape(name) + r"\s*;\n", decl, nl2, flags=re.M)
        nl2 = re.sub(word + r"\s*\[(\d+)\]", lambda m: f"{name}_{m[1]}_", nl2)
        nvec += 1
    open(nl_out, "w").write(nl2)

    dead = {m[2] for m in re.finditer(r"^\s*(\w+)\s+(\w+)\s*\(", nl2, re.M) if m[1] in DEAD_CELLS}

    def fix_path(path):
        """SDF hierarchical path (flat: instance[.pin]) with escapes -> renamed."""
        parts = re.split(r"(?<!\\)\.", path)
        out = []
        for p in parts:
            raw = p.replace("\\", "")
            if "\\" in p and raw in rename:
                out.append(rename[raw])
            else:
                out.append(p)
        return ".".join(out)

    lines = open(sdf_in).read().split("\n")
    IC = re.compile(r"(\s*\(INTERCONNECT\s+)(\S+)(\s+)(\S+)\s+(\([^()]*\))(?:\s+(\([^()]*\)))?\)\s*$")
    IOPATH = re.compile(r"\(IOPATH (\S+) (\S+) (\([^()]*\)) (\([^()]*\))\)")
    INST = re.compile(r"(\s*\(INSTANCE\s+)([^)]+)(\).*)$")

    # pass 1: which (instance, pin) pairs start an IOPATH; the COND variants
    # of each arc
    arcs, conds, inst, cond, depth = set(), {}, None, False, 0
    for line in lines:
        if depth or line.strip() == "(TIMINGCHECK":
            depth += line.count("(") - line.count(")")
            continue
        if m := INST.match(line):
            inst = fix_path(m[2].strip())
        elif "(COND" in line:
            assert "(IOPATH" not in line, line
            cond = True
        elif "(IOPATH" in line:
            for m in IOPATH.finditer(line):
                arcs.add((inst, re.sub(r"\[\d+\]$", "", m[1])))
                if cond:
                    conds.setdefault((inst, m[1], m[2]), []).append((m[3], m[4]))
            cond = False

    # pass 2: rename, drop dead-end and fold net delays, drop TIMINGCHECKs,
    # merge per-bit IOPATHs
    fold = {}               # (instance, pin) -> (rise, fall) of the net delay into it
    out = []
    drop = kept = ntc = nbit = 0
    for line in lines:
        m = IC.match(line)
        if not m:
            assert "(INTERCONNECT" not in line, line
            continue
        src, dst = fix_path(m[2]), fix_path(m[4])
        dinst, _, dpin = dst.rpartition(".")
        if dinst in dead:
            drop += 1
        elif (dinst, dpin) in arcs:
            fold[(dinst, dpin)] = (m[5], m[6] or m[5])     # one triple: rise = fall
        else:
            kept += 1
    worst_rf = max((rf_diff(r, f) for r, f in fold.values()), default=0.0)

    depth_tc = 0            # > 0 while inside a TIMINGCHECK block
    bitpaths = []           # pending per-bit IOPATHs: (indent, src, dst base, rise, fall)
    inst = None

    def flush():
        nonlocal nbit
        if not bitpaths:
            return
        ind, src, base = bitpaths[0][:3]
        trip = [max_triple([b[k] for b in bitpaths]) for k in (3, 4)]
        out.append(f"{ind}{iopath(src, base, *trip)}")
        nbit += len(bitpaths)
        bitpaths.clear()

    raised = [0, 0.0]       # unconditional arcs raised to a COND variant, by up to

    def worst(t, variants):
        """t raised to the element-wise max of the non-empty variants."""
        vs = [v for v in variants if triple(v) is not None]
        if triple(t) is None or not vs:
            return t
        new = max_triple([t] + vs)
        if (d := rf_diff(new, t)) > 0:
            raised[0] += 1
            raised[1] = max(raised[1], d)
        return new

    def iopath(src, dst, rise, fall):
        variants = conds.get((inst, src, dst), ())
        rise = worst(rise, [r for r, _ in variants])
        fall = worst(fall, [f for _, f in variants])
        w = fold.get((inst, src))
        if w:
            wire = max_triple(w)
            rise, fall = add_triple(rise, wire), add_triple(fall, wire)
        return f"(IOPATH {src} {dst} {rise} {fall})"

    skip_cond = False
    for line in lines:
        if skip_cond:           # the IOPATH of a COND entry (Icarus ignores it)
            assert "(IOPATH" in line and line.rstrip().endswith(")))"), line
            skip_cond = False
            continue
        if "(COND" in line and not depth_tc:
            skip_cond = True
            continue
        if depth_tc:
            depth_tc += line.count("(") - line.count(")")
            continue
        if line.strip() == "(TIMINGCHECK":
            depth_tc, ntc = 1, ntc + 1
            continue
        m = re.match(r"(\s*)\(IOPATH (\S+) (\w+)\[\d+\] (\([^()]*\)) (\([^()]*\))\)\s*$", line)
        if m:
            if bitpaths and bitpaths[0][1:3] != (m[2], m[3]):
                flush()
            bitpaths.append((m[1], m[2], m[3], m[4], m[5]))
            continue
        flush()
        if m := IC.match(line):
            src, dst = fix_path(m[2]), fix_path(m[4])
            dinst, _, dpin = dst.rpartition(".")
            if dinst in dead or (dinst, dpin) in fold:
                continue
            out.append(f"{m[1]}{src}{m[3]}{dst} {m[5]}{' ' + m[6] if m[6] else ''})")
        elif m := INST.match(line):
            inst = fix_path(m[2].strip())
            out.append(m[1] + inst + m[3])
        elif "(IOPATH" in line:
            out.append(IOPATH.sub(lambda m: iopath(m[1], m[2], m[3], m[4]), line))
        else:
            out.append(line)
    open(sdf_out, "w").write("\n".join(out))
    print(f"sdf_prep: renamed {len(rename)} escaped identifiers, split {nvec} vector wires; net delays: "
          f"{len(fold)} folded into the sink's IOPATHs (rise/fall differ by <= {worst_rf:.3f} ns), "
          f"{kept} kept, {drop} to {len(dead)} antenna cells dropped; merged {nbit} per-bit IOPATHs; "
          f"dropped {ntc} TIMINGCHECK blocks; {sum(map(len, conds.values()))} COND IOPATHs dropped, "
          f"{raised[0]} unconditional rise/fall triples raised to their COND variants' max (by <= {raised[1]:.3f} ns)")


def triple(t):
    v = t.strip("()").split(":")
    return None if v == [""] else [float(x) for x in v]


def fmt(v):
    return "()" if v is None else "(" + ":".join(f"{x:.3f}" for x in v) + ")"


def add_triple(a, b):
    a, b = triple(a), triple(b)
    return fmt(None if a is None else [x + y for x, y in zip(a, b or [0, 0, 0])])


def rf_diff(r, f):
    r, f = triple(r), triple(f)
    return 0.0 if r is None or f is None else max(abs(x - y) for x, y in zip(r, f))


def max_triple(trips):
    """Element-wise max of SDF triples "(a:b:c)" (an empty "()" stays empty)."""
    vals = [t.strip("()").split(":") for t in trips]
    if any(v == [""] for v in vals):
        assert all(v == [""] for v in vals), trips
        return "()"
    return "(" + ":".join(f"{max(float(v[i]) for v in vals):.3f}" for i in range(3)) + ")"


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(*sys.argv[1:5])
