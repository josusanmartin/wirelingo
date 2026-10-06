"""A cycle-level USB low-speed host (bus master) for simulations on ModelHost.

It drives D+/D- through the model's external pin drivers and decodes the
device's answers from the resolved bus. Transactions retry on NAK or on no
answer, the way a real host does.
"""
from . import usb

LINE = {usb.J: (0, 1), usb.K: (1, 0), usb.SE0: (0, 0)}
DP, DM = 0, 1


class UsbHost:
    def __init__(self, mh, bit=32, idle=None):
        """mh: ModelHost. bit: clocks per bit. idle(mh): called between packets
        (lets the device-side software run)."""
        self.mh, self.bit = mh, bit
        self.idle = idle or (lambda mh: None)
        self.log = []
        mh.pull_up |= 1 << DM  # device's 1.5k pull-up on D-: J when idle

    # -- bit level
    def _send(self, packet):
        mh = self.mh
        mh.ext_oe |= 0b11
        for s in usb.encode(packet):
            dp, dm = LINE[s]
            mh.uio = (mh.uio & ~0b11) | dp | (dm << 1)
            mh.step(self.bit)
        mh.ext_oe &= ~0b11

    def _listen(self, cycles):
        """Record the bus until an EOP after a packet, or `cycles` elapse."""
        mh = self.mh
        trace, start = [], None
        for i in range(cycles):
            v = mh.bus_uio() & 3
            s = {0b10: usb.J, 0b01: usb.K, 0b00: usb.SE0}.get(v, "?")
            trace.append(s)
            if start is None and s == usb.K:
                start = i
            if start is not None and len(trace) > 2 and trace[-1] == usb.J and trace[-2] == usb.SE0:
                break
            mh.step(1)
        if start is None:
            return None
        states = [trace[j] for j in range(start + self.bit // 2, len(trace), self.bit)]
        try:
            return usb.decode([usb.J] + states)
        except ValueError:
            return b"?"

    def _gap(self, bits=4):
        self.mh.step(bits * self.bit)

    # -- transactions
    def setup(self, addr, req, tries=20):
        for _ in range(tries):
            self._send(usb.token(usb.PID_SETUP, addr, 0))
            self._gap(2)
            self._send(usb.data_packet(usb.PID_DATA0, req))
            r = self._listen(20 * self.bit)
            self.log.append(("SETUP", req.hex(), r))
            self.idle(self.mh)
            if r == usb.handshake(usb.PID_ACK):
                return True
        raise TimeoutError("SETUP not acknowledged")

    def data_in(self, addr, endp, tries=2000):
        """IN transaction: returns (pid, payload); retries while the device NAKs."""
        for _ in range(tries):
            self._send(usb.token(usb.PID_IN, addr, endp))
            r = self._listen(120 * self.bit)
            if r and len(r) >= 3 and r[0] in (usb.pid_byte(usb.PID_DATA0), usb.pid_byte(usb.PID_DATA1)):
                payload, crc = r[1:-2], r[-2:]
                assert usb.crc16(payload) == crc, "bad CRC from device"
                self._gap(2)
                self._send(usb.handshake(usb.PID_ACK))
                self.log.append(("IN", r[0], payload.hex()))
                self.idle(self.mh)
                return r[0], payload
            self.log.append(("IN", "nak" if r == usb.handshake(usb.PID_NAK) else r, None))
            self.idle(self.mh)
            self._gap(8)
        raise TimeoutError("IN never answered with data")

    def data_out(self, addr, endp, pid, payload, tries=20):
        for _ in range(tries):
            self._send(usb.token(usb.PID_OUT, addr, endp))
            self._gap(2)
            self._send(usb.data_packet(pid, payload))
            r = self._listen(20 * self.bit)
            self.log.append(("OUT", payload.hex(), r))
            self.idle(self.mh)
            if r == usb.handshake(usb.PID_ACK):
                return True
        raise TimeoutError("OUT not acknowledged")

    def control_read(self, addr, req, length):
        """SETUP, IN data stage (DATA1, DATA0, ...), OUT zero-length status stage."""
        self.setup(addr, req)
        out = b""
        while len(out) < length:
            pid, payload = self.data_in(addr, 0)
            out += payload
            if len(payload) < 8:
                break
        self.data_out(addr, 0, usb.PID_DATA1, b"")
        return out[:length]

    def control_write_nodata(self, addr, req):
        """SETUP then an IN status stage (zero-length DATA1 from the device)."""
        self.setup(addr, req)
        pid, payload = self.data_in(addr, 0)
        assert payload == b""


class Stall(Exception):
    """The device answered a transaction with STALL."""


class ControlHost(UsbHost):
    """Control transfers the way a host controller runs them, pins only and
    independent of the device firmware: DATA toggles (a retransmission with the
    old toggle is ACKed and discarded), up to `retries` retries of a transaction
    that got no answer or a garbled one, STALL raised as `Stall`, keep-alive
    EOPs once per millisecond, and injected faults:

      faults["lose_handshake"]  the device's ACK of a SETUP or OUT DATA is lost:
                                the host retries the transaction;
      faults["lose_host_ack"]   the host takes the device's DATA but its ACK is
                                lost on the way (the device never sees it);
      faults["bad_data"]        the device's DATA arrives damaged: no ACK, retry.

    Each counter is consumed as the opportunities come. `turnarounds` records
    the device's response time in bit times (USB 2.0 §7.1.19: the device must
    answer within 6.5, the host waits 16-18; low speed allows 7.5 at the device
    through a hub, which this host model treats as the limit)."""

    TIMEOUT_BITS = 18

    def __init__(self, mh, bit=32, ppm=0.0, gap_bits=4, keepalive=True, retries=3):
        super().__init__(mh, bit)
        self.tpb = bit * (1 + ppm * 1e-6)
        self.gap_bits = gap_bits
        self.keepalive = keepalive
        self.retries = retries
        self.faults = {}
        self.injected = {}
        self.turnarounds = []
        self.wire = []                 # every packet on the bus, in order (bytes)
        self.next_keepalive = mh.chip.cycle + 48000
        self.frac = 0.0

    def fault(self, kind):
        if self.faults.get(kind, 0) > 0:
            self.faults[kind] -= 1
            self.injected[kind] = self.injected.get(kind, 0) + 1
            return True
        return False

    # -- bus level
    def _steps(self, bits):
        self.frac += bits * self.tpb
        n = int(self.frac)
        self.frac -= n
        self.mh.step(n)

    def _drive(self, states):
        mh = self.mh
        mh.ext_oe |= 0b11
        for s in states:
            dp, dm = LINE[s]
            mh.uio = (mh.uio & ~0b11) | dp | (dm << 1)
            self._steps(1)
        mh.ext_oe &= ~0b11

    def _send(self, packet):
        self.wire.append(bytes(packet))
        self._drive(usb.encode(packet))

    def _response(self):
        """The device's packet (bytes, b"?" if undecodable, None if nothing came
        within the timeout), its turnaround recorded. Called right after our EOP
        (whose J bit has just ended)."""
        mh = self.mh
        trace, start = [], None
        limit = int(self.TIMEOUT_BITS * self.bit)
        i = 0
        while True:
            v = mh.bus_uio() & 3
            s = {0b10: usb.J, 0b01: usb.K, 0b00: usb.SE0}.get(v, "?")
            if start is None:
                if s == usb.K:
                    start = i
                    self.turnarounds.append(1 + i / self.bit)   # from the EOP's J
                elif i >= limit:
                    return None
            if start is not None:
                trace.append(s)
                if len(trace) > 2 and trace[-1] == usb.J and trace[-2] == usb.SE0:
                    break
                if len(trace) > 200 * self.bit:
                    return b"?"
            mh.step(1)
            i += 1
        return self._decode(trace)

    def _decode(self, trace):
        """Bus states, one per clock from the device's first K -> packet bytes."""
        states = [trace[j] for j in range(self.bit // 2, len(trace), self.bit)]
        try:
            r = usb.decode([usb.J] + states)
        except ValueError:
            r = b"?"
        self.wire.append(r)
        return r

    def gap(self, bits=None):
        self._steps(self.gap_bits if bits is None else bits)
        if self.keepalive and self.mh.chip.cycle >= self.next_keepalive:
            self._drive([usb.SE0, usb.SE0, usb.J])        # low-speed keep-alive
            self.next_keepalive = self.mh.chip.cycle + 48000
            self._steps(self.gap_bits)

    def bus_reset(self, cycles=480000, recovery=48000 * 10):
        """SE0 for `cycles` (10 ms by default), then J and reset recovery."""
        mh = self.mh
        mh.ext_oe |= 0b11
        mh.uio &= ~0b11
        mh.step(cycles)
        mh.ext_oe &= ~0b11
        mh.step(recovery)
        self.next_keepalive = mh.chip.cycle

    # -- transactions
    def setup(self, addr, req):
        for _ in range(self.retries + 1):
            self._send(usb.token(usb.PID_SETUP, addr, 0))
            self._steps(2)
            self._send(usb.data_packet(usb.PID_DATA0, req))
            r = self._response()
            self.log.append(("SETUP", addr, req.hex(), r))
            self.gap()
            if r == usb.handshake(usb.PID_ACK) and not self.fault("lose_handshake"):
                return
        raise TimeoutError("SETUP not acknowledged")

    def data_in(self, addr, toggle):
        """One IN transaction expecting DATA<toggle>; returns the payload."""
        want = usb.pid_byte(usb.PID_DATA1 if toggle else usb.PID_DATA0)
        errors = 0
        while True:
            self._send(usb.token(usb.PID_IN, addr, 0))
            r = self._response()
            if r == usb.handshake(usb.PID_STALL):
                self.log.append(("IN", addr, "STALL"))
                self.gap()
                raise Stall()
            ok = (r and len(r) >= 3 and r[0] in (usb.pid_byte(usb.PID_DATA0), usb.pid_byte(usb.PID_DATA1))
                  and usb.crc16(r[1:-2]) == r[-2:])
            if ok and self.fault("bad_data"):
                ok = False
            if not ok:
                self.log.append(("IN", addr, "error", r))
                errors += 1
                if errors > self.retries:
                    raise TimeoutError(f"IN to {addr}: {r!r}")
                self.gap()
                continue
            lost = r[0] == want and self.fault("lose_host_ack")
            if not lost:
                self._steps(2)
                self._send(usb.handshake(usb.PID_ACK))
            self.log.append(("IN", addr, "DATA1" if r[0] == usb.pid_byte(usb.PID_DATA1) else "DATA0",
                             r[1:-2].hex(), "ack lost" if lost else "ack"))
            self.gap()
            if r[0] == want:
                return r[1:-2]
            # the old toggle: a retransmission of data we already have (our
            # ACK was lost); acknowledged above and discarded

    def data_out(self, addr, pid, payload):
        for _ in range(self.retries + 1):
            self._send(usb.token(usb.PID_OUT, addr, 0))
            self._steps(2)
            self._send(usb.data_packet(pid, payload))
            r = self._response()
            self.log.append(("OUT", addr, payload.hex(), r))
            self.gap()
            if r == usb.handshake(usb.PID_STALL):
                raise Stall()
            if r == usb.handshake(usb.PID_ACK) and not self.fault("lose_handshake"):
                return
        raise TimeoutError("OUT not acknowledged")

    # -- control transfers (USB 2.0 §8.5.3)
    def control_read(self, addr, req, length=None):
        """SETUP, IN data stage (DATA1, DATA0, ...) until wLength bytes or a
        short packet, then the zero-length OUT status stage."""
        wlength = req[6] | req[7] << 8
        self.setup(addr, req)
        out, toggle = b"", 1
        while len(out) < wlength:
            payload = self.data_in(addr, toggle)
            toggle ^= 1
            out += payload
            if len(payload) < 8:
                break
        self.data_out(addr, usb.PID_DATA1, b"")
        return out

    def control_write(self, addr, req, data=b""):
        """SETUP, OUT data stage if any, then the zero-length IN status stage."""
        self.setup(addr, req)
        toggle = 1
        for i in range(0, len(data), 8):
            self.data_out(addr, usb.PID_DATA1 if toggle else usb.PID_DATA0, data[i:i + 8])
            toggle ^= 1
        payload = self.data_in(addr, 1)
        if payload:
            raise AssertionError(f"status stage carried data {payload.hex()}")

    def get_descriptor(self, addr, dtype, length, index=0):
        return self.control_read(addr, bytes([0x80, 6, index, dtype, 0, 0, length & 255, length >> 8]))
