; Bidirectional-DShot ESC stand-in for the KV260 post-freeze demo
; (host/postfreeze_demo.py `bidir`): the second core answers the flight
; controller's frames (firmware/dshot_bidir.pasm) on the same wire, so the
; telemetry path can run without an ESC. A test fixture, not ESC firmware:
; it does not decode or check the frame (the host does, from the samples it
; pushes), and its reply is whatever the host queued.
;
; Pin E: open drain (OD_MASK), joined to the controller's pin by pe_lab's
; I2C-bus mode (uio0 and uio2 wired-AND with a pull-up). IN_BASE = E,
; OUT_BASE = E, OUT_COUNT = 1. 1 tick = 1/48 MHz (CLKDIV = f_clk / 48 MHz,
; 1.04 at 50 MHz): a reply level is 64 ticks (750 kbit/s).
;
; Per frame: pull the reply (TX FIFO: the 21 line levels in [31:11], l[0]
; first, pe.postfreeze.telemetry_levels); sample each of the frame's 16 bits
; 45 ticks (0.94 us) after its falling edge, between a 0's 0.625 us and a
; 1's 1.25 us low time, so a low sample is a 1; push the 16 samples
; (noblock); after TURN x 4 ticks drive the 21 levels; release the line.
; With the TX FIFO empty it stalls at the pull and answers nothing.
;
; Defines: E (pin), TURN (turnaround / 4 ticks, counted from the last
; bit's sample).
.program esc_emu
.shift_out left
.shift_in left
top:
    pull block                        ; the next reply
    mov isr, null
    ldi r1, 15
fbit:
    wait fall E                       ; a bit starts with a falling edge
    nop                       [31]
    nop                       [10]
    in pins, 1                        ; 0.94 us after it: low = a 1
    jmp r1--, fbit
    push noblock                      ; the frame as sampled (bits inverted)
    ldi r0, TURN
turn:
    jmp r0--, turn            [3]
    ldi r1, 20
level:
    out pins, 1               [62]    ; 64 ticks per level with the jmp
    jmp r1--, level
    pin E, hi                         ; release (open drain)
    jmp top
