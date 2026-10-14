"""Independent protocol oracle: sigrok's protocol decoders on recorded pins.

Our tests judge the pins with our own Python device models (test/test.py,
tools/pe/usb.py, ...). This module hands the same waveforms to sigrok-cli's
protocol decoders (libsigrokdecode: uart, spi, i2c, usb_signalling +
usb_packet, can, ...), third-party code that shares no assumptions with those
models, and returns what they decoded.

    tr = Trace()                       # named 1-bit channels, changes in ps
    tr.set(t_ps, "uo0", 1)             # or tr.set_bus(t_ps, "uio", 0x5A)
    for t, byte in uart(tr, "uo0", 115200): ...

Finding sigrok-cli: $SIGROK_CLI, then `sigrok-cli` on PATH, then the
user-space copy flow/install_sigrok.sh makes (~/tools/sigrok/sigrok-cli).
`cli()` is None when none exists; tests skip with INSTALL_HINT.
"""
import os
import re
import shutil
import subprocess
import tempfile
from collections import namedtuple

INSTALL_HINT = ("sigrok-cli not found: `sudo apt install sigrok-cli` (Debian/Ubuntu), "
                "or flow/install_sigrok.sh for a user-space copy in ~/tools/sigrok, "
                "or set SIGROK_CLI=/path/to/sigrok-cli")

Ann = namedtuple("Ann", "start end pd text")   # start/end in ns from the trace's t0


def cli():
    for c in (os.environ.get("SIGROK_CLI"), shutil.which("sigrok-cli"),
              os.path.expanduser("~/tools/sigrok/sigrok-cli")):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


class Trace:
    """Value changes of named 1-bit channels, times in ps. Unknown (x/z) bits
    are recorded as 0, as a logic analyzer's threshold would show them."""

    def __init__(self):
        self.names = []
        self.events = []     # (t_ps, name, bit) in call order (times nondecreasing)
        self.cur = {}
        self.t0 = None

    def set(self, t_ps, name, bit):
        bit = 1 if bit == 1 else 0
        if name not in self.cur:
            self.names.append(name)
        elif self.cur[name] == bit:
            return
        if self.t0 is None:
            self.t0 = t_ps
        self.cur[name] = bit
        self.events.append((t_ps, name, bit))

    def set_bus(self, t_ps, prefix, value, width=8, xmask=0):
        for i in range(width):
            self.set(t_ps, f"{prefix}{i}", 0 if (xmask >> i) & 1 else (value >> i) & 1)

    def end(self, t_ps):
        """Mark the end of the capture (the VCD holds the last levels until here)."""
        self.t_end = t_ps

    def write_vcd(self, path, res_ps=1000, channels=None, time_scale=1.0):
        """Write a VCD with one sample per res_ps (sigrok's sample rate is
        1/res_ps). Times are rounded; changes that collapse into one slot keep
        the last value (a glitch shorter than res_ps disappears). time_scale
        stretches the time axis (e.g. 50/48 shows a 50 MHz simulation as if
        clocked at 48 MHz)."""
        res_ps = res_ps / time_scale
        names = [n for n in (channels or self.names) if n in self.cur]
        ids = {n: _vcd_id(i) for i, n in enumerate(names)}
        unit = {1: "1ps", 10: "10ps", 100: "100ps", 1000: "1ns", 10000: "10ns",
                100000: "100ns"}[round(res_ps * time_scale)]
        t0 = self.t0 or 0
        slots = {}
        first = {}
        for t, n, b in self.events:
            if n not in ids:
                continue
            k = max(0, round((t - t0) / res_ps))
            slots.setdefault(k, {})[n] = b
            first.setdefault(n, b)
        with open(path, "w") as f:
            f.write(f"$timescale {unit} $end\n$scope module pins $end\n")
            for n in names:
                f.write(f"$var wire 1 {ids[n]} {n} $end\n")
            f.write("$upscope $end\n$enddefinitions $end\n")
            state = {}
            for k in sorted(slots):
                ch = {n: b for n, b in slots[k].items() if state.get(n) != b}
                if k == 0:   # every channel needs an initial value
                    ch = {n: slots[0].get(n, first[n]) for n in names}
                if not ch:
                    continue
                f.write(f"#{k}\n" + "".join(f"{b}{ids[n]}\n" for n, b in ch.items()))
                state.update(ch)
            last = max(slots) if slots else 0
            tend = getattr(self, "t_end", None)
            kend = max(last + 1, round((tend - t0) / res_ps) if tend is not None else last + 1)
            f.write(f"#{kend}\n")
        return t0


def _vcd_id(i):
    s = ""
    while True:
        s += chr(33 + i % 94)
        i //= 94
        if not i:
            return s


_LINE = re.compile(r"^(\d+)-(\d+) (\S+): (.*)$")


def decode(trace, decoders, annotations, res_ps=1000, channels=None, keep=None, time_scale=1.0,
           abort_at_exit_ok=False):
    """Run sigrok-cli on the trace. decoders: the -P stack, e.g.
    "uart:rx=uo0:baudrate=115200" or "usb_signalling:dp=uio1:dm=uio0,usb_packet";
    annotations: the -A filter, e.g. "uart=rx-data:rx-warnings" (decoders
    separated by commas: "usb_signalling=error,usb_packet=packet"). Returns
    [Ann(start_ns, end_ns, pd, text)] sorted by start. keep: a directory to
    leave the VCD in (for debugging); otherwise a temporary file is used.
    time_scale: see Trace.write_vcd (returned times stay in the trace's own
    time base). abort_at_exit_ok: accept sigrok-cli aborting with "free():
    invalid pointer" after it printed its annotations (sigrok-cli 0.7.2 /
    libsigrokdecode 0.5.3 do that at teardown after the graycode decoder)."""
    exe = cli()
    if exe is None:
        raise RuntimeError(INSTALL_HINT)
    with tempfile.TemporaryDirectory() as tmp:
        vcd = os.path.join(keep or tmp, "pins.vcd")
        if keep:
            os.makedirs(keep, exist_ok=True)
        trace.write_vcd(vcd, res_ps, channels, time_scale)
        cmd = [exe, "-i", vcd, "-I", "vcd", "-P", decoders, "-A", annotations,
               "--protocol-decoder-samplenum"]
        # sigrok-cli embeds its own Python for the decoders: a simulator's
        # PYTHONHOME/PYTHONPATH (cocotb under oss-cad-suite) would break it
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
        torn_down = (abort_at_exit_ok and r.returncode in (-6, 134) and r.stdout
                     and r.stderr.strip() == "free(): invalid pointer")
        if r.returncode and not torn_down:
            raise RuntimeError(f"{' '.join(cmd)} failed:\n{r.stderr}")
        if keep:
            with open(os.path.join(keep, "sigrok.txt"), "w") as f:
                f.write(" ".join(cmd) + "\n" + r.stdout + r.stderr)
    out = []
    for line in r.stdout.splitlines():
        m = _LINE.match(line)
        if m:
            s, e = int(m[1]), int(m[2])
            k = res_ps / 1000 / time_scale
            out.append(Ann(s * k, e * k, m[3].rsplit("-", 1)[0], m[4]))
    out.sort(key=lambda a: (a.start, a.end))
    return out


# ---------------------------------------------------------------- per protocol

def uart(trace, pin, baud, **kw):
    """[(t_ns, byte)] and [(t_ns, warning text)] from sigrok's uart decoder
    (8N1, LSB first, idle high)."""
    anns = decode(trace, f"uart:rx={pin}:baudrate={baud}", "uart=rx-data:rx-warnings", **kw)
    data, warn = [], []
    for a in anns:
        if re.fullmatch(r"[0-9A-F]{2}", a.text):
            data.append((a.start, int(a.text, 16)))
        else:
            warn.append((a.start, a.text))
    return data, warn


def spi(trace, clk, mosi=None, miso=None, cs=None, cpol=0, cpha=0, bitorder="msb-first", **kw):
    """(mosi bytes, miso bytes, warnings) from sigrok's spi decoder. Each byte
    is (t_ns, value)."""
    pins = [f"clk={clk}"] + [f"{k}={v}" for k, v in (("mosi", mosi), ("miso", miso), ("cs", cs)) if v]
    opts = pins + [f"cpol={cpol}", f"cpha={cpha}", f"bitorder={bitorder}"]
    anns = decode(trace, "spi:" + ":".join(opts), "spi=mosi-data:miso-data:warnings", **kw)
    # sigrok prints MISO and MOSI data identically; run once per line to tell them apart
    res = {}
    for name in ("mosi", "miso"):
        if not locals()[name]:
            res[name] = []
            continue
        a2 = decode(trace, "spi:" + ":".join(opts), f"spi={name}-data", **kw)
        res[name] = [(a.start, int(a.text, 16)) for a in a2]
    warn = [(a.start, a.text) for a in anns if not re.fullmatch(r"[0-9A-F]{2}", a.text)]
    return res["mosi"], res["miso"], warn


def i2c(trace, scl, sda, **kw):
    """sigrok's i2c decoder as tokens: "S", "Sr", "P", "ACK", "NACK",
    ("AW", addr7), ("AR", addr7), ("DW", byte), ("DR", byte), ("WARN", text),
    each as (t_ns, token)."""
    anns = decode(trace, f"i2c:scl={scl}:sda={sda}",
                  "i2c=start:repeat-start:stop:ack:nack:address-read:address-write:"
                  "data-read:data-write:warnings", **kw)
    out = []
    for a in anns:
        t = a.text
        if t in ("Read", "Write"):          # the R/W bit, already in the address token
            continue
        tok = {"Start": "S", "Start repeat": "Sr", "Stop": "P", "ACK": "ACK", "NACK": "NACK"}.get(t)
        if tok is None:
            m = re.fullmatch(r"(Address|Data) (read|write): ([0-9A-F]{2})", t)
            if m:
                tok = (m[1][0] + m[2][0].upper(), int(m[3], 16))
            else:
                tok = ("WARN", t)
        out.append((a.start, tok))
    return out


Packet = namedtuple("Packet", "start end pid addr ep data crc_ok text")


def usb(trace, dp, dm, speed="low-speed", **kw):
    """(packets, errors) from sigrok's usb_signalling + usb_packet stack.
    Packet.pid is sigrok's name ("SETUP", "DATA0", "ACK", ...); addr/ep for
    tokens, data (bytes) for DATAx; crc_ok is False if sigrok reported a CRC5
    or CRC16 error. errors: [(t_ns, text)] for SYNC/CRC/signalling errors."""
    anns = decode(trace, f"usb_signalling:dp={dp}:dm={dm}:signalling={speed},usb_packet",
                  "usb_signalling=error,usb_packet=packet:crc5-err:crc16-err:sync-err", **kw)
    pk, err = [], []
    for a in anns:
        if a.pd == "usb_signalling" or "ERR" in a.text or a.text.startswith("Invalid"):
            err.append((a.start, a.text))
            continue
        f = a.text.split()
        pid, addr, ep, data = f[0], None, None, None
        if "ADDR" in f:
            addr, ep = int(f[f.index("ADDR") + 1]), int(f[f.index("EP") + 1])
        if "[" in f:
            data = bytes(int(x, 16) for x in f[f.index("[") + 1:f.index("]")])
        pk.append(Packet(a.start, a.end, pid, addr, ep, data, True, a.text))
    # attribute CRC errors to the packet that contains them
    for t, text in err:
        if "CRC" in text:
            for i, p in enumerate(pk):
                if p.start <= t <= p.end:
                    pk[i] = p._replace(crc_ok=False)
    return pk, err


USB_PIDS = {0xE1: "OUT", 0x69: "IN", 0x2D: "SETUP", 0xA5: "SOF", 0xC3: "DATA0", 0x4B: "DATA1",
            0xD2: "ACK", 0x5A: "NAK", 0x1E: "STALL"}


def usb_summary(pk):
    """sigrok.usb packets as (pid, addr, ep, data hex) tuples."""
    return [(p.pid, p.addr, p.ep, p.data.hex() if p.data is not None else None) for p in pk]


def usb_expect(packets):
    """Packet bytes (PID first, no SYNC; tools/pe/usb.py) in usb_summary's
    terms, to compare a test's own record of the bus with sigrok's decode."""
    out = []
    for p in packets:
        pid = USB_PIDS.get(p[0], "?")
        if pid in ("OUT", "IN", "SETUP"):
            v = p[1] | p[2] << 8
            out.append((pid, v & 0x7F, (v >> 7) & 15, None))
        elif pid.startswith("DATA"):
            out.append((pid, None, None, p[1:-2].hex()))
        else:
            out.append((pid, None, None, None))
    return out


def can(trace, pin, bitrate, sample_point=70.0, **kw):
    """sigrok's can decoder (classic frames). Returns (frames, warnings);
    each frame is a dict: t, id, rtr, dlc, data (bytes), crc (the CRC-15
    field as sigrok read it off the wire), crc_ok, ack, eof, warnings;
    warnings: [(t_ns, text)] for all frames.

    crc_ok is False only if sigrok reports "CRC is invalid", else None: the
    decoder in libsigrokdecode 0.5.3 does not check the CRC (is_valid_crc()
    is a TODO that returns True), so check `crc` yourself."""
    anns = decode(trace, f"can:can_rx={pin}:nominal_bitrate={bitrate}:sample_point={sample_point}",
                  "can=sof:id:rtr:dlc:data:crc-sequence:ack-slot:eof:warnings", **kw)
    frames, warn, cur = [], [], None
    for a in anns:
        t = a.text
        if t == "Start of frame":
            cur = {"t": a.start, "id": None, "rtr": None, "dlc": None, "data": b"",
                   "crc": None, "crc_ok": None, "ack": None, "eof": False, "warnings": []}
            frames.append(cur)
            continue
        if cur is None:
            warn.append((a.start, t))
            continue
        if m := re.match(r"Identifier: (\d+)", t):
            cur["id"] = int(m[1])
        elif m := re.match(r"Remote transmission request: (\w+) frame", t):
            cur["rtr"] = m[1] == "remote"
        elif m := re.match(r"Data length code: (\d+)", t):
            cur["dlc"] = int(m[1])
        elif m := re.match(r"Data byte \d+: 0x([0-9a-fA-F]{2})", t):
            cur["data"] += bytes([int(m[1], 16)])
        elif m := re.match(r"CRC-15 sequence: 0x([0-9a-fA-F]+)", t):
            cur["crc"] = int(m[1], 16)
        elif t == "CRC is invalid":
            cur["crc_ok"] = False
            cur["warnings"].append(t)
        elif m := re.match(r"ACK slot: (\w+)", t):
            cur["ack"] = m[1] == "ACK"
        elif t == "End of frame":
            cur["eof"] = True
        else:
            cur["warnings"].append(t)
            warn.append((a.start, t))
    return frames, warn


# ---------------------------------------------------------------- the model's pins

class ModelRecorder:
    """Record the cycle model's pins (tools/pe/sim.py) into a Trace, for every
    Chip.step while active, with no change to the tests that drive it:

        with ModelRecorder(period_ns=1000 / 48) as rec:
            test_can.test_can_loopback_tx_to_rx()
        frames, _ = can(rec.trace, "ui0", 500_000)

    Channels: ui0..7 and uio0..7 as the chip sampled them (uio = the resolved
    bus: chip, external drivers, pull-ups): at the rising edge k * period, and
    at the falling edge before it when the caller passes ui_mid/uio_mid; uo0..6
    as the chip drives them, a quarter period after the edge."""

    def __init__(self, period_ns=20.0):
        self.period_ps = round(period_ns * 1000)
        self.trace = Trace()
        self.k = 0

    def restart(self):
        """Drop what was recorded so far (e.g. the setup, with idle levels not
        yet applied); the capture starts at the next step."""
        self.trace = Trace()

    def __enter__(self):
        from . import sim
        self._sim, self._step = sim, sim.Chip.step
        rec = self

        def step(chip, ui_in=0, uio_in=0, *a, **kw):
            p, t = rec.period_ps, rec.k * rec.period_ps
            tr = rec.trace
            if kw.get("ui_mid") is not None or kw.get("uio_mid") is not None:
                tr.set_bus(t - p // 2, "ui", ui_in if kw.get("ui_mid") is None else kw["ui_mid"])
                tr.set_bus(t - p // 2, "uio", uio_in if kw.get("uio_mid") is None else kw["uio_mid"])
            tr.set_bus(t, "ui", ui_in)
            tr.set_bus(t, "uio", uio_in)
            r = rec._step(chip, ui_in, uio_in, *a, **kw)
            tr.set_bus(t + p // 4, "uo", chip.outputs()[0], width=7)
            rec.k += 1
            return r
        sim.Chip.step = step
        return self

    def __exit__(self, *exc):
        self._sim.Chip.step = self._step
        self.trace.end(self.k * self.period_ps)
        return False


# ---------------------------------------------------------------- post-freeze protocols
# (docs/post-freeze-protocols.md: firmware written after the RTL freeze)

def dmx512(trace, pin, **kw):
    """sigrok's dmx512 decoder: (packets, errors). Each packet is a dict with
    t (ns, the BREAK's start), brk_ns, mab_ns and slots (bytes: the START code
    first). errors: [(t_ns, text)].

    Two limits of libsigrokdecode 0.5.3's decoder (both legal DMX512): it
    samples a slot's second stop bit at the first sample of the next slot, so
    slots must be separated by some mark (MTBS > 0); and it looks for the next
    BREAK only after 512 channels, so a shorter packet runs into the next one."""
    anns = decode(trace, f"dmx512:dmx={pin}", "dmx512=break:mab:startcode:channel:data:error", **kw)
    pk, err, cur = [], [], None
    for a in anns:
        if a.text == "Break":
            cur = {"t": a.start, "brk_ns": a.end - a.start, "mab_ns": None, "slots": bytearray()}
            pk.append(cur)
        elif a.text == "MAB" and cur is not None:
            cur["mab_ns"] = a.end - a.start
        elif re.fullmatch(r"\d+ / 0x[0-9a-f]+", a.text) and cur is not None:
            cur["slots"].append(int(a.text.split()[0]))
        elif a.text != "Start code" and not a.text.startswith("Channel"):
            err.append((a.start, a.text))
    for p in pk:
        p["slots"] = bytes(p["slots"])
    return pk, err


def lin(trace, pin, baud, version=2, **kw):
    """sigrok's uart + lin stack: frames as dicts {t, pid, id, parity_ok,
    data (bytes), checksum, checksum_ok}; warnings [(t_ns, text)]."""
    anns = decode(trace, f"uart:rx={pin}:baudrate={baud},lin:version={version}",
                  "lin=data:control:error:inline_error", **kw)
    frames, warn, cur = [], [], None
    for a in anns:
        t = a.text
        if t == "Break condition":
            cur = {"t": a.start, "pid": None, "id": None, "parity_ok": None, "data": bytearray(),
                   "checksum": None, "checksum_ok": None, "sync": False}
            frames.append(cur)
        elif cur is None:
            warn.append((a.start, t))
        elif t == "Sync":
            cur["sync"] = True
        elif m := re.match(r"ID: ([0-9A-F]{2}) Parity: (\d) \((ok|bad)\)", t):
            cur["id"] = int(m[1], 16)
            cur["pid"] = cur["id"] | int(m[2]) << 6
            cur["parity_ok"] = m[3] == "ok"
        elif m := re.match(r"Data: 0x([0-9A-F]{2})", t):
            cur["data"].append(int(m[1], 16))
        elif m := re.match(r"Checksum: 0x([0-9A-F]{2})", t):
            cur["checksum"] = int(m[1], 16)
            cur["checksum_ok"] = a.pd == "lin" and cur["checksum_ok"] is not False
        elif t == "Checksum invalid":
            cur["checksum_ok"] = False
            warn.append((a.start, t))
        elif a.pd == "lin" and (t.startswith("P != ") or t.startswith("Sync is not") or t in ("Error",)):
            warn.append((a.start, t))
    for f in frames:
        f["data"] = bytes(f["data"])
    return frames, warn


def ir_nec(trace, pin, polarity="active-low", cd_freq=0, **kw):
    """sigrok's ir_nec decoder: [(t_ns, kind, addr, cmd)] with kind "frame" or
    "repeat", and warnings [(t_ns, text)]."""
    opts = f"ir_nec:ir={pin}:polarity={polarity}" + (f":cd_freq={cd_freq}" if cd_freq else "")
    anns = decode(trace, opts, "ir_nec=leader-code:addr:addr-inv:cmd:cmd-inv:repeat-code:warnings", **kw)
    out, warn, cur = [], [], None
    for a in anns:
        t = a.text
        if t == "Leader code":
            cur = ["frame", None, None, a.start]
        elif t == "Repeat code":
            out.append((a.start, "repeat", None, None))
            cur = None
        elif m := re.match(r"Address: 0x([0-9A-F]{2})", t):
            if cur:
                cur[1] = int(m[1], 16)
        elif m := re.match(r"Command: 0x([0-9A-F]{2})", t):
            if cur:
                cur[2] = int(m[1], 16)
        elif t.startswith("Command#") and cur and cur[2] is not None:
            out.append((cur[3], "frame", cur[1], cur[2]))
            cur = None
        elif "error" in t.lower():
            warn.append((a.start, t))
    return out, warn


def i2s(trace, sck, ws, sd, **kw):
    """sigrok's i2s decoder: [(t_ns, "L"|"R", value)] and warnings."""
    anns = decode(trace, f"i2s:sck={sck}:ws={ws}:sd={sd}", "i2s=left:right:warnings", **kw)
    out, warn = [], []
    for a in anns:
        if m := re.match(r"(Left|Right) channel: ([0-9a-f]+)", a.text):
            out.append((a.start, m[1][0], int(m[2], 16)))
        else:
            warn.append((a.start, a.text))
    return out, warn


def wiegand(trace, d0, d1, bitwidth_ms=4, active="low", **kw):
    """sigrok's wiegand decoder: [(t_ns, bits as a '0'/'1' string)] per frame
    (a frame ends after bitwidth_ms without a pulse), and invalid states."""
    anns = decode(trace, f"wiegand:d0={d0}:d1={d1}:bitwidth_ms={bitwidth_ms}:active={active}",
                  "wiegand=state", **kw)
    out, bad = [], []
    for a in anns:
        if m := re.match(r"(\d+) bits ([01]*)$", a.text):
            out.append((a.start, m[2]))
        elif a.text == "invalid":
            bad.append((a.start, a.text))
    return out, bad


def pwm(trace, pin, polarity="active-high", **kw):
    """sigrok's pwm decoder: [(t_start_ns, t_end_ns, duty_percent)], one per
    period (active edge to active edge). The period is taken from the
    annotation's span: sigrok's own period text has one decimal."""
    anns = decode(trace, f"pwm:data={pin}:polarity={polarity}", "pwm=duty-cycle", **kw)
    out = []
    for a in anns:
        if m := re.fullmatch(r"([0-9.]+)%", a.text):
            out.append((a.start, a.end, float(m[1])))
    return out


def dali(trace, pin, polarity="active-low", **kw):
    """sigrok's dali decoder: [(t_ns, kind, bytes)] with kind "forward"
    (address byte, command byte: 16-bit frames only) or "backward" (the
    answer). active-low is the decoder's name for a bus that idles high."""
    anns = decode(trace, f"dali:dali={pin}:polarity={polarity}", "dali=raw", **kw)
    out, pend = [], None
    for a in anns:
        if m := re.fullmatch(r"Raw data: ([0-9A-F]{2})", a.text):
            if pend is None:
                pend = (a.start, int(m[1], 16))
            else:
                out.append((pend[0], "forward", bytes([pend[1], int(m[1], 16)])))
                pend = None
        elif m := re.fullmatch(r"Reply: ([0-9A-F]{2})", a.text):
            out.append((a.start, "backward", bytes([int(m[1], 16)])))
    return out


def uart_parity(trace, pin, baud, parity="even", **kw):
    """sigrok's uart decoder with a parity bit: [(t_ns, byte)] and
    [(t_ns, warning)] (parity and frame errors among them)."""
    anns = decode(trace, f"uart:rx={pin}:baudrate={baud}:parity={parity}", "uart=rx-data:rx-warnings", **kw)
    data, warn = [], []
    for a in anns:
        if re.fullmatch(r"[0-9A-F]{2}", a.text):
            data.append((a.start, int(a.text, 16)))
        else:
            warn.append((a.start, a.text))
    return data, warn


def graycode(trace, pins, **kw):
    """sigrok's graycode decoder (pins: d0, d1, ...): the count before each
    change of it, in order (the decoder annotates a value when it changes,
    so the last count only appears after one more step). sigrok-cli 0.7.2
    aborts at exit after this decoder, its output complete: tolerated."""
    chans = ":".join(f"d{i}={p}" for i, p in enumerate(pins))
    anns = decode(trace, f"graycode:{chans}", "graycode=count", abort_at_exit_ok=True, **kw)
    return [int(a.text) for a in anns]


def midi(trace, pin, baud=31250, **kw):
    """sigrok's uart + midi decoders: the midi decoder's verbose texts, one
    per message (real-time and errors included), in time order."""
    anns = decode(trace, f"uart:rx={pin}:baudrate={baud},midi",
                  "midi=text-verbose:text-sysreal-verbose:text-error", **kw)
    return [a.text for a in anns]


def modbus(trace, client, server, baud, parity="even", **kw):
    """sigrok's uart + modbus stack, the client's requests on one channel
    (uart TX) and the server's responses on another (uart RX). Returns
    {"cs": [...], "sc": [...]}: per direction, the decoder's annotations
    [(t_ns, text)] (server id, function, "CRC correct" or "CRC should be
    ...", data, errors)."""
    out = {}
    for d in ("cs", "sc"):
        anns = decode(trace, f"uart:rx={server}:tx={client}:baudrate={baud}:parity={parity},modbus",
                      "modbus=" + ":".join(f"{d}-{k}" for k in ("server-id", "function", "crc", "address", "data",
                                                                  "length", "error")), **kw)
        out[d] = [(a.start, a.text) for a in anns]
    return out
