; Wiegand receiver (panel side): written after the RTL freeze (tag
; rc-01ce16d-t48ba), firmware only (docs/post-freeze-protocols.md).
;
; Pins: D0 and D1 = D0 + 1 (IN_BASE = D0), idle high (pull-ups). A low pulse
; on D0 is a 0, on D1 a 1, MSB first. 1 tick = 1 us: CLKDIV = f_clk / 1 MHz.
; WAIT kind 7 (`wait pins != r mask 3`) catches a pulse on either line in one
; instruction, at clock resolution. A pulse must still be there 8 us after
; its edge (shorter is noise), and must end within PMAX us; both lines low is
; an error. A frame ends when no pulse comes for FTO us (define; > the
; sender's bit interval).
;
; Parity: the CRC tap as a 1-bit LFSR fed by every `in x, 1` (CRCCTRL =
; POLY 1, width 1, IN_TAP) gives the parity of all bits; it is also copied
; after bit 13 and bit 17, the ends of the even-parity halves of the 26-bit
; (H10301) and 34-bit formats. Valid = n is 26 or 34, the whole frame has odd
; parity and its first half even, no error.
;
; Host protocol (RX FIFO), three words per frame: A, B, S.
;   n <= 32: A = the n bits right-aligned, B = 0;  n > 32: A = the first 32,
;   B = the rest right-aligned (a frame over 64 bits keeps its last 32 in B).
;   S = n | valid << 8 | parity of all bits << 9 | parity of bits 1..13 << 10
;       | parity of bits 1..17 << 11 | error << 12.
; pe.postfreeze.wiegand_rx_frames decodes them.
;
; r0 = bits, r1 = 3 (idle reference, also the 1 shifted in) / pulse timeout,
; r2 = parity copies and error, r3 = frame timeout / scratch.
;
; Timing annotations (python -m pe.timing firmware/wiegand_rx.pasm -D D0=0 -D FTO=10000 -D PMAX=500):
; @assume clkdiv=50
; @deadline sample from=edge to=sample avoid=timeout min=8 max=8 unit=tick reach=conditional "the pulse is sampled 8 us after the WAIT sees its edge"
.program wiegand_rx
.shift_in left
frame:
    ldi r0, 0
    ldi r2, 0
    mov isr, null
    mov crc, null
idle:
    ldi r1, 3
next:
    ldi r3, FTO
    wait pins != r1 mask 3 timeout r3 ; a pulse on D0 or D1        @point edge
    jmp t, timeout
    nop                       [5]
    mov r3, pins                      ; 8 us after the edge         @point sample
    and r3, 3
    cmp r3, 3
    jmp z, next                       ; gone: a glitch
    ldi r1, PMAX
    jmp !r3, error                    ; both low
    wait pins != r3 mask 3 timeout r1 ; the pulse ends (or the other line falls)
    jmp t, error                      ; longer than PMAX
    ldi r1, 3
    cmp r3, 1                         ; D1 low: a 1 (D0 is bit 0 of the pins)
    jmp z, one
    in null, 1
    jmp count
one:
    in r1, 1
count:
    add r0, 1
    cmp r0, 13
    jmp !z, not13
    mov r3, crc                       ; parity of bits 1..13
    or r2, r3
not13:
    cmp r0, 17
    jmp !z, not17
    mov r3, crc                       ; parity of bits 1..17
    shl r3, 1
    or r2, r3
not17:
    cmp r0, 32
    jmp !z, next
    push block                        ; A: the first 32 bits
    jmp next
error:
    or r2, 4                          ; the frame is reported, not valid
    wait pins != r3 mask 3            ; until the lines change
    jmp idle
timeout:
    jmp !r0, idle                     ; no frame
    cmp r0, 32
    jmp !c, b_word                    ; n >= 32: A went out at bit 32
    push block                        ; A
    mov isr, null
b_word:
    push block                        ; B
    mov r3, crc
    shl r3, 9
    shl r2, 10
    or r3, r2
    or r3, r0                         ; n | parities | error
    mov r1, r3
    and r1, 0x16FF                    ; n, total parity, parity 1..13, error
    cmp r1, 26 | 0x200
    jmp z, valid
    mov r1, r3
    and r1, 0x1AFF                    ; n, total parity, parity 1..17, error
    cmp r1, 34 | 0x200
    jmp !z, report
valid:
    or r3, 0x100
report:
    mov isr, r3
    push block                        ; S
    jmp frame
