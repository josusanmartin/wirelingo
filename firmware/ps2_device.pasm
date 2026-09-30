; PS/2 device (keyboard/mouse side) transmitter. Tick = 10 us (CLKDIV = f_clk / 100 kHz).
; CLK and DAT are open-drain (OD_MASK) with pull-ups; OUT pin = DAT; IN pins read CLK.
; Frames: start 0, 8 data bits LSB first, odd parity, stop 1. The device drives
; CLK at 12.5 kHz; data changes while CLK is high, the host samples on the falling edge.
; Odd parity comes from the CRC tap configured as a 1-bit LFSR:
;   CRCCTRL: POLY = 1, CRC_W = 0 (width 1), OUT_TAP  ->  CRC = 1 ^ data bits.
; Push one byte per TX word ([7:0]).
.program ps2_device
.shift_out right
top:
    pull block
    wait 1 pin CLK                    ; host not inhibiting (CLK released)
    mov crc, ~null                    ; parity preset (width 1 -> 1)
    pin DAT, lo               [1]     ; start bit
    pin CLK, lo               [3]
    pin CLK, hi               [1]
    ldi r0, 7
bit:
    out pins, 1               [1]     ; data (feeds the parity LFSR)
    pin CLK, lo               [3]
    pin CLK, hi               [0]
    jmp r0--, bit
    mov osr, crc                      ; parity bit (shifting it also feeds the LFSR; harmless)
    out pins, 1               [1]
    pin CLK, lo               [3]
    pin CLK, hi               [1]
    pin DAT, hi               [1]     ; stop bit (released)
    pin CLK, lo               [3]
    pin CLK, hi               [4]
    jmp top
