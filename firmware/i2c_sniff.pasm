; Passive I2C bus sniffer. Watches SDA/SCL (inputs only, never drives).
; RX words: 0x100 = START (or repeated START), 0x200 = STOP,
;           byte events = [7:0] data, [8] = 0, [9] = ACK bit (0 = ACK).
; Uses the WAIT qualifier: "SDA falls while SCL is high" is a START.
; Tick = clk (CLKDIV 1): polling is ~20 ns per step at 48 MHz, fine for 1 MHz I2C.
.program i2c_sniff
.shift_in left
idle:
    wait fall SDA if pin SCL == 1      ; START condition
start:
    ldi r0, 0x100
    mov isr, r0
    push block
    ldi r1, 8                          ; 9 bits per byte (8 data + ACK): count down from 8
    mov isr, null
bit:
    wait 0 pin SCL                     ; SCL low
    wait 1 pin SCL                     ; rising edge: sample SDA
    in pins, 1                         ; IN_BASE = SDA
    mov r2, pins                       ; r2 bit 0 = SDA at the rising edge
    and r2, 1
high:                                  ; while SCL is high, SDA must not change
    jmp !pin SCL, low
    mov r3, pins
    and r3, 1
    cmp r3, r2
    jmp z, high
    jmp !r3, start                     ; SDA fell while SCL high: repeated START
    ldi r0, 0x200                      ; SDA rose while SCL high: STOP
    mov isr, r0
    push block
    jmp idle
low:
    jmp r1--, bit
    ; 9 bits in the ISR: data in [8:1], ACK in [0] -> event [7:0] data, [9] ACK
    mov r0, isr
    mov r3, r0
    and r3, 1                          ; ACK bit
    shr r0, 1                          ; data
    shl r3, 9
    or r0, r3
    mov isr, r0
    push block
    ldi r1, 8
    mov isr, null
    jmp bit
