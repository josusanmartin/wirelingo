; HD44780U character LCD, 4-bit interface: write with busy-flag polling,
; BF/AC read, DDRAM read. Tick = 100 ns (CLKDIV 5 at 50 MHz).
;
; Pins: DB7..4 on four contiguous bidirectional pads from DB (P0-P7): OUT and
; IN base = DB, OUT_COUNT 4; RS, RW any output; E = side-set pin. DB is
; released except while the chip writes; idle E = 0, RW = 0.
;
; Times in ticks meet the VCC 2.7-4.5 V table (datasheet pp. 49, 52; 3.3 V
; pads need it, and it is the stricter one): PWEH >= 450 ns (6 ticks when
; writing, 8 reading), tcycE >= 1000 ns (12, 16), tAS >= 60 ns (3), tAH >= 20 ns
; (3), tDSW >= 195 ns (8), tH >= 10 ns (4). tDDR <= 360 ns: DB is read 7 ticks
; after E rises, 700 ns less 2 clocks of synchroniser, so at least 500 ns at any
; clock that can make a 100 ns tick.
;
; TX word: [7:0] byte, [8] RS, [9] RW, [10] INIT. One reply per word:
;   RW=0: wait for BF=0 and write the byte (RS=0 IR, RS=1 DR). Reply 0.
;   RW=1 RS=0: read BF and AC at once, no wait. Reply [7:0] = BF AC6..0.
;   RW=1 RS=1: wait for BF=0, read DR. Reply [7:0] = the byte.
;   INIT: Figure 24 up to and including 0010: > 40 ms, 0011, > 4.1 ms,
;   0011, > 100 us, 0011, 211 us, 0010, 211 us. The two minimums are
;   stretched to 15 ms and 410 us: enough at a third of nominal fOSC. Reply 0. The host sends the
;   function set, display, clear and entry mode as ordinary writes.
; If BF stays 1 for BFTO + 1 polls (38 ticks, 3.8 us each) the byte is not
; written or read and the reply is 0x100.
;
; After BF=0 is seen the firmware waits 17 us before the next access, longer
; than the tADD of Figure 10 (1.5 / fOSC: 5.6 us at 270 kHz, 16.7 us at 90 kHz).
;
; @assume clkdiv=5
; @deadline e_wr_hi from=we1 to=we2 avoid=top min=6 max=6 unit=tick "PWEH of a written high nibble, 600 ns"
; @deadline e_wr_lo from=we3 to=we4 avoid=top min=6 max=6 unit=tick "PWEH of a written low nibble, 600 ns"
; @deadline e_wr_cyc from=we1 to=we3 avoid=top min=12 max=12 unit=tick "tcycE inside a written byte, 1200 ns"
; @deadline e_rd_hi from=re1 to=re2 avoid=top min=8 max=8 unit=tick "PWEH of a read nibble, 800 ns"
; @deadline e_rd_cyc from=re1 to=re3 avoid=top min=16 max=16 unit=tick "tcycE inside a read byte, 1600 ns"
; @deadline e_rd_sample from=re1 to=rs1 avoid=top min=7 max=7 unit=tick "first nibble read 7 ticks after E rises"
; @deadline e_rd_sample2 from=re3 to=rs2 avoid=top min=7 max=7 unit=tick "second nibble read 7 ticks after E rises"
; @deadline e_rd_ah from=re4 to=rwl avoid=top min=3 max=3 unit=tick "tAH: RW falls 300 ns after E"
.program hd44780_4bit
.side_set 1 opt
.shift_in left
.wrap_target
top:
    pull block
    mov r0, osr
    mov r1, r0
    and r1, 0x400
    jmp !z, init
    mov r1, r0
    and r1, 0x300
    cmp r1, 0x200
    jmp z, raw_read                   ; BF/AC: never waits
    call busy_wait
    jmp r1, bf_stuck
    mov r1, r0
    and r1, 0x100
    jmp z, rs0
    pin RS, hi
    jmp rs_set
rs0:
    pin RS, lo
rs_set:
    mov r1, r0
    and r1, 0x200
    jmp !z, read_dr
; ---- write both nibbles, high first
    mov r1, r0
    shr r1, 4
    mov pins, r1                      ; low 4 bits of r1 reach DB
    mov pindirs, ~null        [2]     ; tAS and tDSW start here
    nop side 1                [5]     ; @point we1
    nop side 0                [3]     ; @point we2
    mov pins, r0              [1]     ; held 4 ticks after E fell (tH)
    nop side 1                [5]     ; @point we3
    nop side 0                [3]     ; @point we4
    mov pindirs, null
    mov isr, null
    push block
    jmp top
read_dr:
    call rd_byte
    push block
    jmp top
raw_read:
    pin RS, lo
    call rd_byte
    push block
    jmp top
bf_stuck:
    ldi r1, 0x100
    mov isr, r1
    push block
    jmp top

; ---- Figure 24 as far as the interface switch; the LCD has 8 data bits until 0010
init:
    pin RS, lo
    pin RW, lo
    ldi r2, 6500                      ; 6501 * 64 ticks: 41.6 ms
    call delay
    ldi r1, 3
    call nibble
    ldi r2, 2343                      ; 15.0 ms (> 4.1 ms)
    call delay
    ldi r1, 3
    call nibble
    ldi r2, 63                        ; 410 us (> 100 us)
    call delay
    ldi r1, 3
    call nibble
    ldi r2, 32                        ; 211 us
    call delay
    ldi r1, 2
    call nibble
    ldi r2, 32
    call delay
    mov isr, null
    push block
.wrap

; ---- leaves: delay (r2 + 1) * 64 ticks; nibble writes r1[3:0]
delay:
    jmp r2--, delay           [63]
    ret
nibble:
    mov pins, r1
    mov pindirs, ~null        [2]
    nop side 1                [5]
    nop side 0                [3]
    mov pindirs, null
    ret

; ---- poll BF. r1 = 0 when free, else nonzero after BFTO polls. RS is left 0.
busy_wait:
    mov r3, lr
    ldi r2, BFTO
    pin RS, lo
bw_poll:
    call rd_byte
    mov r1, isr
    and r1, 0x80
    jmp z, bw_free
    jmp r2--, bw_poll
    mov lr, r3
    ret
bw_free:
    nop                       [63]    ; tADD: 17 us with the next two lines
    nop                       [63]
    nop                       [41]
    mov lr, r3
    ret

; ---- a whole byte, two E pulses, even for BF. ISR[7:0] = byte. RS already set.
rd_byte:
    mov isr, null
    pin RW, hi                [2]     ; tAS; DB is already released
    nop side 1                [6]     ; @point re1
    in pins, 4                        ; @point rs1
    nop side 0                [7]     ; @point re2
    nop side 1                [6]     ; @point re3
    in pins, 4                        ; @point rs2
    nop side 0                [2]     ; @point re4
    pin RW, lo                        ; @point rwl
    ret
