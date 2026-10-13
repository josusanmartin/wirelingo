; LIN commander (master node): written after the RTL freeze (tag
; rc-93c2137-t48ba), firmware only (docs/post-freeze-protocols.md).
;
; Pins: TX = the transceiver's TXD (1 = recessive), OUT_BASE = TX,
; OUT_COUNT = 1; RX = its RXD (the bus), IN_BASE = RX. TB ticks per bit
; (define, 104): CLKDIV = f_clk / (TB * bit rate) (25.04 at 50 MHz for
; 19200 bit/s: 52.08 us per bit, within LIN's +-0.5 % for a commander).
;
; Frame: break 13 bits dominant + 1 bit delimiter, sync 0x55, PID (the
; parity bits computed here), then either
;   publish:   n data bytes from the host and the checksum computed here, or
;   subscribe: n data bytes + checksum from a responder, received at mid-bit
;              and checked here (a byte must start within BTMO ticks), or
;   n = 0:     the header alone.
; Checksum: classic (data) or enhanced (PID + data), as the host asks: the
; 8-bit sum with end-around carry, inverted.
;
; Host protocol. TX FIFO: command = ID | enhanced << 8 | publish << 9 |
; n << 12 (n = 0..8), then for publish ceil(n / 4) words of data, the first
; byte in [7:0]. RX FIFO: 0x2000 | byte for each byte received (subscribe),
; then one end record 0x3000 | count << 4 | checksum ok | timeout << 1
; | framing error << 2 | published << 3. pe.postfreeze.lin_command,
; lin_records.
;
; r0 = delays / timeouts, r1 = PID, then the checksum sum, r2 = the command
; (n counts down in [15:12]), r3 = byte.
;
; Timing annotations (python -m pe.timing firmware/lin_commander.pasm -D TX=8 -D RX=16 -D TB=104 -D BTMO=2000):
; @assume clkdiv=25.04                             ; 19200 bit/s at 50 MHz
; @deadline break from=brk to=delim min=1352 max=1352 unit=tick "break: 13 bits"
; @deadline delimiter from=delim to=drive min=104 max=104 unit=tick "break delimiter: 1 bit, then the sync start bit"
; @deadline tx_bit from=drive to=drive avoid=tx_byte min=104 max=104 unit=tick reach=conditional "a sent bit: TB ticks"
; @deadline byte_gap from=drive to=drive avoid=top,subscribe,report stall=data:0 min=104 max=150 unit=tick reach=conditional "header and published bytes: any bit, the stop bit and the space after it included, <= 1.5 bits"
; @deadline rx_bit from=sample to=sample avoid=rx_byte min=104 max=104 unit=tick reach=conditional "a received bit: TB ticks"
.program lin_commander
.shift_in right
.shift_out right
top:
    pin TX, hi
    pull block                        ; the command
    mov r2, osr
    out null, 32                      ; OSR empty: the data words follow (publish)
    pin TX, lo                        ; break                     @point brk
    ldi r0, 13 * TB - 3
brk_wait:
    jmp r0--, brk_wait
    pin TX, hi                        ; delimiter                 @point delim
    ldi r0, TB - 7
del_wait:
    jmp r0--, del_wait
    ldi r3, 0x55
    call tx_byte                      ; sync
    ; PID: P0 = ID0^ID1^ID2^ID4 (bit 6), P1 = !(ID1^ID3^ID4^ID5) (bit 7)
    mov r1, r2
    and r1, 63
    mov r3, r1
    mov r0, r1
    shr r0, 1
    xor r3, r0
    shr r0, 1
    xor r3, r0
    shr r0, 2
    xor r3, r0
    and r3, 1
    shl r3, 6
    mov r0, r1
    shr r0, 1
    shr r1, 3
    xor r0, r1
    shr r1, 1
    xor r0, r1
    shr r1, 1
    xor r0, r1
    xor r0, 1
    and r0, 1
    shl r0, 7
    or r3, r0
    mov r1, r2
    and r1, 63
    or r3, r1                         ; PID
    mov r1, r3
    call tx_byte
    ldi r0, 0xF300                    ; the ID is sent: keep n and the flags
    and r2, r0                        ; (ALU immediates have 13 bits)
    mov r0, r2
    shr r0, 12
    or r2, r0                         ; and n in [3:0] too
    mov r0, r2
    and r0, 0x100
    jmp r0, enhanced
    ldi r1, 0                         ; classic: the sum starts at 0
enhanced:                             ; (enhanced: at the PID)
    mov r0, r2
    and r0, 15
    jmp !r0, header_only
    sub r2, 0x1000                    ; n - 1 in [15:12]
    mov r0, r2
    and r0, 0x200
    jmp !r0, subscribe
; ---------------------------------------------------------------- publish
pbyte:
    pull ifempty block                ; (the host stages the data with the command)  @point data
    out r3, 8
    add r1, r3
    cmp r1, 256
    jmp c, pnc
    sub r1, 255                       ; end-around carry
pnc:
    call tx_byte
    sub r2, 0x1000
    jmp !c, pbyte                     ; until n bytes
    mov r3, r1
    xor r3, 0xFF
    and r3, 0xFF
    call tx_byte                      ; checksum
    out null, 32                      ; leftover bytes of the last word
    ldi r3, 0x3008                    ; published
    jmp report
; ---------------------------------------------------------------- subscribe
subscribe:
    ldi r0, BTMO
    call rx_byte
    jmp t, timeout
    jmp r0, ferr
    ldi r0, 0x2000
    or r0, r3
    mov isr, r0
    push block                        ; the byte
    add r1, r3
    cmp r1, 256
    jmp c, snc
    sub r1, 255
snc:
    sub r2, 0x1000
    jmp !c, subscribe                 ; until n bytes
    ldi r0, BTMO
    call rx_byte                      ; the checksum
    jmp t, timeout
    jmp r0, ferr
    add r1, r3
    cmp r1, 256
    jmp c, cnc
    sub r1, 255
cnc:
    ldi r3, 0x3000
    cmp r1, 0xFF
    jmp !z, report
    ldi r3, 0x3001                    ; checksum ok
    jmp report
timeout:
    ldi r3, 0x3002
    jmp report
ferr:
    ldi r3, 0x3004
    jmp report
header_only:
    ldi r3, 0x3000
report:                               ; count = n - 1 - r2[15:12] (mod 16)
    mov r0, r2
    and r0, 15
    sub r0, 1
    mov r1, r2
    shr r1, 12
    sub r0, r1
    and r0, 15
    shl r0, 4
    or r3, r0
    mov isr, r3
    push block                        ; the end record
    jmp top
; ---------------------------------------------------------------- subroutines
rx_byte:                              ; r0 = ticks to wait for the start bit.
    wait fall RX timeout r0           ; T = 1: none. Else r3 = byte,
    jmp t, rx_ret                     ; r0 = 1 on a framing error.
    ldi r0, TB + TB / 2 - 5           ; the middle of bit 0
    ldi r3, 0x80
rx_wait0:
    jmp r0--, rx_wait0
rx_bit:
    in pins, 1                        ; LSB first                 @point sample
    ldi r0, TB - 5
rx_wait:
    jmp r0--, rx_wait
    shr r3, 1
    jmp r3, rx_bit
    in null, 24
    mov r3, isr
    ldi r0, 0
    jmp pin RX, rx_ret                ; stop bit
    ldi r0, 1
rx_ret:
    ret
tx_byte:                              ; send r3[7:0], 8N1, TB ticks per bit
    shl r3, 1
    or r3, 0x600                      ; start 0, stop 1, end marker
tx_bit:
    mov pins, r3                      ; @point drive
    ldi r0, TB - 6
tx_wait:
    jmp r0--, tx_wait
    shr r3, 1
    cmp r3, 1
    jmp !z, tx_bit
    ret
