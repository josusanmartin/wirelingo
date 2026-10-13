; UART receiver, 8N1, LSB first. 8 ticks per bit: CLKDIV = f_clk / (8 * baud).
; IN_BASE = RX pin (JMP/WAIT use absolute pin RX, passed as a define).
; Each byte arrives in RXF [7:0]. A framing error (stop bit low) sets flag F0;
; the byte has already been pushed by then, so the host should treat the bytes
; around an F0 as suspect.
;
; Timing (D = the tick the start-bit WAIT completes, at most a tick after the
; falling edge): data bits are sampled at D+12, D+20, ..., D+68, the stop bit at
; D+76. The byte is pushed during the stop bit, so a good stop bit returns to
; the start-bit WAIT at D+77. In the selected +3 % true-baud regression it
; can rearm 9 clocks after the next physical START (docs/uart-rx-timing.md).
; The WAIT is on the level, not the edge: arriving just after
; a start edge, an edge WAIT would miss it (its first attempt only samples).
.program uart_rx
.shift_in right
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
    in null, 24                     ; D+70  right-align the byte
    push block                      ; D+71
    nop                        [3]  ; D+72..75
    jmp pin RX, start               ; D+76  stop bit high: next byte
    flag set 0                      ; framing error / break
    wait 1 pin RX
.wrap
