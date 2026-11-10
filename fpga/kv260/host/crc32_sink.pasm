; CRC-32 sink for bringup_kria.py's crc32 step (v0.3): every bit of every TX
; FIFO word goes out of the OSR on its own (OUT null, 1), MSB first, so with
; CRCCTRL OUT_TAP and W = 32 each one feeds the CRC (docs/isa.md §4.5).
; Autopull 32: the core stalls while the FIFO is empty, so the host's pace
; doesn't change the result.
.program crc32_sink
.shift_out left autopull 32
.wrap_target
    out null, 1
.wrap
