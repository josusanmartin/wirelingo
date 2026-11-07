; SMBus host (master) with Packet Error Checking: written after the RTL
; freeze (tag rc-01ce16d-t48ba), firmware only (docs/post-freeze-protocols.md).
; System Management Bus Specification 3.2 (sbs-forum.org): the I2C-level
; transactions of every SMBus protocol (quick command, send/receive byte,
; write/read byte and word, block write/read, process calls), PEC, the
; clock-low timeout.
;
; Pins: SDA and SCL, uio pins in open-drain mode (OD_MASK) with pull-ups;
; OUT_BASE = IN_BASE = SDA (OUT_COUNT 1). The PEC is the CRC tap: the host
; sets CRCCTRL to POLY 0x07, width 8, IN_TAP and OUT_TAP; every data bit
; goes through `out r1, 1` (sending) or `in pins, 1` (receiving), MSB first
; as on the wire, so the register is CRC-8 (x^8 + x^2 + x + 1, preset 0)
; over every byte of the transaction, the address bytes included; ACK bits
; are driven and read with `mov`, outside it. A received PEC is right when
; the register is 0 after it.
;
; Clock: 9..10 ticks per SCL period (low 5, high 4..5 ticks without
; stretching); CLKDIV 56 at 50 MHz: 1 tick = 1.12 us, SCL 89..99 kHz
; (SMBus: 10..100 kHz, tLOW >= 4.7 us, tHIGH >= 4.0 us). Every SCL release
; waits for SCL to go high (clock stretching) for at most TTO ticks (define:
; 25 ms, the SMBus T_TIMEOUT,MIN); longer, the transaction is abandoned with
; both lines released (the devices reset at T_TIMEOUT). Each bit we send as
; a 1 is checked at SCL high: a 0 there is a lost arbitration (another
; master), and we stop driving.
;
; Host protocol. TX FIFO: a header word [31:24] nr, [23:16] nw, [14:8] the
; 7-bit address, [7:0] flags: [0] PEC, [1] block read (the first byte read
; is the count of those that follow; nr is then only "read"), [3] the R/W
; bit of a quick command (nw = nr = 0); then nw words, one byte each in
; [31:24]. The transaction: START, address + W, the nw bytes (the command
; code first), then if nr: repeated START (or START, if nw = 0), address +
; R, the bytes read; the PEC follows the last byte either way (sent, or
; read and checked). Every read byte is acknowledged but the last, which
; is not (the PEC, if there is one). RX FIFO: a word per byte read, 1 << 16
; | byte (the count of a block and the PEC included), then an end word
; code << 8 | bytes not written: code 0x80 done, 0x81 done with a correct
; PEC read, 0x82 PEC wrong, 0x83 address not acknowledged, 0x84 data byte
; not acknowledged, 0x85 SCL held low (timeout), 0x86 arbitration lost,
; 0x87 the bus not idle at the start (for TTO). A full RX FIFO drops the
; word and sets flag FOV. pe.postfreeze3.smbus_words, smbus_records.
;
; r0 = bit counter / scratch, r1 = the bit / ACK / the end code, r2 = nr <<
; 8 | bytes left to write (in the read phase: bytes left to read << 8), r3
; = timeouts. The ISR keeps the header until the read phase, then collects
; the bytes; the OSR keeps the flags in the read phase.
;
; Timing annotations (python -m pe.timing firmware/smbus_host.pasm -D SDA=0 -D SCL=1 -D TTO=22321 -D FOV=7):
; @assume clkdiv=56                                ; 50 MHz: 1 tick = 1.12 us
; @deadline t_low from=scl_lo to=scl_hi avoid=stop,lost,tmo stall=wpull:0 min=5 max=22 unit=tick "SCL low inside a transaction: 5 ticks (5.6 us) inside a byte, up to 22 between bytes"
; @deadline t_high from=scl_hi to=scl_lo avoid=tmo,lost,stop stall=scl_wait:0 min=4 max=10 unit=tick reach=conditional "SCL high without stretching: 4..5 ticks (4.5..5.6 us), 10 around a repeated START"
; @deadline t_high_s from=scl_hi to=scl_lo avoid=tmo,lost,stop min=4 max=22331 unit=tick reach=conditional "SCL high, stretched up to the timeout"
; @deadline t_hd_dat from=scl_lo to=sda avoid=scl_hi min=1 unit=tick reach=conditional "SDA changes after SCL is low (hold time)"
; @deadline t_low_us from=scl_lo to=scl_hi avoid=stop,lost,tmo stall=wpull:0 min=235 "tLOW >= 4.7 us (235 clocks)"
; @deadline t_high_us from=scl_hi to=scl_lo avoid=tmo,lost,stop min=200 reach=conditional "tHIGH >= 4.0 us (200 clocks)"
; @deadline t_buf from=stop to=start min=5 unit=tick "bus free between STOP and START >= 4.7 us"
; @deadline t_hd_sta from=start to=scl_lo min=4 max=4 unit=tick "START hold time >= 4.0 us"
; @deadline t_su_sto from=sto_hi to=stop avoid=tmo stall=scl_wait:0 min=6 max=6 unit=tick "STOP setup time >= 4.0 us"
; @deadline timeout from=scl_hi to=tmo avoid=scl_lo min=22322 max=22325 unit=tick reach=conditional "SCL held low: given up after 25 ms"
.program smbus_host
.shift_out left
.shift_in left
.out_pins SDA 1
.in_pins SDA
top:
    pull block                        ; a transaction
    mov isr, osr                      ; the header (address, flags in [15:0])
    out r2, 16                        ; nr << 8 | nw
    pin SDA, hi
    pin SCL, hi
    ldi r3, TTO
    wait 1 pin SCL timeout r3         ; the bus idle?
    jmp t, busy
    ldi r3, TTO
    wait 1 pin SDA timeout r3
    jmp t, busy
    mov crc, null             [4]     ; PEC preset 0; bus free time   @point free
    pin SDA, lo               [3]     ; START   @point start @point sda
    pin SCL, lo                       ; @point scl_lo
    mov r1, isr
    shr r1, 7
    and r1, 0xFE                      ; address << 1 | W
    mov r0, r2
    and r0, 255
    jmp r0, wphase                    ; bytes to write
    mov r0, r2
    shr r0, 8
    jmp r0, rphase0                   ; read only
    mov r0, isr                       ; a quick command
    and r0, 8
    shr r0, 3
    or r1, r0                         ; its R/W bit
    mov osr, r1
    out null, 24
    call wbyte
    jmp r1, nack_addr
    jmp done
wphase:
    mov osr, r1
    out null, 24
    call wbyte                        ; address + W
    jmp r1, nack_addr
wloop:
    pull block                        ; the next byte, in [31:24]   @point wpull
    sub r2, 1
    call wbyte
    jmp r1, nack_data
    mov r0, r2
    and r0, 255
    jmp r0, wloop
    mov r0, r2
    shr r0, 8
    jmp r0, rphase                    ; then read
    mov r0, isr
    and r0, 1
    jmp !r0, done                     ; no PEC
    mov r1, crc
    mov osr, r1
    out null, 24
    call wbyte                        ; the PEC
    jmp r1, nack_data
    jmp done
; ---------------------------------------------------------------- read phase
rphase:                               ; repeated START (SCL low)
    pin SDA, hi               [3]     ; @point sda
    ldi r3, TTO
    pin SCL, hi                       ; @point scl_hi
    wait 1 pin SCL timeout r3         ; @point scl_wait
    jmp t, tmo
    nop                       [2]
    pin SDA, lo               [3]     ; repeated START: setup >= 4.7 us, hold >= 4.0 us
    pin SCL, lo                       ; @point scl_lo
rphase0:
    mov r1, isr
    shr r1, 7
    and r1, 0xFE
    or r1, 1                          ; address << 1 | R
    mov osr, r1
    out null, 24
    call wbyte
    jmp r1, nack_addr
    mov r0, isr
    and r0, 255
    mov osr, r0                       ; the flags, from here on
    shr r2, 8
    shl r2, 8                         ; nr << 8: bytes to read
    and r0, 2
    jmp !r0, rloop
    call rbyte                        ; a block: the count first
    mov r0, isr
    and r0, 255
    shl r0, 8
    mov r2, r0                        ; count << 8
    jmp rdec
rloop:
    call rbyte
    sub r2, 0x100
rdec:                                 ; the last byte (no more, no PEC) gets no ACK
    mov r0, r2
    shr r0, 8
    jmp r0, rack
    mov r0, osr
    and r0, 1
    jmp r0, rack
    ldi r1, 1
    call sack
    call put
    ldi r1, 0x8000
    jmp stop
rack:
    ldi r1, 0
    call sack
    call put
    mov r0, r2
    shr r0, 8
    jmp r0, rloop
    call rbyte                        ; the PEC
    ldi r1, 1
    call sack
    call put
    mov r0, crc                       ; 0 after the bytes and their PEC
    ldi r1, 0x8100
    jmp !r0, stop
    ldi r1, 0x8200
    jmp stop
; ---------------------------------------------------------------- endings
nack_addr:
    ldi r1, 0x8300
    jmp stop
nack_data:
    ldi r1, 0x8400
    jmp stop
done:
    ldi r1, 0x8000
stop:                                 ; SCL low: SDA low, SCL high, SDA high
    pin SDA, lo               [3]     ; @point sda
    ldi r3, TTO
    pin SCL, hi                       ; @point scl_hi @point sto_hi
    wait 1 pin SCL timeout r3         ; @point scl_wait
    jmp t, tmo
    nop                       [2]
    pin SDA, hi                       ; STOP   @point stop
report:                               ; r1 = code << 8
    mov r0, r2
    and r0, 255                       ; bytes not written
    or r1, r0
    mov isr, r1
    call put
drain:                                ; the words of bytes not written
    jmp !r0, top
    pull noblock
    sub r0, 1
    jmp drain
tmo:                                  ; SCL low for TTO ticks
    pin SDA, hi
    ldi r1, 0x8500
    jmp report
lost:                                 ; arbitration lost: stop driving
    pin SDA, hi
    pin SCL, hi
    ldi r1, 0x8600
    jmp report
busy:
    ldi r1, 0x8700
    jmp report
; ---------------------------------------------------------------- subroutines
wbyte:                                ; send OSR[31:24], SCL low; r1 = the ACK bit read (0 = ACK)
    ldi r0, 7
wbit:
    out r1, 1                         ; (feeds the PEC)
    mov pins, r1                      ; @point sda
    ldi r3, TTO
    pin SCL, hi                       ; @point scl_hi
    wait 1 pin SCL timeout r3         ; @point scl_wait
    jmp t, tmo
    jmp pin SDA, wb_ok
    jmp r1, lost                      ; we sent a 1 and the line is 0
wb_ok:
    pin SCL, lo                       ; @point scl_lo
    jmp r0--, wbit
    pin SDA, hi               [1]     ; release SDA for the ACK   @point sda
    ldi r3, TTO
    pin SCL, hi                       ; @point scl_hi
    wait 1 pin SCL timeout r3         ; @point scl_wait
    jmp t, tmo
    mov r1, pins                      ; the ACK bit
    pin SCL, lo               [1]     ; @point scl_lo
    and r1, 1
    ret
rbyte:                                ; receive a byte into the ISR (1 << 16 | byte), SCL low
    mov isr, null
    ldi r0, 0x100
    in r0, 24
    ldi r0, 7
rbit:
    ldi r3, TTO               [2]
    pin SCL, hi                       ; @point scl_hi
    wait 1 pin SCL timeout r3         ; @point scl_wait
    jmp t, tmo
    in pins, 1                        ; MSB first (feeds the PEC)
    pin SCL, lo                       ; @point scl_lo
    jmp r0--, rbit
    ret
sack:                                 ; send r1 as the ACK bit (0 ACK, 1 NACK)
    mov pins, r1              [1]     ; @point sda
    ldi r3, TTO
    pin SCL, hi                       ; @point scl_hi
    wait 1 pin SCL timeout r3         ; @point scl_wait
    jmp t, tmo
    nop                       [1]
    pin SCL, lo                       ; @point scl_lo
    pin SDA, hi                       ; @point sda
    ret
put:                                  ; push the ISR; a full FIFO drops it
    jmp rxf, full
    push noblock
    ret
full:
    flag set FOV
    push noblock
    ret
