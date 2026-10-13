; UART transmitter, 8N1, LSB first. 8 ticks per bit: CLKDIV = f_clk / (8 * baud).
; Pins: side-set pin = TX (also OUT pin). Push one byte per TX FIFO word.
;
; Timing annotations (python -m pe.timing firmware/uart_tx.pasm; docs/timing-check.md).
; The TX pin changes when `ldi side 0` runs (start bit), when `out pins` completes
; (data bits) and when `pull` is first attempted (side-set acts on every attempt:
; stop bit). Each bit must last exactly 8 ticks.
; @assume clkdiv=52.083                          ; 115200 baud at 48 MHz (fractional)
; @deadline start_bit from=start to=bit min=8 max=8 unit=tick "start bit"
; @deadline data_bit from=bit to=bit avoid=stop min=8 max=8 unit=tick "bit to bit inside a byte"
; @deadline last_bit from=bit to=stop.enter avoid=bit min=8 max=8 unit=tick "bit 7 to the stop bit"
; @deadline frame from=start to=stop.enter min=72 max=72 unit=tick "start + 8 data bits"
; @deadline stop_bit from=stop.enter to=start min=8 unit=tick "stop bit (and idle: as long as the FIFO is empty)"
.program uart_tx
.side_set 1 opt
.shift_out right
.wrap_target
    pull block          side 1 [7]  ; stop bit / idle high, stall until data   @point stop
    ldi r0, 7           side 0 [7]  ; start bit, 8 ticks                       @point start
bitloop:
    out pins, 1                     ; data bit                                 @point bit
    jmp r0--, bitloop          [6]  ; 8 ticks per bit
.wrap
