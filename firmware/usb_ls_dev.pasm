; USB low-speed device on one core (core 0), with the line unit (docs/isa.md §4.7)
; doing NRZI, bit stuffing, bit timing and the EOP in both directions.
; (usb_ls_rx + usb_ls_tx do the same in pure firmware, on two cores.)
;
; Line unit: LINECFG = EN | NRZI | STUFF_K 6 | STUFF_ONES | DIFF | END 1 (SE0, then J)
;   | END_BITS 2 | RX_EN (0x49F3); LINEDIV = 32 << 16 | 32 << 8 (48 MHz, 32 clocks
;   per bit); LINEPINS = D+ for TXP and RXP (D- is the next pin). The bus rests
;   at J through the device's D- pull-up while the unit doesn't drive it.
; CRCCTRL: poly 0x8005, 16 bits, IN_TAP (DATA packets are read bit by bit
; through the CRC tap; a good packet leaves the residue 0x800D).
;
; Host protocol (pe.usbdev, the same as the two-core firmware):
;   * tokens are filtered on address, endpoint and CRC5 against the immediate at
;     `filter` (pe.usb.token_filter; the host rewrites it after SET_ADDRESS);
;   * SETUP/OUT set F_EXP (and SETUP F_SETUP); the following DATA packet gives
;     a record: payload + CRC16 bits 24 per word in [31:8], the last word, and a
;     trailer [31:16] = bits including SYNC and PID, [15:8] = reversed PID,
;     [7] = 1, [1] = SETUP association, [0] = CRC/PID good.
;     A good one is ACKed;
;   * an IN gets the staged packet (F_STAGED; TX FIFO: bit count, then SYNC,
;     PID, payload and CRC16 bytes, little-endian) or a NAK; a SETUP drops a
;     packet still staged (the host software must not stage during one);
;   * the host's ACK of our DATA sets F_GOTACK.
; Our own transmissions come back through the receiver and are dropped.
; Registers: r0 = bit/byte count, r1 = token tail, r2 = scratch, r3 = SYNC+PID.
.program usb_ls_dev
.shift_in right autopush 24
.shift_out right autopull 32
idle:
    mov isr, null
    ldi r2, 120
    wait 1 pin DM timeout r2
    jmp t, bus_reset
    wait fall DM
    jmp !pin DP, idle              ; J -> SE0: reset, rather than packet SYNC
    in line, 8                     ; SYNC
    jmp t, idle
    in line, 8                     ; PID
    jmp t, idle
    mov r3, ::isr                  ; 0x100 | rev8(PID) when SYNC is right
    mov isr, null
    cmp r3, 0x14B                  ; SYNC + ACK: the host took our DATA
    jmp z, got_ack
    mov r2, r3
    and r2, 0x40                   ; 0: a token (PID ends in binary 01)
    jmp !r2, token
    jmp !f F_EXP, skip             ; DATA we don't expect, or a handshake
; ---------------------------------------------------------------- DATA after SETUP/OUT
    mov crc, ~null                 ; CRC16 over what follows the PID
    ldi r0, 16                     ; bits so far: SYNC + PID
dbit:
    in line, 1                     ; through the CRC tap
    jmp t, dend
    add r0, 1
    jmp dbit
dend:
    flag clr F_EXP
    cmp r3, 0x1C3                  ; exact SYNC + DATA0, PID complement included
    jmp z, data_pid_ok
    jmp f F_SETUP, data_bad_pid
    cmp r3, 0x1D2                  ; exact SYNC + DATA1
    jmp z, data_pid_ok
data_bad_pid:
    ldi r1, 0x80
    jmp trailer
data_pid_ok:
    ldi r2, 0x800D
    mov r1, crc
    cmp r1, r2
    ldi r1, 0x80                   ; trailer status: CRC error (no handshake: the host retries)
    jmp !z, trailer
    ldi r1, 0x81                   ; trailer status: good
    wait 1 pin DM                  ; queue the ACK first, so a full RX FIFO
    nop                       [31] ; can't make it late
    nop                       [31]
    ldi r2, 0xD280                 ; SYNC + ACK
    mov osr, r2
    out line, 16
trailer:
    jmp !f F_SETUP, trailer_out
    or r1, 2
    flag clr F_SETUP
trailer_out:
    push block                     ; the last word (bits at the top)
    mov isr, null
    mov r2, r3
    and r2, 0xFF
    shl r2, 8
    or r1, r2                      ; trailer [15:8] = reversed PID
    in r1, 16
    in r0, 16                      ; [31:16] = bits, [15:0] = status: autopushed
    and r1, 1
    cmp r1, 1
    jmp z, echo                    ; our ACK comes back through the receiver
    jmp idle
; ---------------------------------------------------------------- tokens
token:
    in line, 16                    ; address, endpoint, CRC5
    jmp t, idle                    ; too short
    mov r1, ::isr
    mov isr, null
    in line, 1                     ; the frame must end here
    jmp t, tok_end
    jmp skip
tok_end:
filter:
    ldi r2, TOKEN_FILTER           ; written by the host software
    cmp r1, r2
    jmp !z, idle                   ; another address or endpoint, or a bad CRC5
    cmp r3, 0x196                  ; IN
    jmp z, got_in
    cmp r3, 0x1B4                  ; SETUP
    jmp z, got_setup
    cmp r3, 0x187                  ; OUT
    jmp !z, idle                   ; another token
    flag clr F_SETUP
    flag set F_EXP
    jmp idle
got_setup:                         ; a SETUP aborts the last transfer:
    flag clr F_GOTACK
    flag clr F_STAGED              ; drop a packet still staged for it
flush:
    jmp txne, drop
    flag set F_SETUP
    flag set F_EXP
    jmp idle
drop:
    pull block
    jmp flush
got_in:
    jmp f F_STAGED, send_data
    ldi r3, 0x5A80                 ; SYNC + NAK
; ---------------------------------------------------------------- responses
send16:
    wait 1 pin DM                  ; the host's EOP is over (J)
    nop                       [31]
    nop                       [31] ; about two bit times of turnaround
    mov osr, r3
    out line, 16
    jmp echo
send_data:
    flag clr F_STAGED
    pull block                     ; bit count
    mov r0, osr
    shr r0, 3                      ; bytes
    sub r0, 1
    out null, 32                   ; empty the OSR: the bytes autopull
    wait 1 pin DM
    nop                       [31]
    nop                       [31]
sbyte:
    out line, 8
    jmp r0--, sbyte
echo:                              ; our own packet comes back: drop it
    in line, 16
    jmp t, idle
    mov isr, null
    jmp echo
got_ack:
    flag set F_GOTACK
skip:                              ; the rest of an unwanted packet
    in line, 16
    jmp t, idle
    mov isr, null
    jmp skip
bus_reset:
    jmp pin DP, idle              ; timeout in K is not SE0
    flag set F_RESET
    wait 1 pin DM
    wait 0 flag F_RESET
    jmp idle
