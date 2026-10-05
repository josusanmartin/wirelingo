; RX FIFO source for host-port burst tests: pushes {R0, R1}, then R0 += 1 and
; R1 += 3, blocking while the RX FIFO is full (no word is lost or repeated).
.program source
.wrap_target
    in r0, 16
    in r1, 16
    push block
    add r0, 1
    add r1, 3
.wrap
