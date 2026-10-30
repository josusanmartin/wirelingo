; 10BASE-T cut-through match (core 0, with the line unit, docs/isa.md §4.7): act on
; a field of a frame while the frame is still arriving. The line unit recovers the
; Manchester bits as in eth10_rx: LINECFG = EN | RX_EN | CODE 2 (0x4005),
; LINEDIV[31:16] = clocks per bit (5 at 50 MHz, 4 at 40 MHz), LINEPINS[12:8] = RXP.
;
; The host pushes one word, the threshold, then the core watches frames: EtherType
; ETHERTYPE, then a 16-bit field (first byte low) in the first two payload bytes.
; If the field >= the threshold, pin HIT goes high right after the field's last
; bit, then the field goes to the RX FIFO. HIT stays high until the next frame.
;
; The decision comes before the FCS, which this firmware can't check (no CRC-32):
; a corrupted frame can raise HIT. The host checks what it needs from the records.
; The queue in the line unit absorbs the hunt for the SFD (six instructions per
; bit for at most 24 bits); `in line, n` stalls until the n bits are there, so the
; compare starts as soon as the last bit is recovered.
.program eth10_match
.define ETHERTYPE 0xB588          ; 0x88B5 on the wire, first byte low
.define HIT 8                     ; uo0
.shift_in right
    pull block                    ; the threshold
    mov r2, osr
    ldi r3, ETHERTYPE
frame:
    pin HIT, lo
    mov isr, null
    in line, 32                   ; most of the preamble, and link pulses end here
    jmp t, frame
hunt:                             ; the SFD ends in 1, 1
    in line, 1
    jmp t, frame
    mov r1, ::isr                 ; the newest bit is bit 0
    and r1, 3
    cmp r1, 3
    jmp !z, hunt
    in line, 32                   ; destination, source: 96 bits
    jmp t, frame
    in line, 32
    jmp t, frame
    in line, 32
    jmp t, frame
    in line, 16                   ; EtherType
    jmp t, frame
    in null, 16
    mov r1, isr
    cmp r1, r3
    jmp !z, drain
    in line, 16                   ; the field: the path to the pin starts here
    jmp t, frame
    in null, 16
    mov r1, isr
    cmp r1, r2
    jmp c, drain                  ; below the threshold
    pin HIT, hi
    mov isr, r1
    push noblock
drain:                            ; the rest of the frame
    in line, 16
    jmp t, frame
    jmp drain
