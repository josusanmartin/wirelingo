; HDMI-CEC device (initiator and follower) on one open-drain line: written
; after the RTL freeze (tag rc-93c2137-t48ba), firmware only
; (docs/post-freeze-protocols.md). HDMI 1.3a, Supplement 1 (CEC).
;
; Pin CEC: a uio pin in open-drain mode (OD_MASK) with the bus's pull-up;
; IN_BASE = CEC. 1 tick = 10 us: CLKDIV = f_clk / 100 kHz (500 at 50 MHz,
; exact). The host sets CRCCTRL to width 16 with the taps off and writes our
; logical address (0..14) to the CRC register (the firmware's fifth
; register; 16 = none: frames are received but never acknowledged).
;
; Bits (all times from the bit's falling edge): start bit low 3.7 ms, next
; fall at 4.5 ms; data bit 2.4 ms, low 0.6 ms for a 1 and 1.5 ms for a 0,
; sampled at 1.05 ms. A frame is a start bit and 1..16 blocks of 10 bits:
; 8 data bits MSB first, EOM (1 = the last block), ACK. The first block is
; the header (initiator << 4 | destination). The initiator sends ACK = 1; a
; follower addressed directly makes it a 0 by holding the line low until
; 1.5 ms; for a broadcast (destination 15) a 0 means "rejected".
;
; Follower (always): the start bit must be low 3.5..3.9 ms and the next fall
; come 4.3..4.7 ms after its fall (2 ticks of margin each way: 3.48..3.92
; and 4.28..4.72); every bit is sampled 1.05 ms after its fall is seen, the
; line must be high again by 1.85 ms (a 0 is low 1.3..1.7 ms), the next
; bit's fall must not come sooner than 2.0 ms (the receiver's 2.05 with
; margin) and, if it does not come by 2.8 ms (2.75), the frame has ended
; (normally after a block with EOM = 1, or after a NACK: the initiator
; stops). Frames to our address are acknowledged, every frame on the bus is
; reported. The follower's bookkeeping is padded to P_R ticks on every path
; (the timeout counter is the only stopwatch, and only while waiting).
;
; Initiator (when the host has queued a frame): waits until the line has
; been idle for the frame's signal free time (the host gives it in ticks:
; 3, 5 or 7 bit periods, as CEC asks for a retry, a new initiator, or the
; previous initiator again), sends it, and checks at every 1 it sends that
; the line is high at the sample point: if not, another initiator sent a 0
; (arbitration lost): it stops driving, keeps receiving the frame as a
; follower (and acknowledges it if it is for us) and reports the loss. A
; NACK (directed) or a rejection (broadcast) ends the frame. No retries in
; firmware: the host decides (CEC allows up to 5 for a NACK).
;
; Host protocol. TX FIFO: a header word [15:0] signal free time (ticks),
; [19:16] n - 1 (n = 1..16 blocks), [20] broadcast (the destination is 15),
; then ceil(n / 4) data words, the first block in [31:24]. Push a frame's
; words together (the firmware does not wait for them mid-frame), and only
; when the FIFO has room for all of them. RX FIFO:
;   received block: 1 << 24 | data << 4 | EOM << 3 | ACK << 2 | to us << 1
;                   | header block, the ACK bit as it was on the line, "to
;                   us": we acknowledged the frame;
;   our frame:      0x8000 | ok << 12 | NACK << 13 | lost << 14 | (blocks
;                   sent - 1) << 8;
;   error:          1 << 25 | reason << 8 | bits left in the block (r0):
;                   reason 1 = a bit came too early, 2 = the line was low
;                   too long, 3 = the frame stopped inside a block.
; A full RX FIFO drops the record and sets flag FOV. Flag FLOST is used
; internally (a lost arbitration, reported at the end of the frame).
; pe.postfreeze3.cec_words, cec_records.
;
; r0 = bit index in the block (9..2 data, 1 EOM, 0 ACK), r1 = flags (bit 0
; header block, bit 1 addressed to us, bit 2 broadcast while sending, [6:3]
; blocks left after this one, [11:8] block index), r2 = the bit / the
; drive flag / idle time, r3 = delays and the stopwatch.
;
; Timing annotations (python -m pe.timing firmware/cec.pasm -D CEC=0 -D FOV=7 -D FLOST=6):
; @assume clkdiv=500                               ; 50 MHz: 10 us ticks
; @deadline st_lo from=st to=st_rel stall=tpull:0 min=370 max=370 unit=tick "start bit low: 3.7 ms"
; @deadline st_bit from=st to=fall min=450 max=450 unit=tick stall=tpull:0 "start bit: 4.5 ms"
; @deadline bit from=fall to=fall avoid=done,lost min=240 max=240 unit=tick stall=tpull:0 reach=conditional "every bit we send: 2.4 ms"
; @deadline lo1 from=fall to=rel1 avoid=rel0 min=60 max=60 unit=tick reach=conditional "a 1: low 0.6 ms"
; @deadline lo0 from=fall to=rel0 avoid=rel1 min=150 max=150 unit=tick reach=conditional "a 0: low 1.5 ms"
; @deadline tsmp from=fall to=tsmp avoid=rel0 min=105 max=105 unit=tick reach=conditional "the initiator's sample: 1.05 ms"
; @deadline last from=fall to=done avoid=book,lost,rel0 stall=tpull:0 min=240 max=240 unit=tick reach=conditional "the end of our frame: 2.4 ms after the last ACK slot's fall"
; @deadline rsmp from=rfall to=rsmp avoid=noise,r_short,r_end min=105 max=105 unit=tick reach=conditional "follower: the sample, 1.05 ms after the fall is seen"
; @deadline rrel from=rfall to=rrel avoid=noise,r_short,r_end min=150 max=150 unit=tick reach=conditional "follower: our ACK released at 1.5 ms"
; @deadline rdrv from=rfall to=rdrv avoid=noise,r_short,r_end,rsmp max=6 unit=tick reach=conditional "follower: our ACK drive starts at once"
; @deadline rbook from=rrel to=rwait.enter min=19 max=19 unit=tick "follower: every bookkeeping path P_R + 1 ticks"
; @deadline lost_join from=fall to=r_book avoid=book,rel0,ack0,ack1 min=151 max=151 unit=tick reach=conditional "a lost arbitration joins the follower as if after its release"
; @deadline poll from=poll to=poll avoid=rx_start,have_hdr,get_hdr min=31 max=33 unit=tick reach=conditional "the idle loop (the idle count adds 31 per turn)"
.program cec
.shift_out left
.shift_in left
.in_pins CEC
.define ST_LO 370                     ; start bit low (3.7 ms)
.define ST_T 450                      ; start bit (4.5 ms)
.define BT 240                        ; bit (2.4 ms)
.define LO1 60                        ; a 1: low 0.6 ms
.define LO0 150                       ; a 0: low 1.5 ms
.define SMP 105                       ; sample point (1.05 ms)
.define MARK 0x1000                   ; received-block record marker (becomes bit 24)
.define POLL 24                       ; idle poll: POLL + 7 ticks per turn
.define SW 456                        ; start bit stopwatch: its next fall by 4.72 ms
.define P_R 18                        ; follower: ticks from the release to r_wait, every path
.define C_R 150 + P_R + 5             ; ... from the fall to the next WAIT's stopwatch base
.define W 280 - C_R                   ; no fall 2.8 ms after the last one ends the frame
.define KS W - 26                     ; r3 >= KS at the fall: sooner than 2.0 ms
.define KL W - 16                     ; r3 < KL at the rise: low for more than 1.85 ms
start:
    ldi r2, 0                         ; the line has been idle for r2 ticks
idle:
    jmp !osre, have_hdr               ; a frame is waiting
    jmp txne, get_hdr                 ; @point poll
    ldi r3, POLL - 1
    wait 0 pin CEC timeout r3         ; the line still idle?
    jmp t, quiet
    jmp rx_start                      ; someone's start bit
quiet:
    add r2, POLL + 7                  ; (the turn just made)
    cmp r2, 4000
    jmp c, idle
    ldi r2, 4000
    jmp idle
get_hdr:
    pull block                        ; the header stays in the OSR until it is sent
have_hdr:
    mov r3, osr                       ; the signal free time
    sub r3, r2                        ; still to wait
    jmp !c, sft
    ldi r3, 0
sft:
    wait 0 pin CEC timeout r3         ; idle for that long?
    jmp t, tx_go
    jmp rx_start                      ; no: receive first, then try again
; ================================================================ initiator
tx_go:
    pin CEC, lo                       ; the start bit   @point st
    out null, 11
    out r1, 5                         ; bc << 4 | n - 1
    out null, 16                      ; OSR empty: the data words follow
    mov r2, r1
    and r2, 16
    shr r2, 2                         ; bc -> bit 2
    and r1, 15
    shl r1, 3                         ; blocks left after the header -> [6:3]
    or r1, r2
    or r1, 1                          ; the header block, index 0
    pull ifempty block                ; @point tpull
    ldi r0, 9
    mov isr, null
    ldi r2, MARK
    in r2, 20                         ; (a record prefix, if we lose arbitration)
    out r2, 1                         ; the first bit
    ldi r3, ST_LO - 19
st_lo:
    jmp r3--, st_lo
    pin CEC, hi                       ; @point st_rel
    ldi r3, ST_T - ST_LO - 3
send:                                 ; r2 = the bit; its fall r3 + 1 ticks from now
    jmp r3--, send
txbit:
    pin CEC, lo                       ; @point fall
    jmp !r2, tx0
    nop                       [LO1 - 3]
    pin CEC, hi               [SMP - LO1 - 1]  ; @point rel1
    jmp pin CEC, t1ok                 ; @point tsmp
    jmp !r0, ack0                     ; the ACK slot: a follower's 0
    jmp lost                          ; elsewhere: another initiator's 0
t1ok:
    jmp !r0, ack1
    in r2, 1
    ldi r3, BT - SMP - 15
    jmp book
tx0:
    nop                       [LO0 - 3]
    pin CEC, hi                       ; @point rel0
    in r2, 1
    ldi r3, BT - LO0 - 14
    jmp book
book:                                 ; r0 = the bit just sent (9..1); 9 ticks to `send`
    sub r0, 1
    jmp !r0, nxt_ack
    cmp r0, 1
    jmp z, nxt_eom
    out r2, 1                 [3]     ; a data bit
    jmp send
nxt_ack:
    ldi r2, 1                 [5]     ; ACK: we send a 1
    jmp send
nxt_eom:
    mov r2, r1
    and r2, 0x78                      ; blocks left
    jmp r2, eom0
    ldi r2, 1                         ; EOM = 1: the last block
    jmp send
eom0:
    ldi r2, 0
    jmp send
ack0:                                 ; the line was 0 in the ACK slot
    mov r2, r1
    and r2, 4
    jmp r2, nack                      ; broadcast: rejected
    jmp blk_ok
ack1:                                 ; it was 1
    mov r2, r1
    and r2, 4
    jmp !r2, nack                     ; directed: nobody acknowledged
    nop
blk_ok:                               ; 111 ticks after the ACK slot's fall
    mov r2, r1
    and r2, 0x78
    jmp !r2, tx_ok                    ; that was the last block
    sub r1, 8                         ; one block fewer left
    add r1, 0x100                     ; the next block's index
    and r1, 0xFFE                     ; not the header any more
    pull ifempty block                ; every 4 blocks   @point tpull
    ldi r0, 9
    mov isr, null
    ldi r2, MARK
    in r2, 20
    out r2, 1
    ldi r3, BT - SMP - 21
    jmp send
tx_ok:
    ldi r2, 0x9000            [26]    ; ok (as long as the NACK path's drain)
    jmp tx_end
nack:                                 ; 110 ticks after the fall
    call drain
    ldi r2, 0xA000                    ; NACK
    nop
tx_end:                               ; 142 ticks after the ACK slot's fall
    mov r3, r1
    shr r3, 8
    and r3, 15
    shl r3, 8
    or r2, r3                         ; (the blocks sent - 1)
    mov isr, r2
    call put
    out null, 32                      ; nothing left in the OSR
    ldi r3, BT - 156
endw:
    jmp r3--, endw
    ldi r2, 0                         ; the end of our last bit   @point done
    jmp idle
lost:                                 ; 108 ticks after the fall
    in null, 1                        ; the bit on the line: 0
    flag set FLOST
    call drain
    and r1, 1                         ; follower from here: the header flag only
    ldi r2, 0
    out null, 32              [7]     ; nothing left in the OSR
    jmp r_book                        ; at 151 ticks, as the follower after its release
drain:                                ; discard the frame's data words not yet pulled (29 ticks)
    mov r2, r1
    shr r2, 8
    and r2, 15                        ; i, the block index
    mov r3, r1
    shr r3, 3
    and r3, 15                        ; k, the blocks left
    add r3, r2
    add r3, 4
    shr r3, 2                         ; the frame's words: ceil((i + k + 1) / 4)
    shr r2, 2
    sub r3, r2
    sub r3, 1                         ; minus those pulled: 0..3
    ldi r2, 2
dr_loop:
    jmp !r3, dr_skip
    pull noblock
    sub r3, 1
    jmp dr_next
dr_skip:
    nop                       [2]
dr_next:
    jmp r2--, dr_loop
    ret
; ================================================================ follower
rx_start:                             ; the line went low while idle (seen 2 ticks ago)
    ldi r3, SW                        ; the stopwatch
    wait 1 pin CEC timeout r3         ; the start bit's rise
    jmp t, r_low_idle                 ; low too long: not a start bit
    cmp r3, SW - 343
    jmp !c, noise                     ; rose before 3.48 ms
    cmp r3, SW - 388
    jmp c, noise                      ; after 3.92 ms
    ldi r0, 9
    ldi r1, 1                         ; the header block
    mov isr, null
    ldi r2, MARK
    in r2, 20
    ldi r2, 0                         ; no drive
    wait 0 pin CEC timeout r3         ; the first bit's fall (4.72 ms at the latest)   @point rfall
    jmp t, noise
    cmp r3, SW - 411
    jmp !c, noise                     ; before 4.28 ms
    jmp rx_bit
r_wait:
    ldi r3, W
    wait 1 pin CEC timeout r3         ; the line high again   @point rwait
    jmp t, r_low
    cmp r3, KL
    jmp c, r_low                      ; low for more than 1.85 ms
    wait 0 pin CEC timeout r3         ; the next bit   @point rfall
    jmp t, r_end
rx_next:
    cmp r3, KS
    jmp !c, r_short                   ; sooner than 2.0 ms after the last
    nop
rx_bit:                               ; the fall was seen 5 ticks ago
    jmp !r2, rx_nd
    pin CEC, lo                       ; ACK: hold the line low   @point rdrv
    jmp rx_s
rx_nd:
    jmp rx_s                  [1]
rx_s:
    nop                       [SMP - 9]
    mov r2, pins                      ; @point rsmp
    and r2, 1
    in r2, 1
    nop                       [LO0 - SMP - 4]
    pin CEC, hi                       ; release (our ACK)   @point rrel
r_book:                               ; r0 = the bit just received; P_R ticks to r_wait
    jmp !r0, r_ack
    sub r0, 1
    jmp !r0, r_toack
    cmp r0, 1
    jmp z, r_byte
    ldi r2, 0                         ; a data bit next
    jmp r_wait                [10]
r_toack:                              ; the ACK slot next
    mov r2, r1
    and r2, 2                         ; addressed to us: drive it
    jmp r_wait                [11]
r_byte:                               ; 8 data bits: the header's destination
    mov r2, r1
    and r2, 1
    jmp !r2, r_nh
    mov r2, isr
    and r2, 15
    mov r3, crc                       ; our logical address
    cmp r2, r3
    jmp !z, r_nm
    or r1, 2                          ; addressed to us
    jmp r_bz
r_nm:
    jmp r_bz                  [1]
r_bz:
    ldi r2, 0
    jmp r_wait
r_nh:
    ldi r2, 0                 [7]
    jmp r_wait
r_ack:                                ; the block is complete
    in r1, 2                          ; (to us, header)
    call put
    ldi r0, 9
    and r1, 2                         ; not the header any more
    mov isr, null
    ldi r2, MARK
    in r2, 20
    ldi r2, 0                 [3]
    jmp r_wait
r_end:                                ; no fall by 2.8 ms: the frame is over
    cmp r0, 9
    jmp z, r_fin                      ; after a whole block
    ldi r2, 3
    jmp r_err
r_short:
    ldi r2, 1
    jmp r_err
r_low:
    ldi r2, 2
r_err:                                ; reason in r2, r0 = bits left in the block
    mov isr, null
    ldi r3, 0x200
    in r3, 16
    in r2, 8
    in r0, 8
    call put
    wait 1 pin CEC                    ; until the line is released
    ldi r0, 0
    jmp r_rep
r_fin:
    ldi r0, 30                        ; idle since the last bit's nominal end
r_rep:
    jmp !f FLOST, r_idle
    flag clr FLOST
    ldi r2, 0xC000                    ; our frame: arbitration lost
    mov isr, r2
    call put
r_idle:
    mov r2, r0
    jmp idle
r_low_idle:
    wait 1 pin CEC
noise:
    ldi r2, 0
    jmp idle
put:                                  ; push the ISR; a full FIFO drops it (4 ticks)
    jmp rxf, full
    push noblock              [1]
    ret
full:
    flag set FOV
    push noblock
    ret
