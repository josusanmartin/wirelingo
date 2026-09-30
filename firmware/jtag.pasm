; JTAG shift engine. TCK = side-set pin; OUT pins = TDI (base) and TMS (base+1);
; IN pin = TDO. Each TX command shifts n TCK cycles:
;   TX word = [8:0] n-1 (1..512), followed by ceil(n/16) words of (TDI, TMS) bit
;   pairs, LSB first: bit 2i = TDI, bit 2i+1 = TMS for cycle i.
;   RX: n TDO bits (sampled just before each rising TCK edge), LSB first, in
;   32-bit words, the last one right-aligned.
; TCK period = 4 ticks, idling low.
.program jtag
.side_set 1 opt
.shift_out right autopull 32
.shift_in right autopush 32
top:
    pull block                side 0
    out r0, 9
    out null, 23                      ; data words autopull
    mov r2, r0
bit:
    out pins, 2               side 0 [1]  ; TDI/TMS change while TCK is low
    in pins, 1                side 1      ; sample TDO, rising edge
    jmp r0--, bit                   [0]
    and r2, 31                        ; right-align the last word
    xor r2, 31
align:
    jmp r2--, pad
    jmp top
pad:
    in null, 1
    jmp align
