; 1-Wire master, standard speed. Tick = 1 us (CLKDIV = f_clk / 1 MHz).
; DQ is an open-drain pin (OD_MASK) with an external pull-up; IN_BASE = DQ.
; Commands (TX FIFO):
;   [1:0] = 0 reset       -> RX: 1 if a presence pulse was seen, else 0
;   [1:0] = 1 write byte  ([15:8] = byte, LSB first) -> RX: 0 when done
;   [1:0] = 2 read byte   -> RX: the byte
.program onewire
.shift_out right
.shift_in right
top:
    pull block
    out r1, 2
    out null, 6                       ; leaves the byte in OSR[7:0]
    jmp !r1, reset
    cmp r1, 1
    jmp z, write
read:
    ldi r0, 7
rbit:
    pin DQ, lo                [1]     ; 2 us low starts the slot
    pin DQ, hi                [10]    ; release
    in pins, 1                [47]    ; sample ~13 us into the slot, finish it
    jmp r0--, rbit
    in null, 24                       ; right-align the byte
    push block
    jmp top
write:
    ldi r0, 7
wbit:
    out r2, 1
    pin DQ, lo                [1]
    jmp !r2, wzero
    pin DQ, hi                [59]    ; 1: release after 2 us
    jmp wnext
wzero:
    nop                       [58]    ; 0: hold low for the slot
    pin DQ, hi
wnext:
    jmp r0--, wbit            [2]     ; recovery
    mov isr, null
    push block                        ; done
    jmp top
reset:
    pin DQ, lo                [239]
    nop                       [239]   ; 480 us low
    pin DQ, hi                [14]    ; release, let the pull-up win
    ldi r3, 240
    wait 0 pin DQ timeout r3          ; presence pulse within 240 us?
    ldi r1, 0
    jmp t, rdone
    ldi r1, 1
    wait 1 pin DQ                     ; end of the presence pulse
rdone:
    nop                       [200]   ; complete the reset slot
    mov isr, r1
    push block
    jmp top
