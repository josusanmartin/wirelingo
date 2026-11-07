; Bidirectional DShot600 (inverted DShot with eRPM telemetry from the ESC):
; written after the RTL freeze (tag rc-01ce16d-t48ba), firmware only
; (docs/post-freeze-protocols.md).
;
; One wire, a uio pin with a pull-up (ESC): the line idles high. 1 tick =
; 1 clock (CLKDIV 1): the reply is fast enough that every edge is timed to
; the clock.
;   frame:  16 bits MSB first, each starting with a falling edge: low T1
;           ticks for a 1, T0 for a 0, BIT ticks per bit (DShot600: 1.667 us,
;           1.25 / 0.625 us low); checksum = ~(v ^ v >> 4 ^ v >> 8) & 15 (the
;           inverted, bidirectional form), computed here. Then the pin is
;           released (hiz) at once: the ESC answers about 30 us later.
;   reply:  21 bits at 5/4 the rate (750 kbit/s: RBIT ticks), line levels
;           l[0] = 0 (the falling edge that starts it) .. l[20]. Received by
;           timing each edge with a WAIT on "the pin differs from its last
;           level" (kind 7) and its timeout counter: an interval of n bits
;           (n = 1..3 by thresholds at 1.5, 2.5 RBIT; none within 3.5: the
;           reply ended) shifts in n copies of the level it ended. So the
;           sampling re-synchronises on every edge, whatever the ESC's clock.
;   The host turns the 21 levels into telemetry: gcr = l ^ l >> 1 (20 bits),
;   4 x 5-bit GCR -> 4 nibbles, checksum (pe.postfreeze.dshot_telemetry).
;
; Host protocol: TX FIFO word [11:0] = v (throttle << 1 | telemetry bit);
; with the FIFO empty the last value repeats (R0, as in dshot600). RX FIFO,
; one word per frame: levels << 8 | count (count = 21; 0 = no reply within
; RTMO ticks). The frame period is set by GAP (ticks of rest after a reply
; or its timeout).
; Defines (pe.postfreeze.dshot_bidir_defines(f_clk)): ESC (pin), BIT, T0,
; T1, RBIT, RTMO, GAP. 48 MHz: BIT 80, T0 30, T1 60, RBIT 64.
;
; r0 = last value, r1 = bit counter / line level, r2 = frame / count,
; r3 = scratch / timeout.
;
; Timing annotations (python -m pe.timing firmware/dshot_bidir.pasm -D ESC=0 -D BIT=80 -D T0=30 -D T1=60 -D RBIT=64 -D RTMO=4800 -D GAP=1000):
; @assume clkdiv=1                                 ; 48 MHz: 1 tick = 20.83 ns
; @deadline bit from=fall to=fall avoid=frame min=80 max=80 unit=tick reach=conditional "1.667 us per bit"
; @deadline t1l from=fall to=rise1 avoid=rise0 min=60 max=60 unit=tick "1: low 1.25 us"
; @deadline t0l from=fall to=rise0 avoid=rise1 min=30 max=30 unit=tick reach=conditional "0: low 0.625 us"
; @deadline release from=frame to=release min=1298 max=1298 unit=tick "the frame (18 ticks of setup + 16 bits) and the release, at once"
.program dshot_bidir
.side_set 1 opt
.shift_out left
.shift_in left
.wrap_target
frame:
    pull noblock                      ; next value, or R0 (the last one)   @point frame
    mov r0, osr
    mov r2, osr
    and r2, 0xFFF                     ; v
    mov r3, r2
    shr r3, 4
    xor r3, r2
    mov r1, r2
    shr r1, 8
    xor r3, r1
    xor r3, 15                        ; inverted checksum (bidirectional DShot)
    and r3, 15
    shl r2, 4
    or r2, r3
    mov osr, r2
    out null, 16                      ; frame to OSR[31:16]
    ldi r1, 15
    pin ESC, hi                       ; drive the idle level
bit:
    out r3, 1                 side 0 [(T0 - 2) / 2]          ; falling edge    @point fall
    jmp !r3, zero             side 0 [T0 - 2 - (T0 - 2) / 2]
    nop                       side 0 [T1 - T0 - 1]           ; a 1 stays low
    nop                       side 1 [BIT - T1 - 3]          ; @point rise1
next:
    jmp r1--, bit             side 1 [1]
    pin ESC, hiz                      ; release: the ESC answers on this wire   @point release
; ---------------------------------------------------------------- the reply
    ldi r3, RTMO
    ldi r1, 1                         ; the idle level, before the reply's start edge
    wait pins != r1 mask 1 timeout r3 ; the reply starts (a falling edge)
    jmp t, none
    ldi r2, 0                         ; levels received
edge:
    xor r1, 1                         ; the line's level now
    ldi r3, (7 * RBIT) / 2 - 8        ; 3.5 bits: then the reply has ended
    wait pins != r1 mask 1 timeout r3
    jmp t, tail
    ; ticks since the last edge ~ (7 RBIT / 2 - 8) - r3 + 12 (the 9 to 15
    ; ticks of instructions between two WAITs, so +-3 ticks of 64)
    cmp r3, 2 * RBIT + 4
    jmp !c, one_bit                   ; < 1.5 bits
    cmp r3, RBIT + 4
    jmp !c, two_bits                  ; < 2.5 bits
    in r1, 1                          ; 3 bits of this level
    add r2, 1
two_bits:
    in r1, 1
    add r2, 1
one_bit:
    in r1, 1
    add r2, 1
    cmp r2, 21
    jmp c, edge                       ; until 21 levels
    jmp report
tail:                                 ; no edge for 3.5 bits: the rest is this level
    cmp r2, 21
    jmp !c, report
    in r1, 1
    add r2, 1
    jmp tail
none:
    ldi r2, 0
report:
    in r2, 8                          ; levels << 8 | count
    push noblock
    mov isr, null
    ldi r3, GAP
rest:
    jmp r3--, rest
.wrap
zero:
    nop                       side 1 [BIT - T0 - 4]          ; @point rise0
    jmp next                  side 1
