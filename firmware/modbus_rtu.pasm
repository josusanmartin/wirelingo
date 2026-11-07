; Modbus RTU client (master) framing: written after the RTL freeze (tag
; rc-01ce16d-t48ba), firmware only (docs/post-freeze-protocols.md).
; Modbus over Serial Line V1.02 (modbus.org): RTU mode, 11-bit characters
; (start, 8 data bits LSB first, even parity, stop), CRC-16 (polynomial
; 0x8005 reflected, preset 0xFFFF) appended low byte first, frames
; delimited by silence: at least t3.5 between frames, at most t1.5 between
; the characters of a frame.
;
; Pins: TX = the transceiver's DI (OUT_BASE = TX, OUT_COUNT = 1), DE = its
; driver enable (high while we send), RX = its RO (IN_BASE = RX; the bus
; level, our own echo included). TB ticks per bit (define, 104):
; CLKDIV = f_clk / (TB * baud), 25.04 at 50 MHz for 19200 baud. T15 and T35
; (defines, ticks): 1.5 and 3.5 character times (16.5 and 38.5 bits) up to
; 19200 baud, 750 and 1750 us above (the specification's fixed values).
; PAR: 0 even parity (the default the specification requires), 1 odd.
;
; Request: waits until the line has been idle for t3.5, enables the driver,
; sends n bytes from the host and the CRC computed here by the CRC tap (the
; host sets CRCCTRL: POLY 0x8005, width 16, IN_TAP and OUT_TAP; every
; `out pins, 1` and `in r, 1` of a data bit feeds it, in wire order, so the
; register holds the bit-reversed CRC: sent with `mov osr, ::crc`), the
; parity bit computed here, then releases the driver after the last stop bit.
; Response (if asked): waits RTMO * 4099 ticks for a start bit, then
; receives characters (each start bit re-synchronises, every bit sampled at
; mid-bit, parity checked) until the line has been idle for t3.5 after a
; stop bit. A character that starts after t1.5 but before t3.5 marks the
; frame as broken (the specification says such a frame is discarded) and is
; kept. The CRC tap is preset before the response and fed its data bits;
; over a correct frame and its CRC it ends at 0.
;
; Host protocol. TX FIFO: header n | expect << 8 (n = 1..255 bytes without
; the CRC), then ceil(n / 4) words, the first byte in [7:0]. Without expect
; (a broadcast) nothing comes back. RX FIFO: a data word per 3 received
; bytes, [23:0] the bytes (first in [7:0]), [31:24] = 3; then one end word:
; [8k-1:0] the k = 0..2 bytes left, [31:24] = 0x80 | k | parity error << 2
; | framing error << 3 | t1.5 gap << 4 | CRC ok << 5 | overflow << 6
; (the RX FIFO was full: a data word was lost). No bytes at all: no
; response. pe.postfreeze2.modbus_words, modbus_response.
;
; r0 = bit counter / timeout loops, r1 = parity / count, r2 = scratch /
; status, r3 = byte count and expect flag / timeouts / late flag. While
; receiving, the status byte (k, flags, 0x80) lives in the OSR.
;
; Timing annotations (python -m pe.timing firmware/modbus_rtu.pasm -D TX=8 -D DE=9 -D RX=16 -D TB=104 -D T15=1716 -D T35=4004 -D RTMO=50 -D PAR=0):
; @assume clkdiv=25.04                             ; 19200 baud at 50 MHz
; @deadline tx_bit from=bit to=bit avoid=stop,txend min=104 max=104 unit=tick "start, data and parity bits: TB ticks"
; @deadline tx_stop from=stop to=bit avoid=txend,top stall=more:0 min=104 max=114 unit=tick "the stop bit and the space after it, inside a request (host keeping up)"
; @deadline tx_last from=stop to=txend avoid=bit min=104 max=104 unit=tick reach=conditional "the last stop bit is complete when the driver is released"
; @deadline rx_mid from=sw to=sample avoid=gap15,first_more,frame_end min=52 max=52 unit=tick reach=conditional "start bit seen -> its middle: TB / 2"
; @deadline rx_bit from=sample to=sample avoid=gap,glitch min=104 max=104 unit=tick reach=conditional "every received bit: TB ticks after the last sample"
; @deadline rx_turn from=stop_s to=sw.enter avoid=ferr min=1 max=40 unit=tick "stop bit's middle -> watching for the next start bit: < TB / 2"
.program modbus_rtu
.shift_out right
.shift_in right
.out_pins TX 1
.in_pins RX
top:
    pull block                        ; the header
    mov r3, osr                       ; n | expect << 8
    out null, 32                      ; OSR empty: the data words follow
idle:
    ldi r2, T35
    wait 0 pin RX timeout r2          ; t3.5 of silence before a request
    jmp t, go
    wait 1 pin RX
    jmp idle
go:
    mov crc, ~null                    ; CRC preset 0xFFFF
    pin DE, hi                        ; driver on (TX is high: idle)
more:
    pull ifempty block                ; the next 4 bytes
    call tx_byte
    sub r3, 1
    mov r2, r3
    and r2, 255
    jmp r2, more
    mov osr, ::crc                    ; the CRC, bit-reversed: low byte first, LSB first
    out null, 16
    call tx_byte
    call tx_byte
    pin DE, lo                        ; driver off after the last stop bit   @point txend
    and r3, 256
    jmp !r3, top                      ; a broadcast: no response
; ---------------------------------------------------------------- response
    ldi r2, 0x80                      ; status: the end-word marker, k = 0, no flags
    mov osr, r2
    mov crc, ~null
    ldi r0, RTMO
first:
    ldi r3, 4095
    wait 0 pin RX timeout r3          ; the response's first start bit   @point sw
    jmp t, first_more
    jmp rx_first
first_more:
    jmp r0--, first
    jmp frame_end                     ; no response
gap:                                  ; the stop bit's middle was 14..23 ticks ago (checker)
    ldi r3, T15 - TB / 2 - 16
    wait 0 pin RX timeout r3          ; the next start bit within t1.5?   @point sw
    jmp t, gap15
rx_byte:
    ldi r3, 0                 [TB / 2 - 3]
rx_mid:
    jmp pin RX, glitch                ; the start bit's middle: still low?   @point sample
    ldi r1, PAR               [TB - 3]
    ldi r0, 7
rbit:
    mov r2, pins                      ; a data bit, LSB first               @point sample
    in r2, 1                          ; (feeds the CRC tap)
    xor r1, r2                [TB - 4]
    jmp r0--, rbit
    mov r2, pins                      ; the parity bit                      @point sample
    xor r1, r2
    and r1, 1                 [TB - 3]
    mov r0, pins                      ; the stop bit                        @point sample @point stop_s
    mov r2, osr
    or r2, r3                         ; t1.5 gap flag
    jmp !r1, par_ok
    or r2, 4                          ; parity error
par_ok:
    and r0, 1
    jmp r0, stop_ok
    or r2, 8                          ; framing error
stop_ok:
    add r2, 1                         ; k + 1
    mov r0, r2
    and r0, 3
    cmp r0, 3
    jmp !z, keep
    sub r2, 3                         ; 3 bytes: a data word
    ldi r0, 3
    in r0, 8
    jmp rxf, overflow
pushw:
    push noblock                      ; (dropped if the FIFO is full)
keep:
    mov osr, r2
    jmp pin RX, gap
ferr:                                 ; a low stop bit: wait for the line to go high
    ldi r3, T35
    wait 1 pin RX timeout r3
    jmp gap
overflow:
    or r2, 0x40
    jmp pushw
glitch:                               ; the start bit did not last: noise
    mov r2, osr
    or r2, 8
    mov osr, r2
    jmp gap
gap15:                                ; t1.5 of silence: is it the end?
    ldi r3, T35 - T15
    wait 0 pin RX timeout r3          ; @point sw
    jmp t, frame_end
    ldi r3, 0x10              [TB / 2 - 4]   ; a character after t1.5: the frame is broken
    jmp rx_mid
rx_first:
    ldi r3, 0                 [TB / 2 - 5]
    jmp rx_mid
frame_end:                            ; t3.5 of silence (or no response)
    mov r2, osr
    mov r0, crc
    jmp r0, crc_bad
    or r2, 0x20                       ; CRC ok
crc_bad:
    mov r0, r2
    and r0, 3
    jmp !r0, k0
    sub r0, 1
    jmp !r0, k1
    in null, 8                        ; k = 2
    jmp tag
k1:
    in null, 16
    jmp tag
k0:
    in null, 24
tag:
    in r2, 8
    push block                        ; the end word
    jmp top
; ---------------------------------------------------------------- subroutine
tx_byte:                              ; OSR[7:0]: start, 8 data bits, parity, stop
    pin TX, lo                        ; start bit                           @point bit
    mov r1, osr
    and r1, 255
    mov r2, r1
    shr r2, 4
    xor r1, r2
    mov r2, r1
    shr r2, 2
    xor r1, r2
    mov r2, r1
    shr r2, 1
    xor r1, r2
    xor r1, PAR                       ; bit 0: the parity bit
    ldi r0, 7                 [TB - 14]
txb:
    out pins, 1               [TB - 2]  ; LSB first (feeds the CRC tap)    @point bit
    jmp r0--, txb
    mov pins, r1              [TB - 1]  ; parity                            @point bit
    pin TX, hi                [TB - 2]  ; stop bit                          @point stop
    ret
