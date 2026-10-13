; I2S transmitter (Philips I2S, 16-bit stereo): written after the RTL freeze
; (tag rc-93c2137-t48ba), firmware only (docs/post-freeze-protocols.md).
;
; Pins: side-set base = BCLK, BCLK + 1 = WS (LRCLK); OUT pin = SD.
; 2 ticks per bit, 32 bits per frame: f_tick = 64 * fs, CLKDIV = f_clk / (64 fs)
; (48 kHz: 3.072 MHz ticks; 16 at 49.152 MHz, 16.276 at 50 MHz: fractional,
; so a BCLK edge moves by up to one clock).
; SD changes on BCLK falling edges and is sampled by the receiver on rising
; edges, MSB first; WS changes with the last bit of the previous word, one
; BCLK before the MSB (WS low = left).
;
; Host protocol: one TX FIFO word per stereo frame, left sample in [31:16],
; right in [15:0] (pe.postfreeze.i2s_word). On underrun the frame is silence
; (`pull noblock` loads R0 = 0): BCLK and WS never stop, unlike an autopull
; design, which would freeze the clocks mid-frame until data arrives.
;
; Timing annotations (python -m pe.timing firmware/i2s_tx.pasm):
; @assume clkdiv=16                                  ; 48 kHz at 49.152 MHz
; @deadline bit from=sd to=sd min=2 max=2 unit=tick "one bit = one BCLK period = 2 ticks"
; @deadline frame from=frame to=frame min=64 max=64 unit=tick "a stereo frame = 32 BCLK, also on underrun"
; @deadline ws_high from=ws_r to=ws_l min=32 max=32 unit=tick "WS high (right) for 16 BCLK"
; @deadline ws_low from=ws_l to=ws_r min=32 max=32 unit=tick "WS low (left) for 16 BCLK"
.program i2s_tx
.side_set 2                          ; side bit 0 = BCLK, bit 1 = WS
.shift_out left
    ldi r0, 0              side 0b00  ; the underrun word
    pull noblock           side 0b01  ; first frame
.wrap_target
    out pins, 1            side 0b00  ; b0, left MSB                    @point frame @point sd
    ldi r1, 13             side 0b01
left:
    out pins, 1            side 0b00  ; b1..b14                         @point sd
    jmp r1--, left         side 0b01
    out pins, 1            side 0b10  ; b15, left LSB: WS -> right      @point ws_r @point sd
    ldi r1, 13             side 0b11
right:
    out pins, 1            side 0b10  ; b16..b29                        @point sd
    jmp r1--, right        side 0b11
    out pins, 1            side 0b10  ; b30                             @point sd
    nop                    side 0b11
    out pins, 1            side 0b00  ; b31, right LSB: WS -> left      @point sd @point ws_l
    pull noblock           side 0b01  ; next frame, or silence (R0)
.wrap
