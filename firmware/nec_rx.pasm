; NEC infrared remote receiver: written after the RTL freeze (tag
; rc-93c2137-t48ba), firmware only (docs/post-freeze-protocols.md).
;
; Pin IR: the output of an IR receiver module (TSOP382xx style: the 38 kHz
; carrier demodulated, low while a burst is received). 1 tick = 10 us:
; CLKDIV = f_clk / 100 kHz (500 at 50 MHz). Every interval is measured by a
; level WAIT with a timeout, so nothing can hang and an edge that arrives
; early is never missed. Windows (nominal NEC in brackets):
;   leader burst 7..11 ms (9), space: 1.5..3.37 ms is a repeat code (2.25),
;   3.37..5.67 ms a frame (4.5); data burst <= 1 ms (0.56), data space
;   <= 1.12 ms is a 0 (0.56), 1.12..2.52 ms a 1 (1.69); stop burst <= 1 ms.
; A receiver module lengthens bursts and shortens spaces by up to ~0.2 ms;
; test_nec.py sweeps that and +-10 % senders.
;
; Host protocol (RX FIFO), one word per code:
;   frame:  [15:0] the 16 address bits as received, [23:16] command,
;           [24] command ok (the next byte was ~command),
;           [25] standard address (the address high byte is ~low byte)
;   repeat: 0x08000000
;   error:  0x10000000 | r0 (31 - r0 data bits had been received)
; pe.postfreeze.nec_rx_record decodes them.
;
; r0 = bit counter, r1 = address, r2 = 1 (constant) / command, r3 = timeouts.
;
; Timing annotations (python -m pe.timing firmware/nec_rx.pasm -D IR=16):
; @assume clkdiv=500                               ; 50 MHz, 1 tick = 10 us
; @deadline answer from=stop_end to=pushed avoid=err stall=pushed:0 max=40 unit=tick reach=conditional "the frame's record is in the FIFO <= 400 us after its stop burst (the host keeps the FIFO from filling up)"
.program nec_rx
.shift_in right
.shift_out right
idle:
    ldi r2, 1
    wait 0 pin IR                     ; a burst starts
    ldi r3, 1100
    wait 1 pin IR timeout r3          ; leader burst, <= 11 ms
    jmp t, stuck
    cmp r3, 401                       ; r3 = 1100 - length: >= 7 ms leaves <= 400
    jmp !c, idle                      ; shorter: noise
    ldi r3, 337
    wait 0 pin IR timeout r3          ; space
    jmp t, frame                      ; >= 3.37 ms: a frame (there is no `jmp !t`)
rep:
    cmp r3, 188                       ; r3 = 337 - space: >= 1.5 ms leaves <= 187
    jmp !c, idle
    ldi r3, 100
    wait 1 pin IR timeout r3          ; the stop burst (already started) ends
    jmp t, idle
    ldi r3, 0x0800
    mov isr, null
    in null, 16
    in r3, 16
    push block
    jmp idle
frame:
    ldi r3, 230
    wait 0 pin IR timeout r3          ; a frame's space ends by 5.67 ms
    jmp t, idle
    ldi r0, 31
    mov isr, null
bit:
    ldi r3, 100
    wait 1 pin IR timeout r3          ; burst <= 1 ms
    jmp t, err
    ldi r3, 112
    wait 0 pin IR timeout r3          ; space <= 1.12 ms: a 0
    jmp t, one
    in null, 1                        ; LSB first
    jmp r0--, bit
    jmp stop
one:
    ldi r3, 140
    wait 0 pin IR timeout r3          ; <= 2.52 ms: a 1
    jmp t, err
    in r2, 1
    jmp r0--, bit
stop:
    ldi r3, 100
    wait 1 pin IR timeout r3          ; stop burst <= 1 ms      @point stop_end
    jmp t, err
    mov osr, isr
    out r1, 16                        ; address
    out r2, 16                        ; command | ~command << 8
    ldi r0, 0                         ; flags
    mov r3, r2
    shr r3, 8
    xor r3, r2
    and r3, 0xFF
    cmp r3, 0xFF
    jmp !z, c_bad
    or r0, 0x100                      ; command ok
c_bad:
    mov r3, r1
    shr r3, 8
    xor r3, r1
    and r3, 0xFF
    cmp r3, 0xFF
    jmp !z, a_ext
    or r0, 0x200                      ; standard address
a_ext:
    and r2, 0xFF
    or r2, r0
    mov isr, null
    in r1, 16
    in r2, 16
    push block                        ; @point pushed
    jmp idle
err:
    ldi r3, 0x1000
    mov isr, null
    in r0, 16
    in r3, 16
    push block
stuck:
    wait 1 pin IR                     ; wait for the line to rest
    jmp idle
