; Raw pad sampler for the KV260 USB replay (host/usb_replay_demo.py): after
; the trigger (TRIG high), uio0..uio2 every clock, 10 three-bit samples per RX
; word (first sample in bits 29-27). Load with IN_BASE = 0 and CLKDIV 1.
; One `in` per clock with autopush at 30 bits: a word every 10 clocks, so a
; READ_FIXED burst draining a word every 8 clocks (quad, SCK = clk) keeps RXF
; from filling and the samples are contiguous. The trigger stays high after
; it rises, so no word after the WAIT is 0: a zero read is an empty RXF.
.program usb_sampler
.shift_in left autopush 30
    wait 1 pin TRIG
.wrap_target
    in pins, 3
.wrap
