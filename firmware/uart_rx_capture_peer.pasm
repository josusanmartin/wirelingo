; Optional peer for autonomous usb_enum: core1/bank1, F6, read-only RX16.
; USB retains core0/F0..F5/line unit/pads0..1; not FIFO-USB compatible.
; Declared-length UART capture, core1, 8N1/LSB-first, one logical RX define.
; CLKDIV=f_clk/(8*baud), IN_BASE=RX; host pays R1=N (1..32), R2=N mod4.
; Empty target FIFOs and stopped/disabled target required before arming.
; Full groups autopush little endian. Final1..3 bytes PUSH high aligned.
; HALT follows final STOP sample (not necessarily physical STOP end).
; Bad STOP sets sticky F6, retaining suspect data. F6 belongs to this workload.
; Earlier full words may appear before STOP validation; wait for HALT/status/F6.
; Known length/start required: no idle packet inference or timeout/flush promise.
.program uart_rx_capture_peer
.shift_in right autopush 32
    wait 1 pin RX
.wrap_target
start:
    wait 0 pin RX
    ldi r0, 7                  [10]
bitloop:
    in pins, 1
    jmp !r0, last
    sub r0, 1
    jmp bitloop                [4]
last:
    sub r1, 1                       ; D70, counts offered frames, bad bytes retained
    jmp !r1, final_stop              ; D71
    nop                        [3]  ; D72..75
    jmp pin RX, start               ; D76, ordinary rearm remains D77
    flag set 6
    wait 1 pin RX
.wrap
final_stop:
    nop                        [3]  ; D72..75
    jmp pin RX, finish              ; D76
    flag set 6
finish:
    jmp !r2, halted                 ; complete groups already autopushed
    push block                     ; partial groups only, no metadata word
halted:
    halt
