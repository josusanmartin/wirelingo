"""Data over endpoint 0 on the full-speed device (firmware/usb_fs_dev.pasm),
for a real host: vendor control transfers into and out of a buffer held by
the host software on the SPI port.

    OUT vendor/device request 0x10: store the data stage in the buffer
    IN  vendor/device request 0x11: return the buffer, up to wLength bytes

The token filter matches one address and endpoint, so data stays on endpoint
0, in bMaxPacketSize0 = 8 packets. Every packet passes through the SPI
software; automatic NAKs cover its latency.

Tested on the model and the KV260 (fpga/kv260/host/usb_fs_data_demo.py).
Zero-length requests (wLength 0) are tested on the model only, with the
software running concurrently with the bus (test/model/test_usb_fs_data.py):
until 2026-11-03 a zero-length write staged its status ZLP twice, and a
zero-length read staged none.
"""
from . import usbdev

REQ_WRITE, REQ_READ = 0x10, 0x11


class LoopbackDevice(usbdev.DeviceSoftware):
    """usbdev.DeviceSoftware plus the two vendor requests."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.buffer = b""
        self.incoming = None        # bytearray of the OUT data stage in progress
        self.expected = 0

    def handle(self, req):
        bm, breq, wlen = req[0], req[1], req[6] | req[7] << 8
        if bm == 0x40 and breq == REQ_WRITE:
            super().handle(bytes(8))        # the reset of handle(); request 0 answers nothing
            self.out_records = []
            self.incoming, self.expected = bytearray(), wlen   # service() stages the status ZLP once
        elif bm == 0xC0 and breq == REQ_READ:
            super().handle(bytes(8))
            data = self.buffer[:wlen]
            self.pending = [data[i:i + 8] for i in range(0, len(data), 8)]
            if len(data) < wlen and len(data) % 8 == 0 or not wlen:
                self.pending.append(b"")    # a short transfer ends in a short packet;
                                            # wLength 0: the status stage is an IN
        else:
            self.incoming = None
            super().handle(req)

    def service(self, mh=None):
        super().service(mh)
        records, self.out_records = self.out_records, []
        if self.incoming is None:
            return
        for data in records:
            self.incoming += data
        if len(self.incoming) >= self.expected:
            self.buffer = bytes(self.incoming[:self.expected])
            self.incoming = None
            self.pending = [b""]            # status stage: zero-length DATA1
