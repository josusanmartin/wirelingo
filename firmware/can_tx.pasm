; CAN 2.0A transmitter: bit stuffing, CRC15 from the CRC tap, arbitration, ACK check.
; 48 ticks per bit (CLKDIV = f_clk / (48 * bitrate): 2 at 48 MHz for 500 kbit/s).
; TX = OUT pin (TXD, 1 = recessive); RX = input pin (RXD) sampled 25 ticks into each bit.
; CRCCTRL = 0x4599 | 14<<16 | OUT_TAP: the SOF..data bits shifted out feed CRC15.
;
; TX FIFO per frame: word 0 = n = number of bits SOF..last data bit (19 + 8*DLC),
; then those bits MSB first, 32 per word.
; RX FIFO: one status word per frame: 0 = sent and ACKed, 1 = no ACK,
; 2 = lost arbitration (or a bit error).
;
; Every bit period is exactly 48 ticks. T = tick of a period's first instruction;
; the bit is driven at T+1 and the bus is sampled at T+26.
; r0 = bits left - 1 (data, then CRC), r1 = run length, r2 = last bit, r3 = bit.
.program can_tx
.shift_out left autopull 32
idle:
    pin TX, hi
    pull block
    mov r0, osr
    out null, 32                      ; empty the OSR: the frame bits autopull
    sub r0, 1
bus_idle:                             ; 11 recessive bits before a SOF
    wait 1 pin RX
    ldi r2, 527                       ; 11 x 48 - 1 ticks
    wait 0 pin RX timeout r2
    jmp t, start                      ; timed out: the bus has been idle long enough
    jmp bus_idle                      ; somebody else started
start:
    mov crc, null
    ldi r1, 0
    ldi r2, 2                         ; no previous bit
; ---------------------------------------------------------------- data bits
dbit:
    out r3, 1                         ; T+0   next bit (feeds CRC15)
    mov pins, r3                      ; T+1   drive
    cmp r3, r2                        ; T+2
    jmp z, d_same                     ; T+3
    ldi r1, 1                         ; T+4
    mov r2, r3                  [19]  ; T+5..24
    jmp d_samp                        ; T+25
d_same:
    add r1, 1                         ; T+4
    nop                         [20]  ; T+5..25
d_samp:
    jmp pin RX, d_ok                  ; T+26  sample
    jmp r3, lost                      ; T+27  sent recessive, read dominant
    jmp d_tail                        ; T+28
d_ok:
    nop                         [1]   ; T+27..28
d_tail:
    cmp r1, 5                         ; T+29
    jmp !r0, d_last                   ; T+30
    nop                         [13]  ; T+31..44
    jmp z, d_stuff                    ; T+45
    nop                               ; T+46
    jmp r0--, dbit                    ; T+47
d_last:                               ; the last data bit: load the CRC now
    mov osr, crc                      ; T+31  CRC15 in OSR[14:0]
    out null, 17                      ; T+32  now at OSR[31:17], MSB first
    ldi r0, 14                  [11]  ; T+33..44
    jmp z, dl_stuff                   ; T+45
    nop                               ; T+46
    jmp cbit                          ; T+47
; a stuff bit: entered at T+46 of the preceding bit; its own period starts at T'
d_stuff:
    xor r3, 1                         ; T+46
    nop                         [1]   ; T+47, T'+0
    mov pins, r3                      ; T'+1
    mov r2, r3                        ; T'+2
    ldi r1, 1                   [22]  ; T'+3..25
    jmp pin RX, ds_ok                 ; T'+26
    jmp r3, lost                      ; T'+27
    jmp ds_tail                       ; T'+28
ds_ok:
    nop                         [1]   ; T'+27..28
ds_tail:
    nop                         [17]  ; T'+29..46
    jmp r0--, dbit                    ; T'+47 (accounts for the data bit before it)
dl_stuff:                             ; stuff bit after the last data bit
    xor r3, 1
    nop                         [1]
    mov pins, r3
    mov r2, r3
    ldi r1, 1                   [22]
    jmp pin RX, dls_ok
    jmp r3, lost
    jmp dls_tail
dls_ok:
    nop                         [1]
dls_tail:
    nop                         [17]
    jmp cbit
; ---------------------------------------------------------------- CRC bits (also stuffed)
cbit:
    out r3, 1                         ; T+0
    mov pins, r3                      ; T+1
    cmp r3, r2                        ; T+2
    jmp z, c_same                     ; T+3
    ldi r1, 1                         ; T+4
    mov r2, r3                  [19]  ; T+5..24
    jmp c_samp                        ; T+25
c_same:
    add r1, 1
    nop                         [20]
c_samp:
    jmp pin RX, c_ok                  ; T+26
    jmp r3, lost                      ; T+27
    jmp c_tail                        ; T+28
c_ok:
    nop                         [1]
c_tail:
    cmp r1, 5                         ; T+29
    jmp !r0, c_last                   ; T+30
    nop                         [13]  ; T+31..44
    jmp z, c_stuff                    ; T+45
    nop                               ; T+46
    jmp r0--, cbit                    ; T+47
c_last:
    nop                         [13]  ; T+31..44
    jmp z, cl_stuff                   ; T+45
    nop                               ; T+46
    jmp delim                         ; T+47
c_stuff:
    xor r3, 1
    nop                         [1]
    mov pins, r3
    mov r2, r3
    ldi r1, 1                   [22]
    jmp pin RX, cs_ok
    jmp r3, lost
    jmp cs_tail
cs_ok:
    nop                         [1]
cs_tail:
    nop                         [17]
    jmp r0--, cbit
cl_stuff:
    xor r3, 1
    nop                         [1]
    mov pins, r3
    mov r2, r3
    ldi r1, 1                   [22]
    jmp pin RX, cls_ok
    jmp r3, lost
    jmp cls_tail
cls_ok:
    nop                         [1]
cls_tail:
    nop                         [17]
    jmp delim
; ---------------------------------------------------------------- delimiter, ACK, EOF
delim:
    nop                               ; T+0
    pin TX, hi                  [46]  ; T+1..47  CRC delimiter (recessive)
    nop                         [25]  ; ACK slot: T+0..25, still recessive
    jmp pin RX, no_ack                ; T+26 recessive: nobody acknowledged
    ldi r3, 0
    jmp eof
no_ack:
    ldi r3, 1
eof:
    nop                         [255] ; ACK delimiter + EOF + intermission (> 10 bits)
    nop                         [255]
    mov isr, r3
    push block
    jmp idle
lost:                                 ; lost arbitration (or a bit error): back off
    pin TX, hi
    ldi r3, 2
    mov isr, r3
    push block
    jmp idle
