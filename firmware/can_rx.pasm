; CAN 2.0A receiver with ACK (a listening node). 48 ticks per bit
; (CLKDIV = f_clk / (48 * bitrate): 2 at 48 MHz for 500 kbit/s); sample point 24/48.
; RX = input pin (RXD, absolute and IN_BASE); TX = output pin (TXD, 1 = recessive).
;
; * Hard sync on the SOF edge, and the SOF must still be dominant at its
;   sample point (D+24): a shorter low pulse on the idle bus is a glitch, and
;   the receiver goes back to idle with nothing reported and no error flag
;   (docs/fault-testing.md, finding 5). Afterwards every recessive->dominant edge
;   resynchronises: the sample point is 24 ticks after the edge. Without an
;   edge, samples stay 48 ticks apart (WAIT fall with a timeout). The WAIT is
;   re-armed 17 ticks after a sample, 7 before the next nominal edge, which
;   covers the drift over the up-to-10-bit gaps between edges. (With a later
;   sample point the edge can come before the WAIT is armed; a fast
;   transmitter then loses every later resync and drifts off.)
; * Destuffing: the bit after five identical bits is dropped. If it equals them
;   (a stuff error), the frame is abandoned: status 2, a 6-bit error flag
;   (dominant) on TX, then wait for 11 recessive bits (bus idle).
; * CRC15 via the CRC tap (CRCCTRL = 0x4599 | 14<<16 | IN_TAP). SOF..CRC all feed
;   it, so a good frame leaves 0.
; * After 19 destuffed bits the DLC and RTR are read from the ISR to size the rest.
; * The ACK slot is driven dominant when the CRC is good.
;
; RX FIFO per frame (autopush 24, so data words have [31:24] = 0): destuffed
; bits SOF..CRC, MSB first, 24 per word in [23:0]; the last word holds the
; remaining bits right-aligned (19+8n+15 bits in all for a data frame); then a
; status word with [31] = 1 (only status words have it): [1:0] = 1 CRC ok
; (ACKed), 0 CRC error, 2 stuff error (the frame was abandoned).
;
; r0 = bit counter: 0..19 in the header, then 64 + bits still to come;
; r1 = run length; r2 = sampled bit / scratch / timeout budget; r3 = last bit.
; S below = tick of the `sample` instruction.
.program can_rx
.shift_in left autopush 24
idle:
    pin TX, hi
    wait fall RX                      ; SOF edge at D
    mov crc, null                     ; D+1
    mov isr, null                     ; D+2
    ldi r0, 0                         ; D+3
    ldi r1, 0                         ; D+4
    ldi r3, 1                   [18]  ; D+5  last = recessive: SOF starts a run
    mov r2, pins                      ; D+24 = S+0: the SOF at its sample point
    and r2, 1                         ; S+1
    jmp r2, idle                      ; S+2  recessive: a glitch, not a frame
    jmp sof                           ; S+3  (r1 = 0: no stuff check) -> S+4
nextbit:
    wait fall RX timeout r2           ; completes at S'-2 (timeout) or at an edge E
    jmp t, sample                     ;  timeout: sample 2 ticks later, on the grid
    nop                       [21]    ;  edge: sample at E+24
sample:
    mov r2, pins                      ; S+0   RX is bit 0 (IN_BASE = RX)
    and r2, 1                         ; S+1
    cmp r1, 5                         ; S+2
    jmp z, stuffbit                   ; S+3
sof:
    cmp r2, r3                        ; S+4
    jmp z, same                       ; S+5
    ldi r1, 1                         ; S+6
    mov r3, r2                        ; S+7
    jmp store                         ; S+8
same:
    add r1, 1                         ; S+6
    nop                       [1]     ; S+7..8
store:
    in r2, 1                          ; S+9   destuffed bit -> ISR and CRC
    cmp r0, 32                        ; S+10  header phase?
    jmp c, hdr                        ; S+11
    sub r0, 1                         ; S+12
    cmp r0, 64                        ; S+13
    jmp z, eof                        ; S+14
    ldi r2, 29                        ; S+15  (W = S+17, timeout at S+46)
    jmp nextbit                       ; S+16
hdr:
    add r0, 1                         ; S+12
    cmp r0, 19                        ; S+13
    jmp z, parse                      ; S+14
    ldi r2, 29                        ; S+15
    jmp nextbit                       ; S+16
stuffbit:                             ; the stuff bit starts a new run
    cmp r2, r3                        ; S+4
    jmp z, stuff_error                ; S+5   six identical bits
    ldi r1, 1                         ; S+6
    mov r3, r2                        ; S+7
    ldi r2, 36                        ; S+8   (W = S+10, timeout at S+46)
    jmp nextbit                       ; S+9
parse:                                ; 19 bits in: ISR[6] = RTR, ISR[3:0] = DLC
    mov r2, isr                       ; S+15
    and r2, 0x40                      ; S+16
    jmp r2, remote                    ; S+17
    mov r2, isr                       ; S+18
    and r2, 15                        ; S+19
    cmp r2, 8                         ; S+20
    jmp !c, clamp                     ; S+21  DLC >= 8 means 8 bytes
    shl r2, 3                         ; S+22
    jmp setcount                      ; S+23
clamp:
    ldi r2, 64                        ; S+22
    nop                               ; S+23
setcount:
    add r2, 79                        ; S+24  64 + 8n + 15 (CRC)
    mov r0, r2                        ; S+25
    ldi r2, 18                        ; S+26  (W = S+28; an edge before that is
    jmp nextbit                       ; S+27   missed, so this bit uses the grid)
remote:
    ldi r0, 79                        ; S+18  RTR: no data, 15 CRC bits
    ldi r2, 26                        ; S+19  (W = S+21)
    jmp nextbit                       ; S+20
eof:                                  ; S = sample of the last CRC bit
    mov r2, crc                       ; S+15
    jmp r2, bad                       ; S+16
    cmp r1, 5                         ; S+17  the CRC ends in five equal bits:
    jmp !z, delim                     ; S+18  a stuff bit comes before the delimiter
    nop                       [28]    ; S+19..47
    mov r2, pins                      ; S+48  the stuff bit, sampled on the grid
    and r2, 1                         ; S+49
    cmp r2, r3                        ; S+50
    jmp z, stuff_error                ; S+51  six equal bits
    nop                       [14]    ; S+52..66: everything below one bit later
delim:
    nop                       [52]    ; S+19..71  CRC delimiter
    pin TX, lo                [47]    ; S+72      ACK slot: dominant for one bit
    pin TX, hi                        ; S+120
    ldi r3, 1
    jmp tail
bad:
    ldi r3, 0
tail:
    push block                        ; remaining bits (right-aligned)
    ldi r1, 0x8000
    mov isr, r1
    in r3, 16                         ; ISR = 0x8000_0000 | status ([31]: a status word)
    push block
    nop                       [255]   ; ACK delimiter, EOF, intermission (> 10 bits)
    nop                       [255]
    jmp idle
stuff_error:
    pin TX, lo                [255]   ; error flag: 6 dominant bits (288 ticks)
    nop                       [31]
    pin TX, hi
    push block
    ldi r1, 0x8000
    mov isr, r1
    ldi r3, 2
    in r3, 16                         ; status 2: stuff error
    push block
bus_idle:                             ; 11 recessive bits before listening again
    wait 1 pin RX
    ldi r2, 527                       ; 11 x 48 - 1 ticks
    wait 0 pin RX timeout r2
    jmp t, idle                       ; timed out: idle long enough
    jmp bus_idle                      ; dominant bit: still busy
