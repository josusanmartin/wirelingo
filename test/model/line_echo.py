"""Attribute every line-RX overflow in a USB device test to its receiver frame.

The line unit's receiver listens on the same pads its transmitter drives, so it
decodes every packet the device itself sends (docs/isa.md §4.7). The USB
firmware queues a whole reply with OUT LINE and only then drains that echo
(`echo:` in usb_ls_dev/usb_enum and the UART report consumers). A reply longer
than the 32-bit RX queue therefore drops its own tail bits and sets the sticky,
unread overflow bit. Those bits are discarded by design.

`EchoProbe` wraps one chip's line unit and records, without changing it:

* each receiver frame: the cycle it began, whether the device's transmitter was
  active then (an echo of its own packet), bits queued and bits dropped;
* the RX state at the first clock of every peer (external) drive.

`check()` asserts that the overflow is harmless in the strong sense: every
dropped bit belongs to a frame the device's own transmitter started, no peer
frame drops a bit, and every peer drive begins with an empty RX queue, no
pending frame end and the receiver idle, so the echo was fully drained before
the next peer SYNC. A dropped peer bit, or a peer packet that starts while echo
remains queued, fails the check.
"""


class EchoProbe:
    def __init__(self, chip, peer_driving):
        """peer_driving(): True while an external driver owns the USB pads."""
        self.chip, self.peer_driving = chip, peer_driving
        self.frames, self.peer_starts = [], []
        self._peer = False
        line = chip.line
        step, commit = line.step, line.commit

        def probed_step(pins_in, rx_mid=None):
            peer = bool(peer_driving())
            if peer and not self._peer:
                self.peer_starts.append(dict(cycle=chip.cycle, rx_queued=len(line.rq),
                                             end_pending=bool(line.end_pend), rx_in_frame=bool(line.rx_on),
                                             tx_active=bool(line.tx_on)))
            self._peer = peer
            was_on = line.rx_on
            step(pins_in, rx_mid)
            if line.en and line.rx_en and line.rx_on and not was_on:
                self.frames.append(dict(begin=chip.cycle, own=bool(line.tx_on), peer=peer,
                                        queued=0, dropped=0, peak=len(line.rq), end=None))
            elif was_on and not line.rx_on and self.frames:
                self.frames[-1]["end"] = chip.cycle

        def probed_commit():
            new = line.en and getattr(line, "_new_rx", None) is not None
            full = len(line.rq) >= 32
            commit()
            if new and self.frames:
                frame = self.frames[-1]
                frame["dropped" if full else "queued"] += 1
                frame["peak"] = max(frame["peak"], len(line.rq))
            elif new:
                raise AssertionError("received bit outside any recorded frame")

        line.step, line.commit = probed_step, probed_commit

    def check(self):
        """Assert the echo-only overflow contract; return a JSON-ready summary."""
        drops = sum(f["dropped"] for f in self.frames)
        assert drops == self.chip.stats.get("line:rx_overflow", 0), "unattributed overflow"
        dirty = [s for s in self.peer_starts
                 if s["rx_queued"] or s["end_pending"] or s["rx_in_frame"] or s["tx_active"]]
        assert not dirty, ("peer drive began before the echo was drained", dirty[:3])
        foreign = [f for f in self.frames if f["dropped"] and not f["own"]]
        assert not foreign, ("line-RX overflow outside the device's own echo", foreign[:3])
        assert not any(f["own"] and f["peer"] for f in self.frames), "driver overlap"
        own = [f for f in self.frames if f["own"]]
        peer = [f for f in self.frames if not f["own"]]
        return dict(dropped_bits=drops, own_frames=len(own),
                    own_frames_overflowing=sum(1 for f in own if f["dropped"]),
                    own_bits_max=max((f["queued"] + f["dropped"] for f in own), default=0),
                    peer_frames=len(peer), peer_starts=len(self.peer_starts),
                    peer_peak_rx_queued=max((f["peak"] for f in peer), default=0))
