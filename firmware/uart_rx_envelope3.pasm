; C35 PRIVATE SOURCE HYPOTHESIS: NOT ASSEMBLED OR EXECUTED.
; Wire report = b0,b1,b2,n; n=1..3, bytes at indices n..2 are zero padding.
; Fixed4 transport/ACK identity,24-clock leaf and CRC16 scratch.
; Private image selects request2; shipping request1 sources remain unchanged.
; At most3 good UART bytes per pending group; idle flush is source-defined.
; READY closes before a3-clock synchronizer guard and final START recheck.
; A legal START already in flight wins over timeout; its whole frame is kept.
; Counts are retained IN the four-byte report, including across USB reset.
; No RTL, cold helper or C33 source change is made by this prototype.
.program uart_rx_envelope3_source
.shift_in right
.shift_out right
.in_pins RX
.out_pins 24 8
    pin ERROR, lo
    pin READY, lo
    flag clr 6
    flag clr 7                    ; COLD entry only
    mov isr, null
    ldi r3, 0x300
qualify_idle:
    call service
    jmp !pin RX, qualify_idle      ; bounded service while waiting out break
    pin READY, hi
    ldi r0, 1845                  ;1846 idle polls,26 clocks each
wait_start:
    call service
    jmp !pin RX, start_frame       ; detected START = D, poll every26 clocks
    jmp r0--, wait_start
    pin READY, lo                 ; close admission BEFORE committing a flush
    nop                        [2] ;3 clocks: include the two synchronizer stages
    jmp !pin RX, start_frame       ; last previously admitted START wins
    mov r0, r3
    shr r0, 8
    cmp r0, 3
    jmp z, qualify_idle           ; empty timeout: no zero-length report
    jmp pending_full              ; seal partial ISR until old report ACK
start_frame:
    pin READY, lo                  ; D+1, harmless if guard already lowered it
    or r3, 0x70                   ; D+2: seven nonfinal data bits
    ldi r0, 23                    ; D+3
first_delay:
    call service                  ;24 iterations *25 clocks, D+4..603
    jmp r0--, first_delay
    nop                       [20] ; D+604..624
sample:
    in pins, 1                    ; D+625+417*k, k=0..7
    mov r0, r3
    and r0, 0x70
    jmp !r0, last_sample
    sub r3, 0x10
    ldi r0, 15
bit_delay:
    call service                  ;16 iterations *25 clocks
    jmp r0--, bit_delay
    nop                        [9]
    jmp sample
last_sample:
    ldi r0, 15
stop_delay:
    call service                  ;16 iterations *25 clocks
    jmp r0--, stop_delay
    nop                       [11]
    jmp pin RX, good_stop          ; D+3961: nominal STOP sample
    pin ERROR, hi
    mov isr, ::isr                 ; discard bad byte, retain high-aligned prefix
    in null, 8
    mov isr, ::isr                 ; ISR count resets; deliberately unused
    jmp qualify_idle
good_stop:
    sub r3, 0x100                 ; only GOOD STOP decrements remaining bytes
    mov r0, r3
    shr r0, 8
    jmp !r0, pending_full
    jmp qualify_idle
pending_full:
    call service                  ; sealed full/partial report pauses new grants
    jmp f 7, pending_full          ; never overwrite retained data before ACK
promote:
    flag clr 6                    ; old F7 low; retained registers now disposable
    mov r0, r3
    shr r0, 8                     ;zero-padding bytes =3-good-byte count
    ldi r1, 3
    sub r1, r0                    ;valid count1..3
pad_partial:
    jmp !r0, append_count
    in null, 8                    ;align oldest byte toward low byte positions
    sub r0, 1
    jmp pad_partial
append_count:
    in r1, 8                      ;count is wire byte3; payload and count retained
    mov r1, isr
    mov osr, isr
    out null, 16
    mov r2, osr
    mov isr, null
    ldi r3, 0x301                 ;3 next bytes, next USB byte1
    mov osr, ::r1
    out null, 16
    out pins, 8                   ; initial byte0 = rev8(b1)
    flag set 6
    flag set 7                    ; report becomes visible AFTER initial byte
    jmp qualify_idle

; One constant-time leaf,24 clocks from CALL to returned caller instruction.
; All active publications FLAG6 at CALL+16; pin write at CALL+9/+11/+13.
; A clear coincident with F6 poll at CALL+2 is observed on the next call.
; F7 may clear meanwhile: never re-set here; next inactive call clears F6.
service:
    jmp !f 7, inactive             ; CALL+1
    jmp f 6, already_ready         ; CALL+2
    mov crc, r0                    ; requires16-bit CRC width, taps OFF
    mov r0, r3
    and r0, 3
    jmp !r0, byte0
    sub r0, 1
    jmp !r0, byte1
    sub r0, 1
    jmp !r0, byte2
byte3:
    mov osr, ::r2
    out null, 24
    out pins, 8
    xor r3, 3                     ;3 ->0, no carry into UART fields
    jmp publish
byte0:
    mov osr, ::r1
    out null, 16
    out pins, 8
    xor r3, 1                     ;0 ->1
    jmp publish                [4]
byte1:
    mov osr, ::r1
    out null, 24
    out pins, 8
    xor r3, 3                     ;1 ->2
    jmp publish                [2]
byte2:
    mov osr, ::r2
    out null, 16
    out pins, 8
    xor r3, 1                     ;2 ->3
    jmp publish
publish:
    flag set 6                    ; CALL+16 on all four active routes
    mov r0, crc
    ret                        [5] ; CALL+18..23
inactive:
    flag clr 6                    ; does NOT discard partial/full ISR or ERROR
    ret                       [20] ; CALL+3..23
already_ready:
    ret                       [20] ; CALL+3..23
