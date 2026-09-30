; SPI master, mode 0 (CPOL=0, CPHA=0), MSB first, 8-bit frames.
; SCK = side-set pin, MOSI = OUT pin, MISO = IN pin. Two ticks per SCK phase:
; f_SCK = f_tick / 4. Push bytes in TX FIFO bits [31:24]; received bytes appear in RXF [7:0].
; (Chip select is left to the host or another program: see spi_master_cs.)
.program spi_master
.side_set 1
.shift_out left autopull 8
.shift_in left autopush 8
.wrap_target
    out pins, 1     side 0 [1]      ; stalls here with SCK low when TX is empty
    in pins, 1      side 1 [1]
.wrap
