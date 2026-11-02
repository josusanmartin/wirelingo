; RMII transmit, 100 Mbit/s, CLKDIV 1, one core (docs/rmii-study.md).
; One instruction: every clock, three bits from the FIFO go to TXD0, TXD1, TX_EN
; (uo0, uo1, uo2). The core has no spare clock to end a frame, so TX_EN is data. Ten
; samples per word, lowest first, bits [31:30] unused: pe.rmii.tx_words packs them.
; The host supplies preamble, SFD, payload, FCS and the gap. TX_EN = 0 samples are
; idle, and a word of them is how the host holds the gap. A frame must be followed by
; at least one idle sample: the pins hold their last value while the core waits for
; data, so without one TX_EN stays up.
;
; 30 bits per word every 10 clocks: the host must average one word per 10 clocks
; (8 SCK at 40 MHz, for example). FJOIN_TX gives 16 words of cushion. An empty FIFO
; stalls the OUT with the pins held: mid-frame that repeats a dibit with TX_EN high,
; and nothing on the chip records it. The far end's FCS check is what catches it.
.program rmii_tx
.clkdiv 1
.out_pins 8 3
.shift_out right autopull 30
.fifo join_tx
; @assume clkdiv=1 fifo_stall=0
; @deadline dibit from=loop to=loop min=1 max=1 unit=tick "one sample per clock while the host keeps up"
.wrap_target
loop:
    out pins, 3
.wrap
