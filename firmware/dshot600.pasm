; DShot600 ESC protocol transmitter: written after the RTL freeze (tag
; rc-01ce16d-t48ba), firmware only (docs/post-freeze-protocols.md).
;
; Frame: 16 bits MSB first: 11-bit throttle, telemetry request bit, 4-bit
; checksum = (v ^ v >> 4 ^ v >> 8) & 15 with v = throttle << 1 | telemetry,
; computed here. Bit = 1.667 us (600 kbit/s), a rising edge every bit:
; 1 = high 1.25 us (75 %), 0 = high 0.625 us (37.5 %). The line idles low.
; 8 ticks per bit: CLKDIV = f_clk / 4.8 MHz (10 at 48 MHz, exact; 10.42 at
; 50 MHz, so an edge moves by up to one clock, 20 ns).
; Pins: side-set (1 pin) = the ESC signal.
;
; Host protocol: TX FIFO word [11:0] = v (throttle << 1 | telemetry). The
; frame repeats every FRAME ticks with the last value when the FIFO is empty
; (`pull noblock` loads R0, which holds the last value): an ESC sees a steady
; stream without the host. The host should zero R0 before starting (0 = motor
; stop). Commands 1..47 must be repeated by the host as the ESC requires.
;
; r0 = last value, r1 = bit counter, r2 = frame, r3 = bit / scratch.
;
; GAP (define) sets the frame period: FRAME = 146 + GAP ticks (GAP = 454: 600
; ticks = 125 us at 4.8 MHz ticks, an 8 kHz loop; the frame itself is 26.7 us).
;
; Timing annotations (python -m pe.timing firmware/dshot600.pasm -D GAP=454):
; @assume clkdiv=10                                ; 48 MHz: 1 tick = 208.3 ns
; @deadline bit from=rise to=rise avoid=frame min=8 max=8 unit=tick reach=conditional "1.667 us per bit"
; @deadline t1h from=rise to=fall1 avoid=fall0 min=6 max=6 unit=tick "1: high 1.25 us"
; @deadline t0h from=rise to=fall0 avoid=fall1 min=3 max=3 unit=tick reach=conditional "0: high 0.625 us"
; @deadline period from=frame to=frame min=600 max=600 unit=tick "frame period 146 + GAP ticks (GAP = 454)"
.program dshot600
.side_set 1
.shift_out left
.wrap_target
frame:
    pull noblock              side 0   ; next value, or R0 (the last one)  @point frame
    mov r0, osr               side 0   ; remember it
    mov r2, osr               side 0
    and r2, 0xFFF             side 0   ; v (12 bits)
    mov r3, r2                side 0
    shr r3, 4                 side 0
    xor r3, r2                side 0   ; v ^ v >> 4
    mov r1, r2                side 0
    shr r1, 8                 side 0
    xor r3, r1                side 0   ; ^ v >> 8
    and r3, 15                side 0   ; checksum
    shl r2, 4                 side 0
    or r2, r3                 side 0   ; frame = v << 4 | checksum
    mov osr, r2               side 0
    out null, 16              side 0   ; frame to OSR[31:16], MSB first
    ldi r1, 15                side 0
bit:
    out r3, 1                 side 1      ; t0: rising edge              @point rise
    jmp !r3, zero             side 1 [1]  ; t1..t2 high
    nop                       side 1 [2]  ; t3..t5 high: a 1
    nop                       side 0      ; t6 low                       @point fall1
next:
    jmp r1--, bit             side 0      ; t7
    ldi r3, GAP               side 0      ; the line rests low
wait_gap:
    jmp r3--, wait_gap        side 0
.wrap
zero:
    nop                       side 0 [2]  ; t3..t5 low: a 0              @point fall0
    jmp next                  side 0      ; t6
