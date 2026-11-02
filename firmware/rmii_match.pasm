; RMII cut-through match, 100 Mbit/s, CLKDIV 1, one core (docs/rmii-study.md): as
; firmware/eth10_match.pasm, by exact delay. Wait for the SFD, count clocks to the
; EtherType, sample it and the field that follows it on consecutive clocks, compare,
; raise a pin. Only RXD0 -> uio0, RXD1 -> uio1, CRS_DV -> uio4 are needed.
;
; The host pushes one word, the threshold. Frames: EtherType ETHERTYPE, then a 16-bit
; field, first byte low. If the field >= the threshold, pin HIT goes high, then the
; field goes to the RX FIFO. HIT is a latch: it stays high through the frame's end
; and idle, until the next frame's carrier. The threshold is its low 16 bits. The
; push is noblock: with RXF full the record goes, with no flag, and HIT still rises.
;
; The offsets are fixed: a VLAN tag moves the field and this firmware misses it. Like
; eth10_match the decision comes before the FCS, so a corrupted frame can raise HIT.
.program rmii_match
.define RX0 0                     ; uio0
.define RX1 1                     ; uio1
.define CRS 4                     ; uio4
.define HIT 11                    ; uo3
.define ETHERTYPE 0xB588          ; 0x88B5 on the wire, first byte low
.clkdiv 1
.in_pins 0
.shift_in right
; @assume clkdiv=1
; @deadline to_field from=sfd to=first min=49 max=49 unit=tick "the EtherType's first dibit, 12 bytes on"
; @deadline last_to_pin from=last to=hit min=8 max=8 unit=tick avoid=quiet2 reach=conditional "compare path, instructions only"
    pull block                    ; the threshold
    mov r2, osr
    ldi r3, ETHERTYPE
quiet:                            ; 4 clocks of no carrier, or the tail's toggling
    wait 0 pin CRS                ; CRS_DV could look like a new frame
    ldi r0, 3
    wait 1 pin CRS timeout r0
    jmp t, armed
    jmp quiet
armed:
    wait 1 pin CRS
    pin HIT, lo
sfd:
    wait 1 pin RX1 if pin RX0 == 1
    nop                      [47] ; 12 bytes, 48 dibits
first:                            ; 16 dibits, no spare clock: straight-line code
    in pins, 2
    in pins, 2
    in pins, 2
    in pins, 2
    in pins, 2
    in pins, 2
    in pins, 2
    in pins, 2
    in pins, 2
    in pins, 2
    in pins, 2
    in pins, 2
    in pins, 2
    in pins, 2
    in pins, 2
last:
    in pins, 2                    ; the path to the pin starts here
    mov r1, isr                   ; EtherType, low half
    cmp r1, r3
    jmp !z, quiet2
    in null, 16
    mov r1, isr                   ; the field
    cmp r1, r2
    jmp c, quiet2                 ; below the threshold
hit:
    pin HIT, hi
    mov isr, r1
    push noblock
    jmp quiet
quiet2:
    jmp quiet
