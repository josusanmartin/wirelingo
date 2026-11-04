"""Who gives the KV260 back, from the moment there is something to give back.

eth_tap.run_closing makes a Held before it builds anything and passes its `hold` to
the builder. fabric_eth.board_rig calls hold(closer) as soon as it has the board's
three objects, before its first write that changes anything (the chip's reset, then
v2_demo.Hw's move of the host port to the fabric master, quad, the cores). From then
on run_closing's finally runs that closer, whatever ended the run and wherever: there
is no stretch of code in which the board has been touched and nobody holds it.

Before hold() only fabric_eth.board_parts has run: it maps the fabric, reads pe_spim's
VERSION and pe_lab's MAGIC, and writes the GPIO path's idle levels (rst_n high, CS_n
high, uio drive 0xFF), which are what the board is given back to anyway. A signal
there leaves nothing to give back. (verifier reports 029, 031, 034)
"""
import signal

SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def shielded(fn):
    """fn() with SIGINT, SIGTERM and SIGHUP ignored, so a second signal can't cut a
    give-back short; the handlers are put back after."""
    old = [signal.signal(n, signal.SIG_IGN) for n in SIGNALS]
    try:
        return fn()
    finally:
        for n, h in zip(SIGNALS, old):
            signal.signal(n, h)


class Held:
    """At most one closer: the latest one given to hold(). close() runs it once,
    shielded. A closer is set by one assignment, so it is either there or not."""

    def __init__(self):
        self.closer = None

    def hold(self, closer):
        self.closer = closer

    def close(self):
        closer, self.closer = self.closer, None
        if closer is not None:
            shielded(closer)
        return closer is not None
