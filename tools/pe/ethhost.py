"""Host side of a 10BASE-T station: eth10_rx on core 0, eth10_tx_cs on core 1.

The host streams both directions through the chip's FIFOs. `port` is anything
with ProtoEmu's bus calls, read(addr, n=1, fixed=False) and write(addr, words,
fixed=False): pe.ethsim.TimedPort on the model, the pe_spim transport on the
KV260, tools/micropython's ProtoEmu on the demo board. Nothing here is specific
to one of them, and there are no imports beyond eth, so the bundle
(tools/pe/mpy_bundle.py) carries this file too.

    h = Eth10Host(port, clock_hz=50_000_000)
    h.configure(load_writes(50e6))          # PC; on a board, the bundled bursts
    while True:
        for frame in h.service():           # one pass: drain RX, feed TX
            for reply in station.handle(frame):
                h.send(reply)

service() does one pass and returns; call it often. How often is the whole
timing contract, and docs/eth10-network.md has the numbers. RX has no
flow control: 24 line bits per FIFO word, a word every 2.4 us, eight words of
FIFO, and a frame that overflows is lost (and counted). TX has 16 words of
FIFO, a word every 3.2 us, and a host that stops feeding it mid-frame corrupts
that frame: a pass the FIFOs may not have covered abandons the frame.

An empty RX FIFO reads 0, and a run of 24 zero bits, which ARP padding is, makes
a data word that is also 0. So the host reads STATUS for the level first and
never pops more than that. Blind bursts lose those words.
"""
from . import eth

A_CTRL, A_FSET, A_FCLR = 0x801, 0x802, 0x803
A_LINECFG, A_LINEDIV, A_LINEPINS, A_LINESTAT = 0x80C, 0x80D, 0x80E, 0x80F
CORE_BASE, CORE_STRIDE, C_R0, C_STATUS, C_FIFO = 0x400, 0x20, 0xA, 0x9, 0x10
ST_HALTED, ST_FAULT = 1 << 13, 1 << 14
LS_RX_FRAME, LS_RX_END, LS_RX_OVERFLOW = 1 << 17, 1 << 18, 1 << 19
LINECFG_ETH = 0x4005                  # EN | CODE Manchester | RX_EN
RX_CORE, TX_CORE, F_GO = 0, 1, 0
DEPTH = 8                             # tapeout FIFO depth; TX has two stages (.fifo join_tx)
PULSE_BITS = 4                        # records up to this long are link pulses and ends of TP_IDL
MIN_RECORD_BITS = eth.MIN_PREAMBLE_BITS + 8 + 8 * eth.MIN_FRAME
TX_TIMEOUT_US = 20000                 # F_GO to the frame's end: a 1518-byte frame and deferring
WORD_US = 3.2                         # the line takes a 32-bit word in this long


def core_reg(core, off):
    return CORE_BASE + CORE_STRIDE * core + off


def clock_settings(clock_hz):
    """(TX CLKDIV register value, RX clocks per bit) for a 40 or 50 MHz project clock."""
    if clock_hz == 40e6:
        return 1 << 8, 4
    if clock_hz == 50e6:
        return (1 << 8) | 0x40, 5                # 1.25: 4 ticks = 5 clocks, half cells 40/60 ns
    raise ValueError("10BASE-T needs a 40 or 50 MHz project clock")


def load_writes(clock_hz, carrier_sense=True, td_pin=8, rxp=16):
    """PC only: every register write that takes a chip from reset to both cores
    running. TD+ = td_pin, TD- = td_pin + 1 (uo0, uo1), RXP = rxp (ui0).
    carrier_sense=False loads plain eth10_tx."""
    import pathlib
    from . import asm, host
    fw = pathlib.Path(__file__).resolve().parents[2] / "firmware"
    clkdiv, period = clock_settings(clock_hz)
    host.input_pad(rxp)
    name = "eth10_tx_cs" if carrier_sense else "eth10_tx"
    tx = asm.assemble((fw / (name + ".pasm")).read_text(), {"F_GO": F_GO, "RXP": rxp})
    rx = asm.assemble((fw / "eth10_rx.pasm").read_text())
    w = [(A_CTRL, 3 << 16), (A_LINECFG, 0), (A_FCLR, 0xFF)]
    w += host.pin_owner_writes({td_pin: TX_CORE, td_pin + 1: TX_CORE})
    w += host.load_program(RX_CORE, rx, 0, clkdiv=1 << 8)
    w += host.load_program(TX_CORE, tx, 256, side_pins=td_pin, clkdiv=clkdiv)
    w += [(A_LINEDIV, period << 16), (A_LINEPINS, rxp << 8), (A_LINECFG, LINECFG_ETH)]
    w += [(A_CTRL, 3 | (3 << 8))]
    return w


class Eth10Host:
    def __init__(self, port, clock_hz=50_000_000, carrier_sense=True):
        self.port = port
        self.carrier_sense = carrier_sense
        self.period = clock_settings(clock_hz)[1]
        self.records = eth.Records()
        self.txq = []
        self.tx = None                   # words of the frame being sent
        self.txi = 0                     # words pushed so far
        self.go = False                  # F_GO set for it
        self.on_air = False              # and the core has started taking words
        self.t_go = 0
        self.rx_words = self.skipped = 0
        self.tx_timeout_us = TX_TIMEOUT_US
        self.stats = {}

    def count(self, name, n=1):
        self.stats[name] = self.stats.get(name, 0) + n

    def configure(self, bursts):
        """Apply load_writes() output or the bundle's [addr, [words]] bursts."""
        for a, d in bursts:
            self.port.write(a, d)

    def now_us(self):
        try:
            return self.port.now_us()
        except AttributeError:
            import time
            return time.monotonic_ns() // 1000

    def since_us(self, then):
        """us since `then`, across the board clock's wrap where there is one."""
        try:
            return self.port.elapsed_us(self.now_us(), then)
        except AttributeError:
            return self.now_us() - then

    # ------------------------------------------------------------ one pass
    def service(self):
        """Drain what RX has, then give TX what it can take. Returns the frames
        that ended, destination..FCS; their FCS has not been checked."""
        before = self.rx_words
        frames = self._rx()
        # While a frame arrives nothing of ours is on the air, unless a collision
        # has just begun, and every transaction spent on TX is taken from RX. A
        # frame can wait to be loaded; a loaded one gets a look every eighth pass.
        if self.rx_words != before and not self.on_air:
            self.skipped += 1
            if not self.go or self.skipped & 7:
                return frames
        if self.tx is None and self.txq:
            self._tx_load(self.txq.pop(0))
        if self.tx is not None:
            self._tx_feed()
        return frames

    def send(self, raw):
        """Queue a whole frame, destination..FCS."""
        if not eth.MIN_FRAME <= len(raw) <= eth.MAX_FRAME:
            raise ValueError("frame must be 64..1518 bytes, FCS included")
        self.txq.append(bytes(raw))

    def busy(self):
        return self.tx is not None or bool(self.txq)

    # ------------------------------------------------------------ receive
    def _rx(self):
        st = self.port.read(core_reg(RX_CORE, C_STATUS))
        if st & (ST_HALTED | ST_FAULT):
            raise RuntimeError("eth10_rx core halted or faulted")
        n = (st >> 4) & 15
        if not n:
            return []
        words = self.port.read(core_reg(RX_CORE, C_FIFO), n, True)
        if n == 1:
            words = [words]
        self.rx_words += n
        got, suspect = [], False
        for w in words:
            rec = self.records.feed(w)
            if rec is None:
                continue
            if rec[0] <= PULSE_BITS:
                self.count("rx_pulses")
                continue
            suspect = True
            if rec[0] < MIN_RECORD_BITS:
                self.count("rx_runts")
            else:
                got.append(rec)
        if self.records.malformed:
            self.count("rx_malformed", self.records.malformed)
            self.records.malformed = 0
            suspect = True
        if not suspect:
            return []
        # bits lost mid-frame leave a record that looks whole, so ask before trusting it
        if self.port.read(A_LINESTAT) & LS_RX_OVERFLOW:
            self.count("rx_overflows")
            self.count("rx_dropped", len(got))
            self._rx_recover()
            return []
        frames = []
        for count, record in got:
            raw = eth.decode_record(count, record)
            if raw is None:
                self.count("rx_malformed")
            else:
                self.count("rx_frames")
                frames.append(raw)
        return frames

    def _rx_recover(self):
        """eth10_rx's recovery, leaving core 1 and its FIFOs alone."""
        self.port.write(A_LINECFG, 0)
        self.port.write(A_CTRL, 3 | (1 << (8 + RX_CORE)) | (1 << (16 + RX_CORE)))
        self.port.write(A_LINEDIV, self.period << 16)
        self.port.write(A_LINECFG, LINECFG_ETH)
        self.records.reset()

    # ------------------------------------------------------------ transmit
    def _tx_load(self, raw):
        self.port.write(core_reg(TX_CORE, C_R0), 8 * len(raw) - 1)
        raw = raw + bytes(-len(raw) & 3)
        self.tx = [int.from_bytes(raw[i:i + 4], "little") for i in range(0, len(raw), 4)]
        self.txi, self.go, self.on_air = 0, False, False

    def _tx_feed(self):
        t0 = self.now_us()
        st = self.port.read(core_reg(TX_CORE, C_STATUS))
        if st & (ST_HALTED | ST_FAULT):
            raise RuntimeError("eth10_tx core halted or faulted")
        txf, rxf = st & 15, (st >> 4) & 15
        left = len(self.tx) - self.txi
        self.on_air = self.go and txf + rxf < self.txi
        if left and txf < DEPTH:
            k = min(DEPTH - txf, left)
            if not self.go:
                k = min(k, 2 * DEPTH - self.txi)
            if k:
                self.port.write(core_reg(TX_CORE, C_FIFO), self.tx[self.txi:self.txi + k], True)
                self.txi += k
        # A stall can start after this STATUS and end when these words land, and
        # nothing on the chip remembers it. So the FIFOs must have covered the
        # whole pass, counting nothing for the word in the OSR. Fast hosts never
        # get close; a host that does has lost the right to say the frame was good.
        if self.go and left and self.since_us(t0) > (txf + rxf) * WORD_US:
            self.count("tx_underruns")
            self.abort_tx()
            return
        if not self.go:
            if self.txi == len(self.tx) or self.txi >= 2 * DEPTH:
                self._tx_go()
            return
        late = self.since_us(self.t_go) > self.tx_timeout_us
        if self.txi == len(self.tx) or late:
            if not self.port.read(A_FSET) & (1 << F_GO):
                self.count("tx_frames")
                self.tx, self.on_air = None, False
            elif late:
                self.count("tx_timeouts")
                self.abort_tx()

    def _tx_go(self):
        if not self.carrier_sense and self.port.read(A_LINESTAT) & (LS_RX_FRAME | LS_RX_END):
            return                          # plain eth10_tx: the host's own look at the line
        self.port.write(A_FSET, 1 << F_GO)
        self.go, self.t_go = True, self.now_us()

    def abort_tx(self):
        """Give up on the frame in progress or deferring: stop core 1 where it
        is, clear F_GO, then restart it with its FIFOs cleared. The restart
        begins at the link pulse, so a frame cut short never gets a TP_IDL or
        a good FCS."""
        self.port.write(A_CTRL, 1)
        self.port.write(A_FCLR, 1 << F_GO)
        self.port.write(A_CTRL, 3 | (1 << (8 + TX_CORE)) | (1 << (16 + TX_CORE)))
        self.tx, self.go, self.on_air = None, False, False
        self.count("tx_aborts")
