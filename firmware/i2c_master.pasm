; I2C master using open-drain pins (OD_MASK) with clock-stretching support.
; Ticks: 4 per SCL period nominal (CLKDIV = f_clk / (4 * f_scl)).
; Command words (TX FIFO):
;   [31:24] data byte (MSB first), [1:0] cmd: 0=WRITE 1=READ 2=START 3=STOP
;   [2] for READ: ack bit to send (0 = ACK, 1 = NACK)
; Replies (RX FIFO): WRITE -> [0] = ACK bit from target (0 = ACK); READ -> [7:0] data.
; Pins: OUT_BASE = SDA (count 1), IN_BASE = SDA; SDA/SCL given as defines.
; Every SCL release waits for the line with a timeout (R3); on timeout F1 is set.
.program i2c_master
.shift_out left
.shift_in left
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
    ldi r3, 2000
    wait 1 pin SCL timeout r3
    jmp t, stretch_timeout
    pin SDA, hi              [1]  ; STOP: SDA rises while SCL high
    jmp top
do_start:
    pin SDA, hi              [1]
    pin SCL, hi
    ldi r3, 2000
    wait 1 pin SCL timeout r3
    jmp t, stretch_timeout
    pin SDA, lo              [1]  ; START: SDA falls while SCL high
    pin SCL, lo              [1]
    jmp top
do_write:
    ldi r0, 7
wbit:
    out pins, 1                   ; SDA = next data bit (SCL is low)
    pin SCL, hi              [1]
    wait 1 pin SCL                ; clock stretching
    pin SCL, lo
    jmp r0--, wbit
    pin SDA, hi                   ; release SDA for ACK
    pin SCL, hi              [1]
    wait 1 pin SCL
    in pins, 1                    ; sample ACK
    pin SCL, lo
    push block
    jmp top
do_read:
    ldi r0, 7
    pin SDA, hi                   ; release SDA
rbit:
    pin SCL, hi              [1]
    wait 1 pin SCL
    in pins, 1
    pin SCL, lo              [1]
    jmp r0--, rbit
    and r1, 4                     ; ack bit to send
    jmp !r1, send_ack
    pin SDA, hi
    jmp ack_clk
send_ack:
    pin SDA, lo
ack_clk:
    pin SCL, hi              [1]
    wait 1 pin SCL
    pin SCL, lo
    pin SDA, hi
    push block
    jmp top
stretch_timeout:
    flag set 1
    jmp top
