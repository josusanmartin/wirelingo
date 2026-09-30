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
