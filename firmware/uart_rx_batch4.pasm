; Cycle26 source candidate: encoded divider3333, 32ticks/UARTbit.
; Optional fixed4 producer; no batch dynamic qualification yet.
; Core1 retains four GOOD UART bytes; STOP-error restores prior partial ISR.
; READY grants one frame at a time. RX16, READY8, sticky ERROR9 (extra uo1).
; F7=complete retained report, cleared only by core0's exact4 valid ACK.
; F6=published virtual byte ready, cleared by core0 WAIT before IN8 capture.
; Polling F7 avoids deadlock when ACK consumes a report with byte0 prefetched.
; No reception while a report is retained; not continuous unthrottled UART.
.program uart_rx_batch4
.shift_in right
.shift_out right
.in_pins RX
.out_pins 24 8
    pin ERROR, lo                  ; cold entry only; USB reset must not jump here
    flag clr 6
    mov isr, null
    ldi r3, 4
next_frame:
    wait 1 pin RX
    pin READY, hi
    wait 0 pin RX                  ; D
    pin READY, lo                  ; D+1
    mov osr, isr                   ; D+2: backup good partial group
    ldi r0, 7                 [44] ; D+3..47
bitloop:
    in pins, 1                    ; D+48+32k
    jmp !r0, last
    sub r0, 1
    jmp bitloop               [28]
last:
    nop                           ; D+274: retain accumulated bits
    nop                       [28] ; D+275..303
    jmp pin RX, good_stop          ; D+304
    pin ERROR, hi                 ; sticky until cold restart
    mov isr, osr                   ; discard only suspect byte, keep good partials
    wait 1 pin RX
    jmp next_frame
good_stop:
    sub r3, 1
    jmp !r3, full
    jmp next_frame
full:
    mov r1, isr                   ; low16 = b0 | b1<<8
    mov osr, isr
    out null, 16
    mov r2, osr                   ; high16 = b2 | b3<<8
    mov osr, ::r1
    out null, 16
    out pins, 8                   ; rev8(b1), first byte of reversed pair
    flag set 6
    flag set 7                    ; expose report only AFTER byte0-ready
    jmp wait_a
word0:
    mov osr, ::r1
    out null, 16
    out pins, 8
    flag set 6                    ; never re-set F7 on a retry cycle
wait_a:
    jmp !f 7, consumed
    jmp f 6, wait_a
    out pins, 8                   ; rev8(b0); prior clear observed separately
    flag set 6
wait_b:
    jmp !f 7, consumed
    jmp f 6, wait_b
    mov osr, ::r2
    out null, 16
    out pins, 8                   ; rev8(b3)
    flag set 6
wait_c:
    jmp !f 7, consumed
    jmp f 6, wait_c
    out pins, 8                   ; rev8(b2)
    flag set 6
wait_d:
    jmp !f 7, consumed
    jmp f 6, wait_d
    jmp word0                     ; fixed4 reads return supplier to first pair
consumed:
    flag clr 6                    ; discard speculative next byte when F7 clears
    mov isr, null
    ldi r3, 4
    jmp next_frame                ; ERROR pin intentionally remains sticky
