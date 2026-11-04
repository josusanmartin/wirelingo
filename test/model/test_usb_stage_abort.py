"""A host that abandons a transfer with a new SETUP while the device software
is staging that transfer's reply: a normal stage (not the lost-ACK re-stage),
at both speeds, with the software running over SPI concurrently with the bus
and held at every access from the stage's first write (verifier 044, note 3).

Full speed: usb_fs_dev (one core), whose got_setup flushes the TX FIFO, so a
SETUP between two pushes leaves the later pushes behind it: a partial packet
that the got_in guard (F_STAGED over an *empty* FIFO) does not catch. Low
speed: usb_ls_rx + usb_ls_tx (two cores), where the SETUP clears F_STAGED but
nothing flushes core 1's TX FIFO. The new request is SET_CONFIGURATION; its
status stage must be one zero-length DATA1."""
import pytest

from pe import usb, usbdev
from pe.usbfs import FsHost
from pe.usbhost import UsbHost
import test_usb_fs_data as fs
import test_usb_ls_restage as ls

DESC_18 = bytes([0x80, 6, 0, 1, 0, 0, 18, 0])
SET_CONFIG = bytes([0, 9, 1, 0, 0, 0, 0, 0])
FCLR_GOTACK = ("write", fs.sim.A_FCLR, usbdev.F_GOTACK)


def fs_bridges(overhead=48):
    made = []

    def make(h, sw):
        made.append(fs.SpiBridge(h, sw, overhead=overhead))
        return made[-1]
    yield make
    for b in made:
        b.close()


def from_reply_stage(n):
    """Hold at the n-th access from the first write of the stage of the reply
    to GET_DESCRIPTOR(device, 18) (n = 0: that FCLR)."""
    seen = {"n": None, "last": None}

    def hold_at(op, prev, prev2, ctx):
        sw = ctx["sw"]
        if seen["n"] is None:
            if not (op == FCLR_GOTACK and sw.requests and sw.requests[-1] == DESC_18.hex()):
                return False
            seen["n"], seen["last"] = 0, op
        elif op is not seen["last"]:
            seen["n"] += 1
            seen["last"] = op
        if seen["n"] == n:
            ctx["held_op"] = op
        return seen["n"] == n
    return hold_at


def classify(r, naks, silent, extra):
    """The status stage's answer: right (a zero-length DATA1), stale (the
    abandoned reply's bytes) or garbage (anything else)."""
    d = {"naks": naks, "silent": silent, **extra}
    if r == usb.data_packet(usb.PID_DATA1, b""):
        return ("silent" if silent else "right"), d
    if r[1:-2] == usbdev.DEVICE_DESC[:8]:
        return "stale", d
    return f"garbage {r.hex()}", d


def abort_fs(make, resume, hold_at):
    h, sw = fs.enumerated()
    b = make(h, sw)
    ctx = {"go": False, "sw": sw, "b": b}
    b.hold = lambda op, prev, prev2: not ctx["go"] and hold_at(op, prev, prev2, ctx)
    h.setup(5, DESC_18)
    for _ in range(5000):
        if b.held:
            break
        h.wait_bits(8)
    assert b.held, "the software never got to the hold"
    if resume == "after the SETUP":
        h.setup(5, SET_CONFIG)
        ctx["go"] = True
    else:
        t = h.send(usb.token(usb.PID_SETUP, 5, 0))
        ctx["go"] = True
        t = h.send(usb.data_packet(usb.PID_DATA0, SET_CONFIG), t, h.ipd)
        assert h.listen(t) == usb.handshake(usb.PID_ACK)
    n = len(h.log)
    try:
        pid, payload = h.data_in(5, 0)
        r = bytes([pid]) + payload + usb.crc16(payload)       # the packet as received
    except (TimeoutError, AssertionError) as e:
        return f"no answer ({type(e).__name__})", {}
    silent = sum(1 for e in h.log[n:] if e == ("IN", None, None))
    naks = sum(1 for e in h.log[n:] if e[:2] == ("IN", "nak"))
    outcome, d = classify(r, naks, silent, {"held": ctx.get("held_op"), "underrun": bool(b.chip.flags & 1)})
    if outcome in ("right", "silent"):
        fs.staged_over_empty(h, b, "after SET_CONFIGURATION")
        b.service.overhead = 48                  # the race is over: the check after at 1 us per access
        h.setup(5, DESC_18)
        got, toggle = b"", 1
        while len(got) < 18:
            pid, payload = h.data_in(5, 0)
            assert pid == usb.pid_byte(usb.PID_DATA1 if toggle else usb.PID_DATA0), "DATA toggle after"
            toggle ^= 1
            got += payload
        h.data_out(5, 0, usb.PID_DATA1, b"")
        assert got == usbdev.DEVICE_DESC, "descriptor read after"
    return outcome, d


def abort_ls(make, resume, hold_at):
    mh, sw = ls.two_core()
    b = make(mh, sw)
    h = UsbHost(mh, idle=lambda m: m.step(ls.GAP * 32))
    ctx = {"go": False, "sw": sw, "b": b, "mh": mh}
    b.hold = lambda op, prev, prev2: not ctx["go"] and hold_at(op, prev, prev2, ctx)
    h.setup(0, DESC_18)
    for _ in range(5000):
        if b.held:
            break
        h._gap(8)
    assert b.held, "the software never got to the hold"
    if resume == "after the SETUP":
        h.setup(0, SET_CONFIG)
        ctx["go"] = True
    else:
        h._gap(ls.GAP)
        h._send(usb.token(usb.PID_SETUP, 0, 0))
        ctx["go"] = True
        h._gap(2)
        h._send(usb.data_packet(usb.PID_DATA0, SET_CONFIG))
        assert h._listen(20 * h.bit) == usb.handshake(usb.PID_ACK)
    try:
        r, naks, silent = ls.in_until_answer(h, 0)
    except TimeoutError:
        return "no answer (TimeoutError)", {}
    outcome, d = classify(r, naks, silent, {"held": ctx.get("held_op"), "tx_fifo": len(mh.chip.cores[ls.TX].txf)})
    if outcome in ("right", "silent"):
        b.service.overhead = 48                  # the race is over: the check after at 1 us per access
        h._gap(16)
        h.setup(0, DESC_18)
        got = b""
        for toggle in (1, 0, 1):
            pkt, _, _ = ls.in_until_answer(h, 0)
            assert pkt[0] == usb.pid_byte(usb.PID_DATA1 if toggle else usb.PID_DATA0), "DATA toggle after"
            got += pkt[1:-2]
        h.data_out(0, 0, usb.PID_DATA1, b"")
        assert got == usbdev.DEVICE_DESC, "descriptor read after"
    return outcome, d


SPEEDS = {"full": (abort_fs, fs_bridges), "low": (abort_ls, ls.bridges)}


def run(speed, resume, n, overhead=48):
    """One case: (outcome, details). overhead: SPI host-software clocks per transaction."""
    scenario, bridges = SPEEDS[speed]
    gen = bridges(overhead)
    make = next(gen)
    try:
        return scenario(make, resume, from_reply_stage(n))
    except (AssertionError, TimeoutError) as e:
        return f"ERROR {type(e).__name__}: {e}", {}
    finally:
        for _ in gen:
            pass


@pytest.mark.parametrize("overhead", (48, 2400))      # clocks per SPI transaction: 1 us, and the board's tens of us
@pytest.mark.parametrize("resume", ["after the SETUP", "after its token"])
@pytest.mark.parametrize("n", range(16))
@pytest.mark.parametrize("speed", ["full", "low"])
def test_stage_abort_held_at_every_access(speed, n, resume, overhead):
    """The reply's stage held at its n-th access (n = 0: its FCLR; then the four
    pushes, the check, the FSET, the check after and the service loops) while
    the host abandons the transfer with SET_CONFIGURATION. Every case must end
    right: one zero-length DATA1 after NAKs only, then a whole descriptor read.
    Before pe.usbdev checked every stage and usb_ls_tx dropped its FIFO on a
    SETUP (dc51cc8): full speed sent the old reply (n = 0-1) or a partial
    packet (n = 2-4, F_UNDERRUN); low speed, at 2400 clocks, the old reply
    (n = 0-5)."""
    outcome, d = run(speed, resume, n, overhead)
    assert outcome == "right", f"{speed} speed, access {n}, {resume}, {overhead} clocks: {outcome} {d}"


if __name__ == "__main__":        # one case: <speed> <resume index> <n> [overhead]
    import sys
    speed, ri, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    overhead = int(sys.argv[4]) if len(sys.argv) > 4 else 48
    resume = ("after the SETUP", "after its token")[ri]
    outcome, d = run(speed, resume, n, overhead)
    print(f"{speed:4s} overhead {overhead:5d} {resume:16s} n={n:2d}  {outcome:10s} {d}", flush=True)
