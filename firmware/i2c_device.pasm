; I2C target (device) at a host-chosen 7-bit address, with clock stretching.
; The core does the bus; the host is the device's register model (pe.i2cdev:
; an EEPROM by default, or anything else, e.g. a clone of a captured device).
;
; Pins: SDA and SCL open drain (OD_MASK, pull-ups), owned by this core;
; OUT_BASE = SDA (count 1), IN_BASE = SDA; SDA/SCL given as defines.
; R3 = the 7-bit address (the host writes it before starting the core).
; Tick = clk (CLKDIV 1): the core sees an SCL edge 2-3 clocks after the pad
; (synchronizer). Measured on the model from the SCL fall (test_reverse_engineering):
; a data bit is on SDA after 3 clocks, a data byte's ACK after 4, the address
; ACK after 10 (the compare), the read stretch starts after 4. At 50 MHz that
; is <= 200 ns into an SCL low phase of >= 1300 ns (fast mode) or 500 ns (Fm+).
;
; RX events (core -> host):
;   0x100 | byte  address byte matched and ACKed ([0] = R/W)
;   0x200 | byte  data byte written by the master (ACKed)
;   0x300         read request: push the next byte to send
;   0x400         STOP
;   0x500         the master NACKed the byte just sent (end of the read)
; A repeated START shows up as the next 0x1xx. Other addresses are ignored.
; TX words (host -> core): [7:0] the byte to send, one per 0x300.
;
; Clock stretching: SCL is held low (1) from the start of each read byte until
; the host's byte is there, for at least 22 ticks, and (2) during an ACK when
; RXF is full, until the host drains it. So a slow host only slows the bus
; down (the master must allow stretching: i2c_master does, with a timeout).
; Each byte to send is on SDA 16 ticks before SCL is released (data setup:
; >= 250 ns at 50 MHz). The STOP and NACK events are pushed without blocking
; (the bus is free and can't be stretched there); they are dropped if RXF is
; full.
;
; r0 = byte/event, r1 = bit counter, r2 = R/W, then the constant 1, r3 = address.
; Three subroutines (CALL / RET, one level: docs/isa.md §5.2) carry what the
; address and data paths share: the bit loop, the event push with its stretch,
; and the bit that may turn out to be a STOP or a repeated START. g4, without
; CALL, had two copies of each (98 instructions; now 88).
.program i2c_device
.shift_in left
.shift_out left
.define SETUP 15
idle:
    pin SDA, hi
    pin SCL, hi
    wait fall SDA if pin SCL == 1      ; START
start:
    pin SDA, hi
    mov isr, null                      ; the address byte lands alone in the ISR
    wait 0 pin SCL
    ldi r1, 7
    call rxbits                        ; address byte, MSB first
    mov r0, isr
    mov r2, r0
    shr r2, 1
    cmp r2, r3
    jmp !z, idle                       ; not ours: wait for the next START
    pin SDA, lo                        ; ACK (SCL is low)
    mov r2, r0
    and r2, 1                          ; R/W
    or r0, 0x100
    call event                         ; push it; returns in the ACK clock
    wait 0 pin SCL
    jmp r2, rbyte
; ---- the master writes
wbyte:
    pin SDA, hi                        ; release the ACK
    mov isr, null
    ldi r2, 1
    call bit1                          ; first bit: may be a STOP or a repeated START
    ldi r1, 6
    call rxbits                        ; bits 2..8
    pin SDA, lo                        ; ACK
    mov r0, isr
    or r0, 0x200
    call event
    wait 0 pin SCL
    jmp wbyte
stop:
    ldi r0, 0x400
    mov isr, r0
    push noblock
    jmp idle
; ---- the master reads
rbyte:                                 ; SCL is low: hold it until the host's byte is here
    pin SCL, lo
    ldi r0, 0x300
    mov isr, r0
    push block
    pull block
    out null, 24
    out pins, 1               [SETUP]  ; MSB, set up before SCL is released
    pin SCL, hi
    ldi r1, 6
rbit:
    wait 1 pin SCL
    wait 0 pin SCL
    out pins, 1
    jmp r1--, rbit
    wait 1 pin SCL
    wait 0 pin SCL
    pin SDA, hi                        ; release SDA for the master's ACK
    wait 1 pin SCL
    jmp pin SDA, rnack
    wait 0 pin SCL
    jmp rbyte                          ; ACK: another byte
rnack:
    ldi r0, 0x500
    mov isr, r0
    push noblock
    wait 0 pin SCL
    call bit1                          ; after a NACK: STOP or repeated START (bit1 goes there)
    jmp idle                           ; a clock instead: not ours any more
; ---- subroutines
rxbits:                                ; r1 + 1 bits, MSB first, into the ISR; returns after the last SCL fall
    wait 1 pin SCL
    in pins, 1                         ; SDA, sampled while SCL is high
    wait 0 pin SCL
    jmp r1--, rxbits
    ret
event:                                 ; push r0; with RXF full, stretch SCL until the host reads.
    mov isr, r0                        ; returns once the ACK clock is high
    jmp rxf, estall
    push
    jmp eclk
estall:
    pin SCL, lo
    push block
    pin SCL, hi
eclk:
    wait 1 pin SCL
    ret
bit1:                                  ; SCL low: one bit, unless SDA moves while SCL is high
    wait 1 pin SCL
    jmp pin SDA, h1
h0:
    jmp !pin SCL, h0done
    jmp !pin SDA, h0
    jmp stop                           ; SDA rose while SCL high: STOP
h1:
    jmp !pin SCL, h1done
    jmp pin SDA, h1
    jmp start                          ; SDA fell while SCL high: repeated START
h0done:
    in null, 1
    ret
h1done:
    in r2, 1                           ; r2 = 1 here (after a NACK the bit is unused)
    ret
