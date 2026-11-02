"""tools/pe/sim.py in lockstep with the OCaml model (ocaml/bin/wl_simcheck).

patch() wraps sim.Chip so that every chip, whoever creates it, feeds an
wl_simcheck process: each step's stimulus, the bus read and pins before the edge,
and the complete state after it. wl_simcheck runs the OCaml model on the same
stimulus and stops at the first difference. Nothing in sim.py changes, and the
wrapped chip behaves as before.

The bus read and the pins are compared every cycle, the whole state every
WL_LOCK_EVERY cycles (default 1) and at the end of a chip's life.

Some model tests reach into a chip (they drain core.rxf directly, or set a
register). The OCaml model cannot see that. With WL_LOCK_EVERY=1 the wrapper
compares the chip with the state it last sent before each step, and on a
difference stops comparing that chip and counts it as poked: the cycles up to
then still count. With a larger stride a poke shows as a mismatch, and the test
is rerun with stride 1 to tell the two apart. A deepcopy of a chip (a few USB
tests fork a run that way) is not compared; TOTALS counts those copies.

Totals go to $WL_LOCK_OUT.<pid>.json at exit, for the summary.

SPDX-License-Identifier: Apache-2.0
"""
import atexit
import json
import os
import pathlib
import subprocess
import tempfile
import weakref

from pe import sim

SIMCHECK = pathlib.Path(__file__).resolve().parents[1] / "_build/default/bin/wl_simcheck.exe"
EVERY = int(os.environ.get("WL_LOCK_EVERY", "1"))
TOTALS = {"chips": 0, "cycles": 0, "state_compares": 0, "poked_chips": 0, "mismatches": 0, "coverage_events": 0,
          "copies_not_compared": 0, "cycles_after_mismatch_ignored": 0}
FAILS = []
LIVE = []


def state_line(chip):
    out = [chip.enable, -1 if chip.ctrl_pend is None else chip.ctrl_pend, chip.flags, *chip.pin_owner, chip.od_mask,
           chip.pin_out, chip.pin_oe, chip.hostcfg, chip.sync1, chip.sync2, chip.sync_bypass, chip.rx_hq, chip.rx_h2,
           chip.pads_prev, chip.cycle]
    for c in chip.cores:
        out += (c.div_int, c.div_frac, c.out_base, c.out_count, c.set_base, c.set_count, c.side_base, c.side_count,
                c.side_opt, c.side_pindir, c.in_base, c.out_shr, c.in_shr, c.autopull, c.autopush, c.pull_thresh,
                c.push_thresh, c.fjoin, c.entry, c.wrap_bottom, c.wrap_top, c.crc_poly, c.crc_w, c.in_tap, c.out_tap,
                c.crc, *c.r, c.lr, c.pc, c.isr, c.isr_cnt, c.osr, c.osr_cnt, c.z, c.c, c.t, c.dcnt, c.in_wait, c.wprev,
                c.wevent, c.cnt, c.facc, c.halted, c.fault, c.stalled)
        out.append(len(c.txf))
        out += c.txf
        out.append(len(c.rxf))
        out += c.rxf
        out.append(len(c.res))
        out += [int(r) for r in c.res]
    l = chip.line
    out += (l.cfg, l.div, l.pins, l.tx_on, l.ending, l.eop_idle, l.half, l.bit, l.level, l.txn, l.tcnt, l.tfacc,
            l.trun, l.trunbit, l.trunv, l.rx_on, l.rcnt, l.rlast, l.rprev, l.quiet, l.q, l.n, l.rrun, l.rrunbit,
            l.rrunv, l.end_pend, l.ovf)
    out.append(len(l.entries))
    for e in l.entries:
        out.append(len(e))
        out += e
    out.append(len(l.rq))
    out += l.rq
    return " ".join(map(str, out)) + " "


class Link:
    def __init__(self, chip):
        for link in list(LIVE):              # chips already garbage-collected
            if link.chip() is None:
                link.finish(None, stats=False)
        self.log = tempfile.TemporaryFile("w+")
        self.p = subprocess.Popen([str(SIMCHECK)], stdin=subprocess.PIPE, stdout=self.log, text=True, bufsize=1 << 20)
        self.send = self.p.stdin.write
        self.send(f"C {chip.bank_words} {chip.ncores} {chip.nbanks} {chip.fifo_depth}\n")
        self.last = state_line(chip)
        self.chip = weakref.ref(chip)
        self.cycles = 0
        self.poked = False
        self.dead = False
        TOTALS["chips"] += 1
        LIVE.append(self)

    def __deepcopy__(self, memo):
        TOTALS["copies_not_compared"] += 1
        c = Link.__new__(Link)
        c.dead, c.poked, c.cycles = True, False, 0
        return c

    def finish(self, chip, stats=True):
        if self.dead:
            return
        self.dead = True
        LIVE.remove(self)
        if EVERY == 1 and stats and chip is not None and not self.poked and state_line(chip) != self.last:
            self.poked = True                # a write after the last step
            TOTALS["poked_chips"] += 1
        try:
            if stats and not self.poked and chip is not None:
                self.send(f"= {state_line(chip)}\n")
                TOTALS["state_compares"] += 1
                self.send("F " + " ".join(f"{k}={v}" for k, v in sorted(chip.stats.items())) + "\n")
                TOTALS["coverage_events"] += sum(chip.stats.values())
            self.p.stdin.close()
        except BrokenPipeError:
            pass
        self.p.wait()
        self.log.seek(0)
        text = self.log.read()
        if not text.startswith("OK ") or self.p.returncode:
            # the stream outran wl_simcheck's exit by however much the pipe held,
            # so these cycles depend on timing and are not counted as compared
            TOTALS["cycles_after_mismatch_ignored"] += self.cycles
            TOTALS["mismatches"] += 1
            FAILS.append(text[:4000])
        else:
            TOTALS["cycles"] += self.cycles


_init = sim.Chip.__init__
_step = sim.Chip.step


class Stats(dict):
    """chip.stats; a test may clear it, and the OCaml counters must follow."""

    def __init__(self, link):
        super().__init__()
        self.link = link

    def clear(self):
        super().clear()
        if not self.link.dead:
            self.link.send("X\n")


def init(self, *a, **k):
    _init(self, *a, **k)
    self._link = Link(self)
    self.stats = Stats(self._link)


def step(self, ui_in=0, uio_in=0, we=False, addr=0, wdata=0, commit=False, snap=False, cancel=False,
         ui_mid=None, uio_mid=None):
    link = self._link
    if link.dead or link.poked:
        return _step(self, ui_in, uio_in, we, addr, wdata, commit, snap, cancel, ui_mid, uio_mid)
    if EVERY == 1 and state_line(self) != link.last:
        link.poked = True
        TOTALS["poked_chips"] += 1
        link.finish(self, stats=False)
        return _step(self, ui_in, uio_in, we, addr, wdata, commit, snap, cancel, ui_mid, uio_mid)
    uo, uio_out, uio_oe = self.outputs()
    rd = self.read(addr)
    _step(self, ui_in, uio_in, we, addr, wdata, commit, snap, cancel, ui_mid, uio_mid)
    link.cycles += 1
    try:
        link.send(f"S {ui_in} {uio_in} {int(we)} {addr} {wdata} {int(commit)} {int(snap)} {int(cancel)} "
                  f"{-1 if ui_mid is None else ui_mid} {-1 if uio_mid is None else uio_mid} {rd} {uo} {uio_out} {uio_oe}\n")
        if link.cycles % EVERY == 0:
            link.last = state_line(self)
            link.send(f"= {link.last}\n")
            TOTALS["state_compares"] += 1
    except BrokenPipeError:
        link.finish(self, stats=False)


def patch():
    sim.Chip.__init__ = init
    sim.Chip.step = step


def finish_all():
    """Close every open chip, comparing coverage where the chip is still alive."""
    for link in list(LIVE):
        chip = link.chip()
        link.finish(chip, stats=chip is not None)
    return FAILS


@atexit.register
def _write_totals():
    finish_all()
    out = os.environ.get("WL_LOCK_OUT")
    if out:
        with open(f"{out}.{os.getpid()}.json", "w") as f:
            json.dump({"totals": TOTALS, "fails": FAILS[:5]}, f)
