; RMII receive, 100 Mbit/s, CLKDIV 1, one core, no line unit (docs/rmii-study.md).
; REF_CLK is the chip's clock: one dibit per clock, RXD0 first.
;
; `in pins, 2` on every clock leaves no instruction for the end of the frame, so the
; board gives each RXD line two pads, and SYNC_BYPASS gives them two ages:
;     RXD0 -> uio0 and uio2     RXD1 -> uio1 and uio3     CRS_DV -> uio4
;     SYNC_BYPASS = 0x0C (uio2, uio3 from the first flop: one clock newer)
; One `in pins, 5` then takes two dibits, so a dibit pair costs two instructions:
; the sample and a free one. CRS_DV stays on two flops: it rises asynchronously.
;
; The sample is V7, uio0, uio1, uio2, uio3 (IN_BASE = 31 wraps round to P0). V7 is
; held high: a tag, so that a data word is never 0, which is what a read of an empty
; FIFO returns. Six samples, 12 clocks, make a word: bits [1:0] are 0, and then per
; sample, lowest first, {new RXD, old RXD, tag}: 3 frame bytes per word. Every tag of
; a full word is 1; a frame's last word has tag 0 in the samples it did not get.
;
; A trailer ends every record: [15:0] = 1 | flags, [31:16] = MAXWORDS minus the full
; words pushed. Only a trailer has bit 0 set. Flags:
;     2  overflow: RXF was full when a word was due. The record is incomplete.
;     4  too long: over MAXWORDS. The record is incomplete.
;     8  missed: carrier arrived while the core was not ready, so a frame was lost.
;        This record's own data is still good if flags 2 and 4 are clear.
; Flags 2 and 8 also set F_OVF, sticky until the host clears it.
;
; The preamble is seen, not counted, and the FCS is the host's: a damaged SFD or a
; stray 01, 11 gives a record whose FCS fails, not a lost alignment for later frames.
;
; End of frame: when carrier ends before the data, CRS_DV goes low on a nibble's first
; dibit and high on its second until the PHY's FIFO is empty (LAN8720A datasheet rev
; 1.4 section 3.4.1.1), so only a nibble's second dibit is looked at. It is checked at
; the second dibit of each byte: a frame's end is seen one sample late, and every
; record carries two dibits after the FCS. The host keeps whole bytes.
.program rmii_rx
.define RX0 0                     ; uio0, the older RXD0
.define RX1 1                     ; uio1, the older RXD1
.define CRS 4                     ; uio4
.define TAG 31                    ; V7
.define F_OVF 0
.define MAXWORDS 600              ; 1800 bytes
.clkdiv 1
.in_pins 31
.shift_in right autopush 30
; @assume clkdiv=1
; @deadline sfd_to_data from=sfd to=s0 min=1 max=1 unit=tick "no dibit lost after the SFD"
; @deadline word from=s5 to=s5 min=12 max=12 unit=tick avoid=end0,end2,end4,overflow,toolong reach=conditional "a word every 12 ticks, exactly"
; @deadline s01 from=s0 to=s1 min=2 max=2 unit=tick avoid=end0,end2,end4,overflow,toolong reach=conditional "pairs are 2 clocks apart"
; @deadline s12 from=s1 to=s2 min=2 max=2 unit=tick avoid=end0,end2,end4,overflow,toolong reach=conditional
; @deadline s23 from=s2 to=s3 min=2 max=2 unit=tick avoid=end0,end2,end4,overflow,toolong reach=conditional
; @deadline s34 from=s3 to=s4 min=2 max=2 unit=tick avoid=end0,end2,end4,overflow,toolong reach=conditional
; @deadline s45 from=s4 to=s5 min=2 max=2 unit=tick avoid=end0,end2,end4,overflow,toolong reach=conditional
; @deadline s50 from=s5 to=s0 min=2 max=2 unit=tick avoid=end0,end2,end4,overflow,toolong reach=conditional
; @deadline turnaround0 from=end0 to=armed min=12 max=12 unit=tick avoid=busy1,busy2,idle "end of frame seen to hunting again, RXF not full"
; @deadline turnaround2 from=end2 to=armed min=11 max=11 unit=tick avoid=busy1,busy2,idle
; @deadline turnaround4 from=end4 to=armed min=10 max=10 unit=tick avoid=busy1,busy2,idle
    pin TAG, hi
    ldi r3, 1
idle:                             ; out of step with the wire: wait for 4 quiet clocks
    wait 0 pin CRS
    ldi r0, 3
    wait 1 pin CRS timeout r0
    jmp t, armed
    or r3, 8                      ; carrier in the way: that frame is gone
    flag set F_OVF
    jmp idle
armed:
    ldi r1, MAXWORDS
    wait 1 pin CRS
    wait 1 pin RX0 if pin RX1 == 0    ; a preamble dibit, 01. No count: a PHY may trim it
sfd:                              ; the SFD's last dibit, 11, the first with RXD1 high
    wait 1 pin RX1 if pin RX0 == 1
word:
s0: in pins, 5
    jmp !pin CRS, end0            ; dibit 1 of 12
s1: in pins, 5                [1]
s2: in pins, 5
    jmp !pin CRS, end2            ; dibit 5
s3: in pins, 5
    jmp rxf, overflow             ; s5 pushes: refuse here, so that it can't stall
s4: in pins, 5
    jmp !pin CRS, end4            ; dibit 9
s5: in pins, 5
    jmp r1--, word
toolong:
    or r3, 4
    jmp drop
overflow:
    or r3, 2
    flag set F_OVF
drop:                             ; give up on this frame, wait for its end
    mov isr, null
drop2:
    wait 0 pin CRS
    ldi r0, 3
    wait 1 pin CRS timeout r0
    jmp t, tail
    jmp drop2
end0:                             ; bring the word to 25 bits, tag 0 in what is missing
    in null, 20
    jmp last
end2:
    in null, 10
end4:
last:                             ; wait for room with an eye on the wire, then push
    jmp rxf, busy1
    in null, 5                    ; 30 bits: autopush
tail:
    jmp rxf, busy2
    in r3, 16
    in r1, 16                     ; 32 bits: autopush
    mov r0, r3
    and r0, 8
    ldi r3, 1
    jmp !z, idle                  ; a frame went by: find the wire again
    jmp armed
busy1:
    jmp !pin CRS, last
    or r3, 8
    flag set F_OVF
    jmp last
busy2:
    jmp !pin CRS, tail
    or r3, 8
    flag set F_OVF
    jmp tail
