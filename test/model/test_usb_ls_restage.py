"""The lost-ACK re-stage racing the host's next SETUP, at LOW speed, on the
two-core device (usb_ls_rx on core 0, usb_ls_tx on core 1), with the device
software (pe.usbdev.DeviceSoftware) running over SPI concurrently with the bus.

The harness: ConcurrentModelHost runs test_usb_fs_data's SpiBridge inside
every model clock (ModelHost.step), so the pins-only low-speed host
(pe.usbhost.UsbHost), which advances time only through step(), keeps the bus
busy while the software runs, and the software can be held at any SPI access,
as at full speed (test_usb_fs_data.test_restage_held_at_every_access).

The two-core device differs from the one-core ones: on a SETUP, usb_ls_rx
clears F_STAGED, but the TX FIFO is core 1's and nobody flushes it; the
software empties it (DeviceSoftware.handle, and the dirty clear before the
next stage)."""
import pathlib

import pytest

from pe import asm, host, sim, usb, usbdev
from pe.modelhost import ModelHost
from pe.usbhost import UsbHost
from test_usb_fs_data import FSET_READ, SpiBridge

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
RX, TX = 0, 1
DESC_8 = bytes([0x80, 6, 0, 1, 0, 0, 8, 0])          # GET_DESCRIPTOR(device, 8): one DATA1
SET_CONFIG = bytes([0, 9, 1, 0, 0, 0, 0, 0])
DESC_18 = bytes([0x80, 6, 0, 1, 0, 0, 18, 0])
GAP = 8      # bit times between a handshake's EOP and the host's next token (UsbHost.data_in's retry gap)


class ConcurrentModelHost(ModelHost):
    """ModelHost whose every clock also ticks the SPI service (SpiBridge sets
    `service` on its host, and `idle`, unused here)."""
    service = None

    def step(self, n=1):
        for _ in range(n):
            kw = self.service.tick(self.chip) if self.service is not None else {}
            self.chip.step(self.ui, self.bus_uio(), **kw)


def two_core():
    """(ModelHost, DeviceSoftware): test_usb_enum's two-core device, address 0."""
    mh = ConcurrentModelHost()
    defs = usbdev.defines()
    rx = asm.assemble((FW / "usb_ls_rx.pasm").read_text(), defs)
    tx = asm.assemble((FW / "usb_ls_tx.pasm").read_text(), defs)
    mh.apply(host.pin_owner_writes({0: TX, 1: TX}))
    mh.apply(host.load_program(RX, rx, 0, clkdiv=1 << 8))
    mh.write(sim.core_reg(RX, sim.C_CRCCTRL), usbdev.CRCCTRL)
    mh.apply(host.load_program(TX, tx, out_pins=(0, 2), clkdiv=1 << 8))
    mh.pull_up = 0b10
    mh.start(0b11)
    mh.step(50)
    return mh, usbdev.DeviceSoftware(mh, rx, usbdev.DESCRIPTORS, RX, TX)


def bridges(overhead=48):
    """make(mh, sw) -> SpiBridge, every bridge closed when the generator ends.
    overhead: host-software clocks per SPI transaction (48 = 1 us; the board's
    Python loop takes tens of us)."""
    made = []

    def make(mh, sw):
        made.append(SpiBridge(mh, sw, overhead=overhead))
        return made[-1]
    yield make
    for b in made:
        b.close()


@pytest.fixture
def concurrent():
    yield from bridges()


def in_until_answer(h, addr, tries=2000):
    """IN tokens until a DATA packet (ACKed). Returns (packet, NAKs, unanswered)."""
    naks = silent = 0
    h._gap(GAP)
    for _ in range(tries):
        h._send(usb.token(usb.PID_IN, addr, 0))
        r = h._listen(120 * h.bit)
        if r and len(r) >= 3 and r[0] in (usb.pid_byte(usb.PID_DATA0), usb.pid_byte(usb.PID_DATA1)):
            assert usb.crc16(r[1:-2]) == r[-2:], "bad CRC from the device"
            h._gap(2)
            h._send(usb.handshake(usb.PID_ACK))
            return r, naks, silent
        if r == usb.handshake(usb.PID_NAK):
            naks += 1
        else:
            silent += 1
        h._gap(8)
    raise TimeoutError("IN never answered with data")


def restage_race_ls(concurrent, resume, hold_at):
    """GET_DESCRIPTOR(device, 8): the host takes its DATA1 but the ACK is lost,
    so the software re-stages it 100 us on; the host has moved on (OUT status,
    then SET_CONFIGURATION's SETUP). hold_at(op, prev, prev2, ctx) holds the
    software at one access; the SETUP goes on the bus while it is held, whole
    or token first. Returns (outcome, details): outcome "stale" (the status IN
    got the old DATA), "silent" (unanswered INs before the right reply) or
    "right" (one zero-length DATA1 after NAKs only)."""
    mh, sw = two_core()
    b = concurrent(mh, sw)
    h = UsbHost(mh, idle=lambda m: m.step(GAP * 32))   # the host's gap after each transaction
    h.setup(0, DESC_8)
    # armed now: at low speed the OUT status alone outlasts the 100 us re-stage delay
    ctx = {"go": False, "stale": (usb.PID_DATA1, usbdev.DEVICE_DESC[:8]), "sw": sw, "b": b, "mh": mh}
    b.hold = lambda op, prev, prev2: not ctx["go"] and hold_at(op, prev, prev2, ctx)
    old, naks, silent = None, 0, 0
    for _ in range(2000):                            # the DATA1, not ACKed
        h._send(usb.token(usb.PID_IN, 0, 0))
        old = h._listen(120 * h.bit)
        if old and old[0] == usb.pid_byte(usb.PID_DATA1):
            break
        h._gap(8)
    assert old == usb.data_packet(usb.PID_DATA1, usbdev.DEVICE_DESC[:8])
    h._gap(16)
    h.data_out(0, 0, usb.PID_DATA1, b"")            # OUT status: the host moved on
    for _ in range(5000):
        if b.held:
            break
        h._gap(8)
    assert b.held, "the software never got to the hold"
    if resume == "after the SETUP":
        h.setup(0, SET_CONFIG)
        ctx["go"] = True
    else:
        h._gap(GAP)
        h._send(usb.token(usb.PID_SETUP, 0, 0))
        ctx["go"] = True
        h._gap(2)
        h._send(usb.data_packet(usb.PID_DATA0, SET_CONFIG))
        assert h._listen(20 * h.bit) == usb.handshake(usb.PID_ACK)
    r, naks, silent = in_until_answer(h, 0)
    d = {"held": ctx.get("held_op"), "naks": naks, "silent": silent, "tx_fifo": len(mh.chip.cores[TX].txf),
         "staged": bool(mh.chip.flags & usbdev.F_STAGED)}
    if r[1:-2] == usbdev.DEVICE_DESC[:8]:
        return "stale", d
    if r != usb.data_packet(usb.PID_DATA1, b""):
        return f"wrong {r.hex()}", d
    # the device still works: a whole descriptor read
    h._gap(16)
    h.setup(0, DESC_18)
    got, d["silent_after"] = b"", 0
    for toggle in (1, 0, 1):
        pkt, _, s2 = in_until_answer(h, 0)
        assert pkt[0] == usb.pid_byte(usb.PID_DATA1 if toggle else usb.PID_DATA0), "DATA toggle after"
        got += pkt[1:-2]
        d["silent_after"] += s2
    h.data_out(0, 0, usb.PID_DATA1, b"")
    assert got == usbdev.DEVICE_DESC, "descriptor read after"
    return ("silent" if silent or d["silent_after"] else "right"), d


def at_decision(op, prev, prev2, ctx):
    """The flags read after pop_all with which the software decides to re-stage.
    The hold is asked when the read starts; the software reads the time when it
    returns, a one-word SPI read later (pe.usbfs.SpiService.read: 28 SCK-scaled
    clocks + 8 + overhead), so that is the elapsed time it will compare with
    100 us."""
    sw, b = ctx["sw"], ctx["b"]
    if not (op == FSET_READ and bool(prev) and prev[0] == "pop_all" and sw.unacked == ctx["stale"]
            and sw.sent_at is not None):
        return False
    svc = ctx["mh"].service
    read = (28 * svc.sck + 8 + svc.overhead) / 48
    return b.now_us() + read - sw.sent_at >= 100


def nth_access(n):
    """A hold at the n-th SPI access from the re-stage decision (n = 0: its flags read)."""
    seen = {"n": None, "last": None}

    def hold_at(op, prev, prev2, ctx):
        if seen["n"] is None:
            if not at_decision(op, prev, prev2, ctx):
                return False
            seen["n"], seen["last"] = 0, op
        elif op is not seen["last"]:
            seen["n"] += 1
            seen["last"] = op
        if seen["n"] == n:
            ctx["held_op"] = op
        return seen["n"] == n
    return hold_at


def table(n_max=26, resumes=("after the SETUP", "after its token")):
    """Every access from the re-stage decision, both resumes: (resume, n, outcome, details)."""
    rows = []
    for resume in resumes:
        for n in range(n_max):
            fixture = bridges()
            make = next(fixture)
            try:
                outcome, d = restage_race_ls(make, resume, nth_access(n))
            except (AssertionError, TimeoutError) as e:
                outcome, d = f"ERROR {type(e).__name__}: {e}", {}
            finally:
                for _ in fixture:
                    pass
            rows.append((resume, n, outcome, d))
            print(f"{resume:16s} n={n:2d}  {outcome:8s} {d}", flush=True)
    return rows


if __name__ == "__main__":        # the per-access table (runner1: PYTHONPATH=tools python <this file>)
    table()
