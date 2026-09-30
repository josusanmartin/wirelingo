; UART receiver, 8N1, LSB first. 8 ticks per bit: CLKDIV = f_clk / (8 * baud).
; IN_BASE = RX pin (JMP/WAIT use absolute pin RX, passed as a define).
; The start bit's falling edge is caught at full clock resolution (WAIT edge
; trackers run every cycle), then we sample mid-bit. Byte arrives in RXF [7:0].
; Framing errors set flag F0 and the byte is dropped.
.program uart_rx
.shift_in right
start:
    wait fall RX                    ; start bit edge
    ldi r0, 7                  [10] ; to the middle of data bit 0 (12 ticks)
bitloop:
    in pins, 1
    jmp r0--, bitloop          [6]
    jmp pin RX, good                ; stop bit must be high
    flag set 0                      ; framing error / break
    wait 1 pin RX
    jmp start
good:
    in null, 24                     ; right-align the byte
    push block
