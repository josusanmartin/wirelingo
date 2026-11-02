; Pulse timestamps: a free-running 48-bit counter of chip clocks, stamped at
; every edge of one input, for a pulse-per-second style signal. CLKDIV must be 1
; (a tick is a clock). IN_BASE = the input; pe.pps does the loading and decoding,
; and docs/pps-capture.md has the limits.
;
; The counter is OSR[15:0]:r3:r2 and counts down, one per tick, through every
; path below: the WAIT decrements r2, and each path that leaves the WAIT takes
; the ticks it used out of the counter when it returns (the `sub r2` and `ldi r2`
; constants). The host loads r1:r3:r2 (r1 is copied to OSR) before the start; the
; loaded value is the counter at the first WAIT attempt.
;
; Each edge is two RX words, pushed together or not at all:
;   word 0: [31:19] drops (mod 8192), [18] 0, [17] 1, [16] level after the edge,
;           [15:0] counter[47:32]
;   word 1: counter[31:0]
; The FIFO is checked first and the core never blocks: with fewer than two free
; words the record is dropped, `drops` counts it, and the counter goes on. So a
; slow host loses edges, never time, and every record stands alone.
;
; W = the tick the WAIT completes. Pad inputs reach a core 2 clocks late (isa §2),
; a constant. An edge on a tick where the WAIT is attempted is stamped exactly;
; between WAITs the input is not looked at, and the next attempt sees the changed
; level:
;   rollover (r2 ran out): next attempt at W+4, W+8 if r3 ran out too. An edge at
;     W+1..W+3 is stamped W+4, up to 3 ticks late (7 at W+8). 3 ticks in 65536.
;     The stamp shows it: r2 = 0xFFFC (0xFFF8 with r3 = 0xFFFF).
;   record: next attempt at W+13; W+16 if the `sub` borrows, W+19 if r3 ran out
;     too. The next edge is then late by up to 12 (15, 18) ticks, and its stamp
;     is exactly that many ticks after this one, which is how the host knows.
; `wait pins != r0` is a level compare: an edge is never lost, only late, as long
; as the level has not returned by the next attempt. A pulse that starts and ends
; between two attempts is not seen at all.
;
; r0 = drops << 3 | 2 | the level being held (the WAIT's reference, mask 1).
; r1 = scratch. r2, r3 = the counter. OSR[15:0] = the top of the counter.
;
; @assume clkdiv=1
; @deadline record from=wait.done to=wait.enter avoid=over min=13 max=19 unit=tick reach=conditional "the record path"
; @deadline rollover from=wait.done to=wait.enter avoid=edge min=4 max=8 unit=tick reach=conditional "the rollover path"
.program pps_capture
.shift_in left autopush 32
    mov osr, r1                   ; the counter's top from the host
    mov r0, pins                  ; the level at the start is not an edge
    and r0, 1
    or r0, 2
.wrap_target
loop:
    wait pins != r0 mask 1 timeout r2     ; W  @point wait
    jmp t, over                   ; W+1
edge:
    xor r0, 1                     ; W+2
    mov r1, status                ; W+3
    and r1, 0xF0                  ; W+4  RXF level, in [7:4]
    cmp r1, 0x70                  ; W+5  C = level <= 6
    jmp !c, drop                  ; W+6
    in r0, 16                     ; W+7
    in osr, 16                    ; W+8  word 0 (autopush; there is room) @stall max=0
    in r3, 16                     ; W+9
    in r2, 16                     ; W+10 word 1 (autopush) @stall max=0
adjust:
    sub r2, 13                    ; W+11
    jmp !c, loop                  ; W+12 next attempt at W+13
    jmp r3--, borrow              ; W+13 r2 went through zero
    mov r1, osr                   ; W+14 and r3 did too
    sub r1, 1                     ; W+15
    mov osr, r1                   ; W+16
    sub r2, 6                     ; W+17
    jmp loop                      ; W+18 next attempt at W+19
borrow:
    sub r2, 3                     ; W+14
    jmp loop                      ; W+15 next attempt at W+16
drop:
    add r0, 8                 [2] ; W+7..9
    jmp adjust                    ; W+10
over:                             ; r2 = 0 at W
    jmp r3--, over1               ; W+2
    mov r1, osr                   ; W+3  r3 ran out
    sub r1, 1                     ; W+4
    mov osr, r1                   ; W+5
    ldi r2, 0xFFF8                ; W+6  0 - 8
    jmp loop                      ; W+7  next attempt at W+8
over1:
    ldi r2, 0xFFFC                ; W+3  0 - 4; next attempt at W+4
.wrap
