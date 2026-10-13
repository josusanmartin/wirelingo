; Optional fixed4 UART/USB consumer, exact cycle25 instruction image.
; Pair with uart_rx_batch4.pasm (59 words), divider3333, ERROR pin9.
; F6 byte ready, F7 retained report. Exact wLength4 only, ACK cursor4133.
; Existing descriptor bytes/addresses and ACK/reset/controller checks retained.
; Conditional source timing only; no batch protocol/model/native result yet.
; Common pin24 byte interface; producer must observe F6 clear before writing.
; Uninterrupted cores, fixed configuration and eventual supplier progress.
.program usb_uart_batch4
.origin 0                          ; `mov pc, r2` jumps to absolute table addresses
.shift_in right
.shift_out right 16
.define F_STALL 0
.define F_ADDR 1
.define F_SENT 2
.define F_EXP 3
.define F_SETUP 4
.define F_CFG 5
.define FILTER0 0x0008             ; token filter of address 0, endpoint 0
.define CRC5_K 0x08                ; constant term of the software CRC5 (`addr_filter`)
    jmp reset
; ---------------------------------------------------------------- descriptors
; Each entry sends two bytes (low byte first) through `send2`, so byte offset b
; of a descriptor is at table address dev + b: a packet starts at an even
; offset (a multiple of 8). Toggle rule: bit 3 of the start address is 0 for
; the first packet of every transfer (dev = 1, cfg = 19, dev+4, cfg+4 all have
; it clear), so packets alternate DATA1, DATA0, ... as USB requires. Keep dev at
; address 1 (or 0) and both 18-byte descriptors in this order.
; The bytes are pe.usbdev.DEVICE_DESC and CONFIG_DESC (test_usb_autonomous
; checks the table against them).
table:
dev:                               ; device descriptor (18 bytes)
    ldi r1, 0x0112                 ; bLength 18, bDescriptorType 1
    call send2
    ldi r1, 0x0110                 ; bcdUSB 1.10
    call send2
    ldi r1, 0x0000                 ; class, subclass (also GET_STATUS's 00 00)
    call send2
    ldi r1, 0x0800                 ; protocol, bMaxPacketSize0 8
    call send2
    ldi r1, 0x1209                 ; idVendor 0x1209 (pid.codes)
    call send2
    ldi r1, 0x5634                 ; idProduct 0x5634
    call send2
    ldi r1, 0x0001                 ; bcdDevice 0.01
    call send2
    ldi r1, 0x0000                 ; iManufacturer, iProduct: none
    call send2
    ldi r1, 0x0100                 ; iSerialNumber none, bNumConfigurations 1
    call send2
cfg:                               ; configuration + interface (18 bytes)
    ldi r1, 0x0209                 ; bLength 9, bDescriptorType 2
    call send2
    ldi r1, 0x0012                 ; wTotalLength 18
    call send2
    ldi r1, 0x0101                 ; bNumInterfaces 1 (GET_CONFIGURATION's 01),
    call send2                     ; bConfigurationValue 1
    ldi r1, 0x8000                 ; iConfiguration none, bmAttributes bus powered
    call send2
    ldi r1, 0x0932                 ; bMaxPower 100 mA; interface bLength 9
    call send2
    ldi r1, 0x0004                 ; bDescriptorType 4, bInterfaceNumber 0
    call send2
    ldi r1, 0x0000                 ; bAlternateSetting 0, bNumEndpoints 0
    call send2
    ldi r1, 0x00FF                 ; bInterfaceClass vendor, subclass 0
    call send2
    ldi r1, 0x0000                 ; protocol 0, iInterface none
    call send2
; Fixed4 cyclic supplier: byte pairs are reversed by the producer.
; The one CALL returns into get_word for the second complete word.
mailbox:
    call get_word                 ; LR is the next instruction/get_word
get_word:
    wait 1 flag 6 clear
    in pins, 8
    wait 1 flag 6 clear
    in pins, 8
    mov osr, ::isr
    jmp s_bit                     ; tail call: do not overwrite table caller LR
; ---------------------------------------------------------------- bus reset, idle
reset:                             ; power-up and bus reset: the default address
    ldi r3, FILTER0
    flag clr F_ADDR
    flag clr F_CFG
    flag clr F_STALL
    ldi r2, dev                    ; no transfer
    call prep
    wait 1 pin DM                  ; J
idle:
    ldi r0, 120                    ; 2.5 us of SE0 is a bus reset
    wait 1 pin DM timeout r0
    jmp t, se0
    wait 0 pin DM                  ; a packet (any K in its SYNC will do: the
    jmp !pin DP, idle              ; line unit queues from its first edge), or SE0
                                   ; (a level: `wait fall` missed an SE0 that began
                                   ; within 2 clocks before it, i.e. a bus reset)
    in line, 16                    ; SYNC + PID
    jmp t, idle
    mov r0, ::isr                  ; 0x100 | rev8(PID) when the SYNC is right
    cmp r0, 0x14B                  ; ACK
    jmp z, got_ack
    cmp r0, 0x1C3                  ; DATA0
    jmp z, got_data0
    cmp r0, 0x1D2                  ; DATA1
    jmp z, got_data1
; ---------------------------------------------------------------- tokens
    flag clr F_SENT                ; the host's ACK must follow our DATA directly
    flag clr F_SETUP
    flag clr F_EXP
    in line, 16                    ; address, endpoint, CRC5 (or a SOF's frame)
    jmp t, idle                    ; too short
    in line, 1                     ; the frame must end here
    jmp t, tok_end
    jmp skip
tok_end:
    mov r0, ::isr                  ; the tail as the filter holds it
    cmp r0, r3
    jmp z, ours
    jmp !f F_ADDR, idle            ; another address or endpoint, a SOF, a bad CRC5
    cmp r0, r2                     ; the new address, before we saw the ACK of
    jmp !z, idle                   ; SET_ADDRESS's status stage (it was lost):
    mov r3, r2                     ; `commit`, inline: if this is an IN, its
    flag clr F_ADDR                ; answer must still go out within full
    ldi r2, dev                    ; speed's turnaround
ours:
    mov r0, isr
    shr r0, 8                      ; the PID
    cmp r0, 0x69
    jmp z, got_in
    cmp r0, 0xE1
    jmp z, expect                  ; OUT goes straight to shared expectation
    cmp r0, 0x2D
    jmp !z, idle                   ; SETUP falls into its flag operations
got_setup:
    flag set F_SETUP
    flag clr F_ADDR                ; a SETUP abandons a pending SET_ADDRESS
expect:
    flag set F_EXP
    jmp idle
; ---------------------------------------------------------------- IN
got_in:
    wait 1 pin DM                  ; the host's EOP is over (J)
    mov osr, r1             [TURN] ; turnaround
    out line, 16                   ; SYNC + STALL / DATA0 / DATA1
    jmp f F_STALL, echo
    flag set F_SENT
    mov crc, ~null                 ; CRC16 over the payload
    ldi r0, 0                      ; SET_ADDRESS's status stage: no data
    call !f F_ADDR, packet_len    ; r0 stays zero for SET_ADDRESS status
in_n:
    jmp !r0, finish                ; zero-length packet
    shl r0, 3
    mov pc, r2                     ; into the table: its entries call send2
send2:                             ; send r1's 2 bytes through the CRC, LSB first
    mov osr, r1
s_bit:
    out line, 1
    sub r0, 1
    jmp z, finish                  ; payload count reaches zero
s_more:
    jmp !osre, s_bit
    ret                            ; the next table entry
finish:                            ; the CRC16, inverted, MSB first
    mov r1, ~crc
    mov osr, ::r1
    out null, 16
    out line, 16
    call f F_ADDR, addr_filter     ; same optional leaf; LR later set by prep
fin_prep:
    call prep                      ; r1 was the data: again, for a retry
; ---------------------------------------------------------------- packet ends
echo:                              ; our own packet comes back: drop it
skip:                              ; or the rest of one we don't want
    in line, 16
    jmp t, idle
    jmp skip
se0:
    jmp !pin DP, reset             ; D- and D+ low for 2.5 us: bus reset
    jmp idle                       ; (low DM in K is not SE0)
; ---------------------------------------------------------------- ACK
got_ack:
    mov isr, ~null                 ; unread bits stay ones for final-bit dribble
    in line, 4                     ; at most three extra ones before EOP
    jmp t, ack_short
    jmp skip                       ; not ended: drain the remaining invalid tail
ack_short:
    mov r0, ::isr
    add r0, 1                     ; unread high bits are ones: FFFF iff valid
    jmp !z, idle                   ; ended invalid: never consume the next token
ack_end:
    jmp !f F_SENT, idle            ; not for our DATA
    flag clr F_SENT
    jmp f F_ADDR, ack_addr
    cmp r2, mailbox + (4 << 10)     ; only ACK of the fixed4 vendor DATA
    jmp !z, ack_advance
    flag clr 7                     ; release producer only after exact valid ACK
ack_advance:
    call packet_len                ; the packet went through: advance
    add r2, r0                     ; address + n
    shl r0, 10
    sub r2, r0                     ; bytes left - n
    call prep
    jmp idle
ack_addr:                          ; SET_ADDRESS's status stage completed
commit:                            ; the new address takes effect (also inline
    mov r3, r2                     ; at tok_end, without the jmp). r1 needs no
    flag clr F_ADDR                ; `prep`: it is DATA1 while F_ADDR is set,
    ldi r2, dev                    ; and DATA1 (r2 = dev, no transfer) after
    jmp idle
prep:                              ; r1 = SYNC + the PID the next IN gets
    ldi r1, 0x1E80                 ; STALL
    jmp f F_STALL, prep_ret
    ldi r1, 0x4B80                 ; DATA1: SET_ADDRESS's status stage,
    jmp f F_ADDR, prep_ret
    mov r0, r2
    and r0, 8
    jmp !r0, prep_ret              ; or bit 3 of the table address clear
    ldi r1, 0xC380                 ; DATA0
prep_ret:
    ret
packet_len:                        ; r0 = min(8, bytes left)
    mov r0, r2
    shr r0, 10
    cmp r0, 8
    jmp c, pl_ret
    ldi r0, 8
pl_ret:
    ret
addr_filter:                       ; r2 |= the CRC5 of (address, endpoint 0):
    mov r0, r2                     ; long division of the 11 bits, first bit
    shr r0, 3                      ; at bit 12 (13-bit ALU immediates); the
    and r0, 0x1FC0                 ; mask drops a CRC already there (a retry)
    ldi r1, 10
c5_bit:
    cmp r0, 0x1000
    jmp c, c5_next                 ; bit 12 clear
    xor r0, 0x1280                 ; x^5 + x^2 + 1 at bits 12, 9, 7
c5_next:
    shl r0, 1
    jmp r1--, c5_bit
    shr r0, 8                      ; the remainder was at bits 12..8
    xor r0, CRC5_K                 ; the 0x1F preset and final inversion
    or r2, r0
    ret
; ---------------------------------------------------------------- DATA
got_data1:
    jmp f F_SETUP, skip            ; a SETUP's data stage is DATA0
got_data0:
    jmp !f F_EXP, skip
    flag clr F_EXP
    mov crc, ~null                 ; CRC16 over what follows the PID
    ldi r0, 31
    jmp !f F_SETUP, d_count
d_cap:                             ; a SETUP's first 4 bytes, then
    in line, 1                     ; r1 = bRequest << 8 | bmRequestType
    jmp t, idle                    ; r2 = wValue, bit-reversed
    jmp r0--, d_cap
    mov r1, isr
    mov r2, ::isr
d_count:
    ldi r0, 0                      ; bits from here on
.wrap_target
d_bit:                             ; 3 instructions per bit (full speed: 4 clocks)
    in line, 1                     ; through the CRC tap
    jmp t, d_end
    add r0, 1
.wrap
d_end:                             ; the ISR's low half is the last payload word
    jmp !f F_SETUP, d_crc          ; (a SETUP's wLength)
    cmp r0, 48                     ; 8 bytes + CRC16 in all
    jmp !z, idle
d_crc:
    mov r0, crc                    ; residue 0x800D: (crc ^ 0xD) + itself
    xor r0, 0xD                    ; is 0 with a carry only for 0x8000
    add r0, r0
    jmp !c, idle                   ; bad: no handshake, the host retries
    jmp !z, idle
    ldi r0, 0xD280                 ; SYNC + ACK
    jmp f F_SETUP, handshake       ; a SETUP is always ACKed
    jmp !f F_STALL, handshake
    ldi r0, 0x1E80                 ; SYNC + STALL: OUT data of a refused request
handshake:
    wait 1 pin DM                  ; the host's EOP is over (J)
    mov osr, r0             [TURN] ; turnaround
    out line, 16
    jmp !f F_SETUP, echo
; ---------------------------------------------------------------- SETUP requests
; While our ACK goes out (76 clocks at full speed): r1 = bRequest << 8 |
; bmRequestType, r2 = rev16(wValue), ISR[15:0] = wLength. Every exit goes
; through `done`.
    flag clr F_SETUP
    flag clr F_STALL
    mov r0, isr                   ; common wLength, through dispatch
    cmp r1, 0x01C0                 ; vendor/device IN, request1 (bounded prototype)
    jmp z, get_mailbox
    cmp r1, 0x0680                 ; GET_DESCRIPTOR
    jmp z, get_desc
    cmp r1, 0x0500                 ; SET_ADDRESS
    jmp z, set_addr
    cmp r1, 0x0900                 ; SET_CONFIGURATION
    jmp z, set_cfg
    ldi r2, dev + 4 + (1 << 10)    ; 00
    cmp r1, 0x0880                 ; GET_CONFIGURATION
    jmp z, get_cfg
    ldi r2, dev + 4 + (2 << 10)    ; 00 00
    sub r1, 0x80                   ; GET_STATUS: device, interface, endpoint
    cmp r1, 3
    jmp c, clamp
stall:
    flag set F_STALL
    jmp done
get_cfg:
    jmp !f F_CFG, clamp
    ldi r2, cfg + 4 + (1 << 10)    ; 01
    jmp clamp
get_mailbox:
    jmp !f 7, stall                ; no report available; new SETUP may retry
    cmp r0, 4
    jmp !z, stall
    ldi r2, mailbox + (4 << 10)
    jmp done
get_desc:
    mov r1, r2
    and r1, 0xFF                   ; descriptor type, bit-reversed
    ldi r2, dev + (18 << 10)
    cmp r1, 0x80                   ; 1: device
    jmp z, clamp
    ldi r2, cfg + (18 << 10)
    cmp r1, 0x40                   ; 2: configuration
    jmp !z, stall                  ; strings, qualifier, ...
clamp:                             ; bytes left = min(length, wLength)
    mov r1, r2
    shr r1, 10
    sub r1, r0                    ; declared length minus requested length
    jmp c, done                    ; request larger: keep declared length
    shl r1, 10
    sub r2, r1                    ; remove only excess length; address unchanged
    jmp done
set_addr:                          ; r2 = the address, bit-reversed (its filter
    flag set F_ADDR                ; is computed while the status stage's
    jmp done                       ; zero-length packet goes out)
set_cfg:                           ; wValue 0 or 1
    flag clr F_CFG
    jmp !r2, cfg_ok
    shl r2, 1                      ; rev16(1) = 0x8000: 0 after the shift
    jmp !z, stall
    flag set F_CFG
cfg_ok:
    ldi r2, dev                    ; status stage: zero-length DATA1
done:
    jmp fin_prep                  ; existing CALL prep then echo

; Producer/error/ACK/reset contract: research/usb-batch-producer-ownership.md
