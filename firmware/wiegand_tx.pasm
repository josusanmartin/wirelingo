; Wiegand transmitter (card reader side): written after the RTL freeze (tag
; rc-93c2137-t48ba), firmware only (docs/post-freeze-protocols.md).
;
; Pins D0, D1: open drain (OD_MASK) with pull-ups, idle high. A 0 is a low
; pulse on D0, a 1 on D1, PW us long (define; Wiegand readers use 20..100,
; typically 50), one bit every IV us (define; typically 1000..2000), MSB first.
; After a frame the lines rest FG us (define) so the panel sees its end.
; 1 tick = 1 us: CLKDIV = f_clk / 1 MHz (50 at 50 MHz).
;
; Formats: n data bits (1..32) framed by parity: a leading even-parity bit
; over the first floor(n/2) data bits and a trailing odd-parity bit over the
; rest, computed here with the CRC tap as a 1-bit LFSR (CRCCTRL = POLY 1,
; width 1, OUT_TAP): n = 24 is the 26-bit H10301 format, n = 32 the 34-bit
; one. Or raw: the n bits alone.
;
; Host protocol (TX FIFO), per frame: word 0 = n | raw << 8, word 1 = the
; data left-aligned (first bit in bit 31). pe.postfreeze.wiegand_words.
;
; r0 = bit / interval counter, r1 = bits left, r2 = raw flag (and the even
; parity bit while it is computed), r3 = odd parity.
;
; Timing annotations (python -m pe.timing firmware/wiegand_tx.pasm -D D0=0 -D D1=1 -D PW=50 -D IV=2000 -D FG=20000):
; @assume clkdiv=50                                ; 50 MHz: 1 tick = 1 us
; @deadline pulse0 from=lo0 to=hi0 min=50 max=50 unit=tick "a 0: D0 low for PW us"
; @deadline pulse1 from=lo1 to=hi1 min=50 max=50 unit=tick "a 1: D1 low for PW us"
; @deadline interval from=lo to=lo avoid=gap stall=data:0 min=2000 max=2000 unit=tick "bit to bit: IV us"
; @deadline frame_gap from=lo to=lo min=2000 stall=data:0 unit=tick "bit to bit, or a frame gap"
.program wiegand_tx
.shift_out left
top:
    pull block                        ; header
    mov r1, osr
    mov r2, r1
    and r2, 0x100                     ; raw?
    and r1, 63                        ; n
    pull block                        ; data, left-aligned   @point data
    jmp r2, raw
    mov isr, osr                      ; keep a copy
    mov r3, r1
    shr r3, 1                         ; h = n / 2
    mov r0, r3
    sub r0, 1
    mov crc, null                     ; even parity over the first h bits
p1:
    out null, 1                       ; (each 1-bit OUT feeds the CRC tap)
    jmp r0--, p1
    mov r2, crc
    mov r0, r1
    sub r0, r3
    sub r0, 1
    mov crc, ~null                    ; odd parity over the other n - h bits
p2:
    out null, 1
    jmp r0--, p2
    mov r3, crc
    mov osr, isr                      ; the data again
    mov r0, r2
    ldi r2, 0                         ; r2 = 0: not raw
    call pulse                        ; even parity bit
    sub r1, 1                         ; as the loop's `jmp r1--`
bits:
    out r0, 1                 [1]     ; as long as the odd parity's `jmp r2`, `mov`
    call pulse
    jmp r1--, bits
    jmp r2, gap                       ; raw: no parity bits (r2 = 0x100)
    mov r0, r3
    call pulse                        ; odd parity bit
gap:
    ldi r0, FG                        ; frame gap                @point gap
fg:
    jmp r0--, fg
    jmp top
raw:
    sub r1, 1
    jmp bits
; ---- a pulse on D0 (r0 = 0) or D1, then the rest of the bit interval
pulse:
    jmp !r0, zero
    pin D1, lo                [PW - 1]  ; @point lo1 @point lo
    pin D1, hi                          ; @point hi1
    jmp rest
zero:
    pin D0, lo                [PW - 1]  ; @point lo0 @point lo
    pin D0, hi                [1]       ; @point hi0
rest:
    ldi r0, IV - PW - 10
iv:
    jmp r0--, iv
    ret
