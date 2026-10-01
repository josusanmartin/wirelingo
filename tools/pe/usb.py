"""USB low-speed bit-level helpers (reference host side for tests).

Line states (low speed): J = D- high / D+ low (idle), K = D+ high / D- low, SE0 = both low.
Packets: SYNC (0x80 LSB-first), PID, payload, CRC; NRZI (0 = transition) with a
stuffed 0 after six consecutive 1s; EOP = SE0, SE0, J.
"""
J, K, SE0 = "J", "K", "0"

PID_OUT, PID_IN, PID_SETUP = 0x1, 0x9, 0xD
PID_DATA0, PID_DATA1 = 0x3, 0xB
PID_ACK, PID_NAK, PID_STALL = 0x2, 0xA, 0xE


def pid_byte(pid):
    return pid | ((~pid & 0xF) << 4)


def crc5(value, nbits=11):
    """USB CRC5 over `nbits` LSB-first bits of value; returns the 5-bit field as sent."""
    crc = 0x1F
    for i in range(nbits):
        b = (value >> i) & 1
        fb = ((crc >> 4) & 1) ^ b
        crc = (crc << 1) & 0x1F
        if fb:
            crc ^= 0x05
    crc ^= 0x1F
    # transmitted MSB of the CRC register first => reverse into LSB-first field
    return int(f"{crc:05b}"[::-1], 2)


def crc16(data):
    """USB CRC16 over bytes (LSB-first), returns the 2 bytes as sent."""
    crc = 0xFFFF
    for byte in data:
        for i in range(8):
            b = (byte >> i) & 1
            fb = ((crc >> 15) & 1) ^ b
            crc = (crc << 1) & 0xFFFF
            if fb:
                crc ^= 0x8005
    crc ^= 0xFFFF
    r = int(f"{crc:016b}"[::-1], 2)
    return bytes([r & 0xFF, r >> 8])


def token(pid, addr, endp):
    v = addr | (endp << 7)
    c = crc5(v)
    v |= c << 11
    return bytes([pid_byte(pid), v & 0xFF, v >> 8])


def token_filter(addr, endp):
    """The usb_ls_rx `filter` immediate for tokens to (addr, endp): the 16 bits
    after the PID (address, endpoint, CRC5) as the firmware sees them, bit-reversed."""
    v = addr | (endp << 7)
    v |= crc5(v) << 11
    return int(f"{v:016b}"[::-1], 2)


def data_packet(pid, payload):
    return bytes([pid_byte(pid)]) + bytes(payload) + crc16(payload)


def handshake(pid):
    return bytes([pid_byte(pid)])


def bits_lsb_first(data):
    for byte in data:
        for i in range(8):
            yield (byte >> i) & 1


def encode(packet):
    """Bytes (without SYNC) -> list of line states, including SYNC and EOP."""
    states, level, ones = [], J, 0
    for b in bits_lsb_first(bytes([0x80]) + packet):
        if b == 0:
            level = K if level == J else J
            ones = 0
        else:
            ones += 1
        states.append(level)
        if ones == 6:
            level = K if level == J else J
            states.append(level)
            ones = 0
    return states + [SE0, SE0, J]


def nrzi_destuff(states):
    """Line states from idle (J) -> NRZI-decoded, destuffed bits up to SE0, from
    the first K. Raises ValueError on a bit stuffing error."""
    try:
        i = states.index(K)
    except ValueError:
        raise ValueError("no packet") from None
    prev, bits, ones = J, [], 0
    for s in states[i:]:
        if s == SE0:
            break
        b = 1 if s == prev else 0
        prev = s
        if ones == 6:
            if b != 0:
                raise ValueError("bit stuffing error")
            ones = 0
            continue
        ones = ones + 1 if b else 0
        bits.append(b)
    return bits


def decode(states):
    """Line states -> packet bytes (after SYNC), or raises ValueError."""
    bits = nrzi_destuff(states)
    if bits[:8] != [0, 0, 0, 0, 0, 0, 0, 1]:
        raise ValueError(f"bad SYNC {bits[:8]}")
    bits = bits[8:]
    if len(bits) % 8:
        raise ValueError(f"{len(bits)} bits is not a whole number of bytes")
    return bytes(sum(bits[8 * k + j] << j for j in range(8)) for k in range(len(bits) // 8))
