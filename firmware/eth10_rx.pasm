; 10BASE-T receiver (core 0, with the line unit, docs/isa.md §4.7).
; The line unit recovers the Manchester bits: LINECFG = EN | RX_EN | CODE 2
; (0x4005), LINEDIV[31:16] = clocks per bit (5 at 50 MHz, 4 at 40 MHz),
; LINEPINS[12:8] = RXP, the receive comparator's output. A frame starts at its
; first transition and ends when a mid-bit transition is missing (TP_IDL).
;
; Every bit of a frame (preamble, SFD, data, FCS) goes to the RX FIFO, 24 per
; word in [31:8] (low byte 0), the first bit lowest; then the last word (its k
; bits at the top, an empty word if k = 0); then a trailer, count << 16 | 0x80
; (only trailers have a non-zero low byte). The host finds the SFD and checks
; the FCS (test/model/test_eth10.py: rx_bits).
;
; Three instructions per bit (the wrap is free), so core 0 keeps up at 4 clocks
; per bit; the line unit's 32-bit queue absorbs a slow FIFO for a word or so.
; The host must drain at >=13.33 Mbit/s payload (24 line bits per 32-bit word),
; plus SPI headers and status polling. Eight entries hold 19.2 us of line data.
; An overflow (LINESTAT) loses the frame; to recover, disable the line unit,
; restart core 0 with its FIFO cleared, and configure the line unit again.
.program eth10_rx
.shift_in right autopush 24
frame:
    ldi r1, 0                     ; bits in this frame
.wrap_target
bit:
    in line, 1
    jmp t, done                   ; the frame ended
    add r1, 1
.wrap
done:
    push block                    ; the last word
    mov isr, null
    ldi r2, 0x80
    in r2, 16                     ; marker
    in r1, 16                     ; ISR = count << 16 | 0x80, autopushed
    jmp frame
