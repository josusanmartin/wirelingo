; SPI master at 4 clocks per SCK: mode 0 (CPOL=0, CPHA=0), MSB first, 8-bit
; frames, CLKDIV 1, so SCK = f_clk / 4 (12.5 MHz at 50 MHz).
; SCK = side-set pin, MOSI = OUT pin, MISO = IN pin. Push bytes in TX FIFO
; bits [31:24]; received bytes appear in RXF [7:0].
;
; MISO's pad must be in SYNC_BYPASS (docs/isa.md §2), which is safe here
; because MISO answers our own SCK. The IN reads the pad as it was at the
; clock edge where SCK rose, two clocks after SCK fell: the device has two
; clocks (40 ns at 50 MHz, less the pad and board delays) to present the next
; bit, the same budget spi_master.pasm gets at CLKDIV 2 (8 clocks per SCK).
; Without the bypass the IN would see MISO one clock after SCK fell.
; Chip select is left to the host (PIN_OUT) or a PIN instruction in a wrapper.
.program spi_master_fast
.side_set 1
.shift_out left autopull 8
.shift_in left autopush 8
.wrap_target
    out pins, 1     side 0 [1]      ; SCK falls, MOSI changes; stalls here (SCK low) when TX is empty
    nop             side 1          ; SCK rises: the device samples MOSI
    in pins, 1      side 1          ; MISO as it was at that rise (a full RXF stalls with SCK high)
.wrap
