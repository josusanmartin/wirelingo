; Cooperative UART receiver and four-byte USB report supplier.
; Pair with usb_uart_overlap4.pasm: 256 + 93 words, existing RTL.
; C32 finite model case passed; limits in docs/usb-uart-overlap4.md.
; Core1 divider256 (one tick/system clock), CRCCTRL=0x000f0000 mandatory.
; CRC is a 16-bit service scratch register: BOTH CRC taps disabled.
; Shiftctrl3: right IN/OUT, NO autopush/autopull, no FIFO/LINE instructions.
; Same RX16/READY8/ERROR9/virtual24..31 and F6/F7 fixed4 interface.
; R1/R2 retain old report; ISR accumulates up to4 next GOOD UART bytes.
; R3 = remaining_good_bytes<<8 | remaining_data_bits<<4 | next_USB_byte.
; R0 is UART delay/temp; CRC saves it during service; OSR is disposable.
; One CALL service takes exactly24 clocks on every path, including CALL.
; No nested CALL. Z/C/ISR count/OSR count have no live role across service.
; UART source must honor NEW READY and finish the entire previous frame.
; USB reset does not restart this producer; ERROR remains sticky.
.program uart_rx_overlap4
.shift_in right
.shift_out right
.in_pins RX
.out_pins 24 8
    pin ERROR, lo
    pin READY, lo
    flag clr 6
    flag clr 7                    ; COLD entry only
    mov isr, null
    ldi r3, 0x400
qualify_idle:
    call service
    jmp !pin RX, qualify_idle      ; bounded service while waiting out break
    pin READY, hi
wait_start:
    call service
    jmp pin RX, wait_start         ; detected START = D, poll every25 clocks
    pin READY, lo                  ; D+1
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
    call service                  ; complete next report pauses new UART grants
    jmp f 7, pending_full          ; never overwrite retained data before ACK
promote:
    flag clr 6                    ; discard old speculative byte; F7 is low
    mov r1, isr
    mov osr, isr
    out null, 16
    mov r2, osr
    mov isr, null
    ldi r3, 0x401                 ;4 next bytes, next USB byte1
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
