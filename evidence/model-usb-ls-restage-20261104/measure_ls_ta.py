"""Turnarounds of the two-core low-speed device (usb_ls_rx + usb_ls_tx) on the
cycle model, in bit times from the host's EOP J (pe.usbhost.ControlHost):
SETUP DATA0 -> ACK, IN -> NAK (nothing staged), IN -> staged DATA1/DATA0.
usage: measure_ls_ta.py TREE"""
import sys
tree = sys.argv[1]
sys.path[:0] = [tree + "/tools", tree + "/test/model"]
from test_usb_enum import device
from pe import usb, usbdev
from pe.usbhost import ControlHost

mh, sw = device("two-core")
h = ControlHost(mh, bit=32, keepalive=False, retries=0)
ta = {"ACK after SETUP": [], "NAK after IN": [], "DATA after IN": []}
for i in range(8):
    h.setup(0, bytes([0, 9, 1, 0, 0, 0, 0, 0]))
    ta["ACK after SETUP"].append(h.turnarounds[-1])
    mh.pop_all(0)                                    # the SETUP's record, discarded: nothing staged
    h.gap(16)
    h._send(usb.token(usb.PID_IN, 0, 0))
    assert h._response() == usb.handshake(usb.PID_NAK)
    ta["NAK after IN"].append(h.turnarounds[-1])
    h.gap(16)
    pid = usb.PID_DATA1 if i % 2 == 0 else usb.PID_DATA0
    sw.stage(pid, bytes(range(i, i + 8)))
    h._send(usb.token(usb.PID_IN, 0, 0))
    assert h._response() == usb.data_packet(pid, bytes(range(i, i + 8)))
    ta["DATA after IN"].append(h.turnarounds[-1])
    h._steps(2)
    h._send(usb.handshake(usb.PID_ACK))
    h.gap(16)
    mh.pop_all(0)
for k, v in ta.items():
    print(f"{k:16s} {min(v):.3f}-{max(v):.3f} bit times over {len(v)} (spec 2-7.5 at low speed through a hub)")
