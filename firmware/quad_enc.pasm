; Quadrature (incremental) encoder counter, X4 decoding: written after the
; RTL freeze (tag rc-93c2137-t48ba), firmware only
; (docs/post-freeze-protocols.md).
;
; Pins: A and B = A + 1 (IN_BASE = A), the encoder's two outputs. CLKDIV 1.
; Every edge of either line is one step: forward when A leads B (the state
; A | B << 1 runs 0, 1, 3, 2), backward when B leads. Direction: the old B
; XOR the new A. WAIT kind 7 compares the two lines with the last state
; read, on every clock, so an edge that comes while the core is still
; counting the previous one is caught by the next WAIT. Both lines changed
; between two reads: a step was missed (the encoder was too fast, or a
; line is broken): flag FERR is set, the position does not move. A pulse
; that is over before the state is read (a glitch) counts nothing.
;
; Host protocol: none in the FIFOs. The position is R3 (16 bits, two's
; complement, wraps; read it through the core's R3 register at least every
; 32767 steps to extend it); FERR (define, a global flag) is sticky until
; the host clears it. Steps must be at least 15 clocks apart on average
; (checker: the loop takes 14 ticks forward, 15 backward; 300 ns at 50 MHz,
; 3.3 M steps/s, an 830 kHz quadrature signal).
;
; r0 = changed bits, r1 = the last state, r2 = the new state, r3 = position.
;
; Timing annotations (python -m pe.timing firmware/quad_enc.pasm -D A=16 -D FERR=6):
; @assume clkdiv=1
; @deadline step from=w to=w.enter max=15 unit=tick "a step is counted and the next WAIT armed within 15 clocks"
.program quad_enc
.in_pins A
    mov r1, pins
    and r1, 3                         ; the state now
.wrap_target
loop:
    wait pins != r1 mask 3            ; A or B changed               @point w
    mov r2, pins
    and r2, 3                         ; the new state
    mov r0, r1
    xor r0, r2                        ; the lines that changed
    cmp r0, 3
    jmp z, both
    jmp !r0, loop                     ; none any more: a glitch
    shr r1, 1                         ; old B
    xor r1, r2                        ; ^ new A
    and r1, 1
    jmp !r1, back
    add r3, 1                         ; forward
keep:
    mov r1, r2
.wrap
back:
    sub r3, 1                         ; backward
    jmp keep
both:
    flag set FERR                     ; a missed step
    jmp keep
