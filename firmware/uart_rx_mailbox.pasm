; Optional one-byte UART8N1 mailbox, core1, synchronized RX16.
; Active-high READY8 grants exactly one externally cooperating frame.
; Nominal115200 source; divider is supplied by pe.usb_uart_byte.load_writes().
; START lowers READY; good STOP publishes virtual24..31 then F7.
; Bad STOP discards the byte, sets sticky F6 and waits idle for a new grant.
; F7 is released only by core0 matching valid one-byte DATA ACK. No host
; RX FIFO, runtime rearm, extra buffer or lossless unthrottled-stream claim.
.program uart_rx_mailbox
.shift_in right
.in_pins RX
.out_pins 24 8
    wait 1 pin RX
ready:
    wait 0 flag 7
    wait 1 pin RX                   ; establish idle BEFORE granting prompt START
    pin READY, hi
start:
    wait 0 pin RX                   ; D
    pin READY, lo                   ; D+1
    ldi r0, 7                   [9] ; D+2..11
bitloop:
    in pins, 1                      ; D+12+8k
    jmp !r0, last
    sub r0, 1
    jmp bitloop                 [4]
last:
    in null, 24                     ; D+70 right-align complete byte
    nop                         [4] ; D+71..75
    jmp pin RX, publish             ; D+76 STOP
    flag set 6                      ; bad STOP: discard, sticky error
    wait 1 pin RX
    jmp ready
publish:
    mov pins, isr                   ; D+77 publish before valid flag
    flag set 7                      ; D+78 occupied; producer cannot overwrite
    jmp ready
