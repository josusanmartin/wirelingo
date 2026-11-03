; I2C master using open-drain pins (OD_MASK) with clock-stretching support.
; SCL period: 8 ticks without stretching, 4 low + 4 high (CLKDIV = f_clk / (8 * f_scl);
; at 100 kHz that is 5 us + 5 us against the 4.7 us / 4.0 us minimums). The high
; phase is 4 ticks when CLKDIV >= 3; below that the 2-clock input synchronizer
; delays the SCL read-back WAIT by a tick.
; Command words (TX FIFO):
;   [31:24] data byte (MSB first), [1:0] cmd: 0=WRITE 1=READ 2=START 3=STOP
;   [2] for READ: ack bit to send (0 = ACK, 1 = NACK)
; Replies (RX FIFO): WRITE -> [0] = ACK bit from target (0 = ACK); READ -> [7:0] data.
; Pins: OUT_BASE = SDA (count 1), IN_BASE = SDA; SDA/SCL given as defines.
;
; Every SCL release waits for the line with a timeout of TMO ticks (R3). On a
; timeout the master releases both lines, sets F1, and a WRITE or READ replies
; 0x100 (bit 8 set) instead, so the host is never left waiting.
; SDA is read back wherever the master drives it high: before a START, after a
; STOP, every bit of a written byte (each sampled while SCL is high, compared
; after the ACK) and a READ's NACK. A bit read back low (SDA stuck low, or
; another driver) is a bus fault: F1, and a WRITE or READ replies 0x200 (bit 9)
; instead (docs/fault-testing.md, finding 2). The ISR is empty between
; commands: every path that writes it ends in a PUSH.
; r0 = bit counter, r1 = command, r2 = command type, r3 = timeout budget.
;
; Timing annotations (python -m pe.timing firmware/i2c_master.pasm; docs/timing-check.md):
; SCL phases as the master drives them, from each `pin SCL` to the next, inside a
; command (avoid=top: SCL low between commands lasts as long as the host makes it).
; Clock stretching lengthens the high phase, up to the TMO-tick timeout; with no
; stretching (stall=scl_wait:0, which needs CLKDIV >= 3) it is 4 ticks inside a
; byte and 6 for the START (its `ldi r3` sits between the release and the WAIT).
; @assume clkdiv=60                               ; 100 kHz at 48 MHz: 1 tick = 1.25 us
; @deadline scl_high from=scl_hi to=scl_lo avoid=top min=4 max=2006 unit=tick reach=conditional "high, stretching up to the timeout"
; @deadline scl_high_ns from=scl_hi to=scl_lo avoid=top stall=scl_wait:0 min=4 max=6 unit=tick reach=conditional "high, no stretching"
; @deadline scl_low from=scl_lo to=scl_hi avoid=top min=4 max=8 unit=tick reach=conditional "low phase inside a command"
; @deadline t_high from=scl_hi to=scl_lo avoid=top min=192 reach=conditional "100 kHz: tHIGH >= 4.0 us (192 clk)"
; @deadline t_low from=scl_lo to=scl_hi avoid=top min=226 reach=conditional "100 kHz: tLOW >= 4.7 us (226 clk)"
.program i2c_master
.shift_out left
.shift_in left
.define TMO 2000
top:
    pull block
    mov r1, osr
    mov r2, r1
    and r2, 3
    jmp !r2, do_write
    cmp r2, 1
    jmp z, do_read
    cmp r2, 2
    jmp z, do_start
do_stop:
    pin SDA, lo              [1]
    pin SCL, hi                   ; @point scl_hi
    ldi r3, TMO
    wait 1 pin SCL timeout r3     ; @point scl_wait
    jmp t, stretch_timeout
    pin SDA, hi              [1]  ; STOP: SDA rises while SCL high
    jmp pin SDA, top              ; it did
    jmp sda_fault
do_start:
    pin SDA, hi
    ldi r3, TMO
    pin SCL, hi                   ; @point scl_hi
    wait 1 pin SCL timeout r3     ; @point scl_wait
    jmp t, stretch_timeout
    jmp !pin SDA, start_fault     ; SDA low with SCL high: no START
    pin SDA, lo              [1]  ; START: SDA falls while SCL high
    pin SCL, lo              [1]  ; @point scl_lo
    jmp top
do_write:
    mov r0, ::osr                 ; r0[7:0] = the byte, bit-reversed
    out r1, 8                     ; r1 = the byte
    mov osr, ::r0                 ; and back to OSR[31:24], MSB first
    shl r1, 1                     ; what ISR holds after the ACK, but the ACK bit
    ldi r0, 7
wbit:                             ; low: out, ldi (+ pin lo, jmp of the previous bit)
    out pins, 1                   ; SDA = next data bit (SCL is low)
    ldi r3, TMO
    pin SCL, hi                   ; high: pin, wait, jmp, in  @point scl_hi
    wait 1 pin SCL timeout r3     ; clock stretching  @point scl_wait
    jmp t, stretch_timeout
    in pins, 1                    ; read the bit back
    pin SCL, lo                   ; @point scl_lo
    jmp r0--, wbit
    pin SDA, hi                   ; release SDA for the ACK
    ldi r3, TMO
    pin SCL, hi                   ; @point scl_hi
    wait 1 pin SCL timeout r3     ; @point scl_wait
    jmp t, stretch_timeout
    in pins, 1                    ; sample ACK: ISR = read-back << 1 | ACK
    pin SCL, lo                   ; @point scl_lo
    mov r0, isr
    xor r0, r1                    ; the ACK alone if every bit read back as sent
    cmp r0, 2
    jmp !c, sda_fault
    mov isr, r0
    push block
    jmp top
do_read:
    ldi r0, 7
    pin SDA, hi                   ; release SDA
rbit:                             ; low: ldi (+ pin lo [1], jmp of the previous bit)
    ldi r3, TMO
    pin SCL, hi                   ; high: pin, wait, jmp, in  @point scl_hi
    wait 1 pin SCL timeout r3     ; @point scl_wait
    jmp t, stretch_timeout
    in pins, 1                    ; sample at the end of the high phase
    pin SCL, lo              [1]  ; @point scl_lo
    jmp r0--, rbit
    and r1, 4                     ; ack bit to send
    jmp !r1, send_ack
    pin SDA, hi
    jmp ack_clk
send_ack:
    pin SDA, lo
ack_clk:
    ldi r3, TMO
    pin SCL, hi                   ; @point scl_hi
    wait 1 pin SCL timeout r3     ; @point scl_wait
    jmp t, stretch_timeout
    mov r0, ~pins                 ; r0[0] = SDA read back, inverted
    pin SCL, lo                   ; @point scl_lo
    pin SDA, hi
    shl r0, 2
    and r0, r1                    ; r1 = 4 for a NACK: SDA must have been high
    jmp r0, sda_fault
    push block
    jmp top
start_fault:                      ; SCL low again, as after a START: a target
    pin SCL, lo              [1]  ; holding SDA for a clock edge gets it
sda_fault:                        ; SDA read back low where the master released it
    ldi r0, 0x200
    jmp fault
stretch_timeout:                  ; SCL held low by someone else for TMO ticks
    pin SDA, hi                   ; release the bus (SCL is already released)
    ldi r0, 0x100
fault:                            ; r0 = the reply
    flag set 1
    cmp r2, 2
    jmp !c, top                   ; START/STOP: no reply
    mov isr, r0
    push block
    jmp top
