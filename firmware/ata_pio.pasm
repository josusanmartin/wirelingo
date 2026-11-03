; ATA PIO, a working subset on this chip's pads: an 8-bit-wide host, polled,
; assembled for one PIO mode. docs/blind-test.md says what does not fit and why.
;
;   DD7:0 = P0-P7 (uio)              the only 8 bidirectional pads there are
;   DA0-2, CS0-, CS1- = P8-P12       OUT_BASE 0, OUT_COUNT 13: one MOV sets
;                                    DD7:0 and the address together
;   DIOR- = P13, DIOW- = P14         side-set base 13, 2 pins, bit 0 = DIOR-
;   IORDY = an input pad, IORDY define
;
; Not wired, because no pad is left: DD15:8, which need bidirectional pads, and
; RESET- (use SRST): all 7 output pads are taken, which also means the quad host
; port's uo[6:4] are, so the host port runs 1-bit. Not used by choice: INTRQ.
; This firmware polls, and 4 input pads stay free if a design wants it.
; DMARQ/DMACK- are not used (tie DMACK- high).
; A device with a 16-bit Data register gets full register access and reads of
; the low byte only. 512-byte sectors need the CFA feature set's 8-bit mode
; (SET FEATURES 01h), which is the host's first command.
;
; Tick = 20 ns (CLKDIV 1 at 50 MHz; a slower clock only lengthens everything).
; TS, RREC, RGAP, WREC, WGAP set one mode's strobe and recovery; ata_host.py's
; pio_defines(mode) gives them. A device starts at mode 0: run the mode 0 build,
; send SET FEATURES 03h, then load the faster build. Static deadlines below are
; for mode 0; the others are measured (docs/blind-test.md has the table).
; Smallest figures at the pads, mode 0 (test_smallest_figures_seen_at_the_pads):
; t0 620, t1 100, DIOR-/DIOW- low 320 (t2 >= 290 for 8-bit), t3 360, t4 100,
; t9 160 ns.
; Reads take DD 2 clocks of synchroniser before the sample tick, so 40 ns before
; DIOR- rises: 280 ns after it fell, later than the 240 ns (t2 - t5) at which a
; mode 0 device guarantees data.
;
; TX word: [31:29] op, [28:16] pin image {CS1-, CS0-, DA2:0, DD7:0}, [15:0] arg
;   0 WRITE    write the image's register and data
;   1 READ     read the image's register. RX: [7:0]
;   2 WAIT     poll Alternate Status until BSY = 0, for (arg + 1) * 1024 polls
;              (620 ns each at mode 0). RX: [7:0] last status, [8] timed out. A WAIT queued
;              behind a Command write polls 540 ns after that write's rise, past
;              the 400 ns ATA-3 gives BSY to appear (deadline status_400)
;   3 BREAD    one status read, then only if BSY = 0, DRQ = 1, ERR = 0 read
;              (arg + 1) * 4 bytes of Data. RX: status word as WAIT, [9] refused;
;              then the data words, first byte in [31:24], unless refused
;   4 BWRITE   the same check; then (arg + 1) TX words go to Data, first byte
;              in [31:24]. Refused: those TX words are still consumed
;   5 DELAY    (arg + 1) * 640 ns, for SRST's 5 us and the wait after it
; IORDY low at the end of a strobe holds it, up to 88 ticks = 1.76 us from the
; fall (tB is 1.25 us, plus tA). Past that the cycle ends anyway and flag FIO
; is set; the host clears it. The bus lines are idle during every FIFO wait: PUSH is
; explicit, never autopush, because a stalled autopush IN would sample after
; the strobe had gone.
;
; @assume clkdiv=1
; @deadline t2_rd from=rs0 to=rs1 min=16 max=88 unit=tick "DIOR- low 320 ns, or to 1.76 us with IORDY low"
; @deadline t2_wr from=ws0 to=ws1 min=16 max=88 unit=tick "DIOW- low 320 ns, or to 1.76 us with IORDY low"
; @deadline t0_poll from=rs0 to=rs0 avoid=op_read,bcheck,top,bread min=30 unit=tick reach=conditional "t0 between status polls"
; @deadline t0_bread from=rs0 to=rs0 avoid=top,bcheck,op_read,op_wait min=30 unit=tick reach=conditional "t0 inside a block read"
; @deadline t0_bwrite from=ws0 to=ws0 avoid=top,op_write min=30 unit=tick reach=conditional "t0 inside a block write"
; @deadline t1_reg_wr from=addr to=ws0 avoid=bwrite,op_delay,op_wait,op_read,bcheck min=4 unit=tick reach=conditional "t1 register write"
; @deadline t1_reg_rd from=addr to=rs0 avoid=op_wait,bcheck min=4 unit=tick reach=conditional "t1 register read"
; @deadline t1_status from=alt to=rs0 avoid=bcheck_done min=4 unit=tick reach=conditional "t1 before a status read"
; @deadline t4_t9_wr from=ws1 to=wrel avoid=bwrite,op_delay,op_wait,op_read,bcheck min=4 unit=tick reach=conditional "DD and address held 80 ns or more after DIOW- rises"
; @deadline status_400 from=ws1 to=rs0 avoid=op_read,bcheck,wi_back,op_delay,bwrite min=20 unit=tick reach=conditional "first poll >= 400 ns after a write rise"
.program ata_pio
.side_set 2 opt
.shift_in left
.shift_out left
.wrap_target
done:
    ldi r1, 0x1800                    ; CS0- = CS1- = 1: deselected
    mov pins, r1              side 3
    mov pindirs, null
top:
    pull block
    out r0, 3
    out r1, 13
    out r2, 16
    mov pins, r1                      ; @point addr
    jmp !r0, op_write
    cmp r0, 1
    jmp z, op_read
    cmp r0, 2
    jmp z, op_wait
    cmp r0, 3
    jmp z, bcheck
    cmp r0, 4
    jmp z, bcheck
    cmp r0, 5
    jmp z, op_delay
.wrap

op_write:
    ldi r3, 0xFF
    mov pindirs, r3
    call wr_strobe
    mov pindirs, null                 ; @point wrel
    jmp done

op_read:
    call rd_strobe
    push block
    jmp done

op_delay:
    jmp r2--, op_delay        [31]
    jmp done

; ---- poll status. r0 inner count, r2 outer, r1 status, r3 scratch
op_wait:
    ldi r1, 0x0E00                    ; CS1- = 0, DA = 6: Alternate Status
    mov pins, r1              [3]     ; @point alt
wo:
    ldi r0, 1023
wi:
    call rd_strobe
    mov r1, isr                       ; @point wi_back
    mov isr, null
    mov r3, r1
    and r3, 0x80
    jmp z, wdone
    jmp r0--, wi              [3]
    jmp r2--, wo
    or r1, 0x100
wdone:
    mov isr, r1
    push block
    jmp done

; ---- blocks: r0 keeps the op until the check is done
bcheck:
    ldi r1, 0x0E00
    mov pins, r1              [3]     ; @point alt
    call rd_strobe
    mov r1, isr                       ; @point bcheck_done
    mov isr, null
    mov r3, r1
    and r3, 0x89
    cmp r3, 0x08
    jmp !z, refuse
    mov isr, r1
    push block
    ldi r1, 0x1000                    ; CS0- = 0, DA = 0: Data
    mov pins, r1              [3]
    cmp r0, 3
    jmp !z, bwrite
bread:
    ldi r0, 3
bbyte:
    call rd_strobe
    jmp r0--, bbyte           [RGAP]
    push block                        ; bus idle while the host catches up
    jmp r2--, bread
    jmp done

bwrite:
    ldi r1, 0xFF
    mov pindirs, r1
bwword:
    pull block                        ; bus idle while the host catches up
    ldi r0, 3
bwbyte:
    out r1, 8
    or r1, 0x1000
    mov pins, r1
    call wr_strobe
    jmp r0--, bwbyte          [WGAP]
    jmp r2--, bwword
    jmp done

refuse:
    or r1, 0x200
    mov isr, r1
    push block
    cmp r0, 4
    jmp !z, done
drain:
    pull block
    jmp r2--, drain
    jmp done

; ---- leaves: one strobe each. r3 is theirs. Fall to rise TS + 4 ticks if IORDY is high.
; The WAIT reads IORDY TS + 2 ticks after the fall, less 2 of synchroniser: 60 ns
; at TS = 3, the shortest build, past tA's 35.
rd_strobe:
    nop                       side 2 [TS]     ; @point rs0
    ldi r3, ITO
    wait 1 pin IORDY timeout r3
    jmp t, rlate
rend:
    in pins, 8                side 3          ; @point rs1
    ret                       [RREC]
rlate:
    flag set FIO
    jmp rend

wr_strobe:
    nop                       side 1 [TS]     ; @point ws0
    ldi r3, ITO
    wait 1 pin IORDY timeout r3
    jmp t, wlate
wend:
    nop                       side 3 [WREC]   ; @point ws1
    ret
wlate:
    flag set FIO
    jmp wend
