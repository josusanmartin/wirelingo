; USB low-speed transmitter at clk = 48 MHz, CLKDIV = 1 (32 ticks per bit).
; Does NRZI encoding, bit stuffing, SYNC and EOP in firmware.
; OUT pins: D+ (base) and D- (count 2): value 2 = J, 1 = K, 0 = SE0.
;
; Triggers (global flags, set by usb_ls_rx or the host):
;   F_IN  : an IN token arrived -> send the staged packet, or NAK if none
;   F_ACK : a DATA packet after SETUP/OUT arrived -> send ACK
;   F_SETUP: a SETUP is arriving (set by usb_ls_rx from its token to the end of its
;           DATA) -> empty the TX FIFO: the transfer it aborts may have words staged
;           or being pushed, which nothing else would drop before the next IN;
;           and drop an IN token that came inside the SETUP (F_IN would stay set,
;           and that IN be answered after the SETUP's ACK, unasked)
; An IN that finds F_STAGED over an empty TX FIFO (a stage whose words that flush
; dropped) is NAKed, never taken as a packet.
; Staging: the host pushes the packet into the TX FIFO, then sets F_STAGED (so a
; packet is never sent half-pushed). The packet is: word 0 = number of bits
; (including the SYNC byte), then the bytes packed little-endian, 4 per word
; (at most 3 data words: SYNC + PID + 8 data bytes + CRC16). F_STAGED is
; cleared when the packet is taken and F_SENT set; usb_ls_rx then sets F_GOTACK
; when the host acknowledges it, so the host software can re-stage it if that
; never comes.
;
; Each data bit takes exactly 32 ticks; a transition always lands 4 ticks into
; its bit cell, whichever path is taken (see the tick comments).
; Registers: r0 = bits left - 1, r1 = consecutive 1s, r2 = line state, r3 = bit.
.program usb_ls_tx
.shift_out right autopull 32
idle:
    jmp f F_RESET, reset_wait
    jmp f F_ACK, send_ack
    jmp f F_SETUP, setup_flush     ; a SETUP aborts the transfer (usb_ls_rx clears F_STAGED)
    jmp !f F_IN, idle
    flag clr F_IN
    jmp !f F_STAGED, send_nak
    jmp txne, staged               ; F_STAGED over an empty TX FIFO (flushed below): NAK
send_nak:
    ldi r3, 0x5A80                 ; SYNC + NAK PID
    jmp const16
staged:
    flag clr F_STAGED
    flag set F_SENT
    pull block                     ; header: bit count
    mov r0, osr
    out null, 32                   ; empty the OSR: the data words autopull
    jmp go
send_ack:
    flag clr F_ACK
    ldi r3, 0xD280                 ; SYNC + ACK PID
const16:
    mov osr, r3                    ; 16 bits from the OSR (no autopull: only 16 used)
    ldi r0, 16
go:
    sub r0, 1                      ; loop count - 1
    wait 1 pin DM                  ; host's EOP over (J)
    ldi r1, 0                 [31]
    ldi r2, 2                 [31] ; bus turnaround: ~2 bit times of J
    mov pins, r2
    mov pindirs, ~null             ; drive D+/D-
bit:
    out r3, 1                      ; T+0
    jmp !r3, zero                  ; T+1
    add r1, 1                      ; T+2  a 1: no transition
    cmp r1, 6                      ; T+3
    jmp z, stuff                   ; T+4
    jmp r0--, bit             [26] ; T+5  next bit at T+32
    jmp eop
zero:
    xor r2, 3                      ; T+2  J <-> K
    ldi r1, 0                      ; T+3
    mov pins, r2                   ; T+4  transition
    jmp r0--, bit             [26] ; T+5
    jmp eop
stuff:                             ; after six 1s: a stuffed 0 in the next cell
    xor r2, 3                      ; T+5
    ldi r1, 0                 [29] ; T+6
    mov pins, r2                   ; T+36 transition
    jmp r0--, bit             [26] ; T+37 next bit at T+64
    jmp eop
eop:                               ; every path arrives at B+1 (B = cell boundary)
    ldi r2, 0                 [2]  ; B+1
    mov pins, r2              [62] ; B+4  SE0 (2 bit times)
    ldi r2, 2                      ; B+67
    mov pins, r2              [31] ; B+68 J (1 bit time)
    mov pindirs, null              ; release the bus
    jmp idle
reset_wait:
    mov pindirs, null
    wait 0 flag F_RESET
    jmp idle
setup_flush:                       ; while F_SETUP (its token to the end of its DATA), words
    flag clr F_IN                  ; the host software pushed for the abandoned transfer,
    jmp txne, setup_drop           ; before or as the SETUP came, are dropped, and so is an
    jmp idle                       ; IN token inside it: answered after the SETUP's ACK, unasked
setup_drop:
    pull block
    jmp setup_flush
