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
; r0 = bit counter, r1 = command, r2 = command type, r3 = timeout budget.
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
    pin SCL, hi
    ldi r3, TMO
    wait 1 pin SCL timeout r3
    jmp t, stretch_timeout
    pin SDA, hi              [1]  ; STOP: SDA rises while SCL high
    jmp top
do_start:
    pin SDA, hi              [1]
    pin SCL, hi
    ldi r3, TMO
    wait 1 pin SCL timeout r3
    jmp t, stretch_timeout
    pin SDA, lo              [1]  ; START: SDA falls while SCL high
    pin SCL, lo              [1]
    jmp top
do_write:
    ldi r0, 7
wbit:                             ; low: out, ldi (+ pin lo, jmp of the previous bit)
    out pins, 1                   ; SDA = next data bit (SCL is low)
    ldi r3, TMO
    pin SCL, hi                   ; high: pin, wait, jmp [1]
    wait 1 pin SCL timeout r3     ; clock stretching
    jmp t, stretch_timeout   [1]
    pin SCL, lo
    jmp r0--, wbit
    pin SDA, hi                   ; release SDA for the ACK
    ldi r3, TMO
    pin SCL, hi
    wait 1 pin SCL timeout r3
    jmp t, stretch_timeout
    in pins, 1                    ; sample ACK
    pin SCL, lo
    push block
    jmp top
do_read:
    ldi r0, 7
    pin SDA, hi                   ; release SDA
rbit:                             ; low: ldi (+ pin lo [1], jmp of the previous bit)
    ldi r3, TMO
    pin SCL, hi                   ; high: pin, wait, jmp, in
    wait 1 pin SCL timeout r3
    jmp t, stretch_timeout
    in pins, 1                    ; sample at the end of the high phase
    pin SCL, lo              [1]
    jmp r0--, rbit
    and r1, 4                     ; ack bit to send
    jmp !r1, send_ack
    pin SDA, hi
    jmp ack_clk
send_ack:
    pin SDA, lo
ack_clk:
    ldi r3, TMO
    pin SCL, hi
    wait 1 pin SCL timeout r3
    jmp t, stretch_timeout   [1]
    pin SCL, lo
    pin SDA, hi
    push block
    jmp top
stretch_timeout:                  ; SCL held low by someone else for TMO ticks
    pin SDA, hi                   ; release the bus (SCL is already released)
    flag set 1
    cmp r2, 2
    jmp !c, top                   ; START/STOP: no reply
    ldi r0, 0x100
    mov isr, r0
    push block
    jmp top
