; WS2812 ("NeoPixel") driver: 800 kbit/s, 10 ticks per bit (CLKDIV = f_clk / 8 MHz).
;   0 bit: 3 ticks high, 7 low (375 / 875 ns);  1 bit: 7 high, 3 low (875 / 375 ns)
; Push 24-bit GRB colours in TX FIFO bits [31:8] (MSB first). The line idles low;
; a gap of > 50 us between frames latches the LEDs.
.program ws2812
.side_set 1
.shift_out left autopull 24
.wrap_target
bitloop:
    out r0, 1           side 0 [2]    ; low part (stalls here, low, when the FIFO is empty)
    jmp !r0, do_zero    side 1 [1]    ; rising edge
do_one:
    jmp bitloop         side 1 [4]    ; long high
do_zero:
    nop                 side 0 [4]    ; short high, then low
.wrap
