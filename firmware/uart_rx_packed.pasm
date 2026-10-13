; Optional UART RX, 8N1, LSB first: four bytes per RX FIFO word.
; CLKDIV = f_clk/(8*baud), IN_BASE = RX; logical pin RX is a define.
; .shift_in right autopush32 yields little-endian groups: b0 | b1<<8 | ...
; Partial groups (1..3 bytes) stay in ISR; there is no implicit idle flush.
; A low STOP sets sticky F0 without discarding/resetting the partial group.
; The fourth-byte word is pushed at its last data sample, before STOP validation;
; consumers must treat words around F0 as suspect. No FIFO-full lossless promise.
; Uses one core/R0/ISR/F0, no extra storage or host service during a finite burst.
; Timing matches uart_rx: data D+12+8k, STOP D+76, level-WAIT rearm D+77.
.program uart_rx_packed
.shift_in right autopush 32
    wait 1 pin RX                   ; once: the line idles high (a virtual pin starts low)
.wrap_target
start:
    wait 0 pin RX                   ; start bit: D
    ldi r0, 7                  [10] ; D+1..11
bitloop:
    in pins, 1                      ; D+12 + 8k
    jmp !r0, last                   ;  +1
    sub r0, 1                       ;  +2
    jmp bitloop                [4]  ;  +3..7
last:
    nop                             ; D+70 retain accumulated ISR bits
    nop                             ; D+71 packed autopush occurred on fourth byte
    nop                        [3]  ; D+72..75
    jmp pin RX, start               ; D+76  stop bit high: next byte
    flag set 0                      ; framing error / break
    wait 1 pin RX
.wrap
