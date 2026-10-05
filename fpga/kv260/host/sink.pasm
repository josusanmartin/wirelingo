; TX FIFO sink for host-port burst tests: sums the 16-bit halves of every
; word into R0 and counts words in R2. Autopull 32, one word per five
; instructions, so it keeps up with a quad burst at SCK = clk (8 clocks/word).
.program sink
.shift_out left autopull 32
.wrap_target
    out r1, 16
    add r0, r1
    out r1, 16
    add r0, r1
    add r2, 1
.wrap
