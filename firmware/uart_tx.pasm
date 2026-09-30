; UART transmitter, 8N1, LSB first. 8 ticks per bit: CLKDIV = f_clk / (8 * baud).
; Pins: side-set pin = TX (also OUT pin). Push one byte per TX FIFO word.
.program uart_tx
.side_set 1 opt
.shift_out right
.wrap_target
    pull block          side 1 [7]  ; stop bit / idle high, stall until data
    ldi r0, 7           side 0 [7]  ; start bit, 8 ticks
bitloop:
    out pins, 1                     ; data bit
    jmp r0--, bitloop          [6]  ; 8 ticks per bit
.wrap
