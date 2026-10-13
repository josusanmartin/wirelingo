"""Transport-independent words for spi_master_packed.pasm; no pin execution.

Queued frames use one header to supply all message length state.
preload_plan instead supplies disabled-core CSR actions for one-shot frames. Payload padding is
never clocked; partial RX words contain only their low 8*(N%4) bits. Length
and mode are caller-known command context, not an autonomous RX descriptor.
WORD_BITS=8 offers the same header/frame contract for traffic comparisons.
"""
from . import host, sim

MAX_BYTES = 8192  # 16-bit inclusive bit counter: 8*N-1 <= 65535.


def _length(length):
    if type(length) is not int or not 1 <= length <= MAX_BYTES:
        raise ValueError("length must be an integer in 1..8192 bytes")
    return length


def _width(word_bytes):
    if type(word_bytes) is not int or word_bytes not in (1, 4):
        raise ValueError("word_bytes must be 1 or 4")
    return word_bytes


def encode(payload, *, word_bytes=4, padding=0xD3):
    """Header and payload FIFO words. Nonzero padding exposes accidental tails."""
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise ValueError("payload must be bytes-like")
    payload = bytes(payload)
    length, width = _length(len(payload)), _width(word_bytes)
    if type(padding) is not int or not 0 <= padding <= 255:
        raise ValueError("padding must be a byte")
    words = [((8 * length - 1) << 16) | (length % width)]
    for offset in range(0, length, width):
        chunk = payload[offset:offset + width]
        chunk += bytes([padding]) * (width - len(chunk))
        words.append(int.from_bytes(chunk, "big") << (32 - 8 * width))
    return words


def decode(words, length, *, word_bytes=4):
    """Reject missing/phantom words and nonzero high bits in a partial RX word."""
    length, width = _length(length), _width(word_bytes)
    words = list(words)
    if len(words) != (length + width - 1) // width:
        raise ValueError("RX word count does not match exact message length")
    result = bytearray()
    for index, word in enumerate(words):
        size = min(width, length - index * width)
        if type(word) is not int or not 0 <= word < (1 << (8 * size)):
            raise ValueError("RX word is invalid or contains phantom tail bits")
        result.extend(word.to_bytes(size, "big"))
    return bytes(result)


def stage(core, words):
    """FIFO writes only; caller must ensure room and separately start the core."""
    if core != 0:
        raise ValueError("packed firmware reserves core0")
    words = list(words)
    if any(type(w) is not int or not 0 <= w < 2**32 for w in words):
        raise ValueError("FIFO words must be unsigned32 integers")
    return [(sim.core_reg(0, sim.C_FIFO), w) for w in words]


def host_sck_cost(length, *, word_bytes=4, burst_words=8):
    """Source wire accounting, not measured MCU/host-port throughput.

    Command8 single clocks, address16/4, read dummy8, word data32/8.
    Separate fixed-address TX/RX bursts; excludes status/CDC/software/gaps.
    """
    length, width = _length(length), _width(word_bytes)
    if type(burst_words) is not int or not 1 <= burst_words <= 8:
        raise ValueError("burst_words must be 1..8")
    rx = (length + width - 1) // width
    tx = rx + 1
    tb, rb = (tx + burst_words - 1) // burst_words, (rx + burst_words - 1) // burst_words
    return dict(tx_words=tx, rx_words=rx, tx_bursts=tb, rx_bursts=rb,
                single_sck=24 * tb + (24 + 8 * host.READ_DUMMY) * rb + 32 * (tx + rx),
                quad_sck=12 * tb + (12 + 8 * host.READ_DUMMY) * rb + 8 * (tx + rx),
                prestart_payload_capacity_bytes=7 * width)


def preload_plan(payload, *, core=0, mode=0, enabled_before=0, fifo_depth=8,
                 padding=0xD3):
    """Prepare one headerless mode0 frame of 1..32 bytes; perform no bus I/O.

    Load spi_master_preloaded_once first (32-bit shifts, synchronized MISO,
    integer divider >=3). Before applying the plan, verify CS idle, no fault
    and both FIFOs empty; stop any previous frame before disabling core0.
    Apply disable_writes, register_writes, then one FIXED-address payload
    burst, then start_writes. Never clear unexpected RX to make room.

    Restart clears shift state but retains R0/R1. After the caller's fixed
    wait, require HALT/no fault, TX empty and exactly rx_words available;
    drain exactly that count, verify empty STATUS and returned idle POUT.
    Repeat the whole plan to rearm, without reloading/resetting the chip.
    Other core enables are retained from caller-supplied enabled_before.
    This plan does not infer clock timing, completion or simulator state.
    """
    if type(core) is not int or core != 0:
        raise ValueError("preloaded firmware reserves core0")
    if type(mode) is not int or mode != 0:
        raise ValueError("preloaded firmware supports mode0 only")
    if type(enabled_before) is not int or not 0 <= enabled_before <= 3:
        raise ValueError("enabled_before must be the two-core enable mask")
    if type(fifo_depth) is not int or fifo_depth != 8:
        raise ValueError("preloaded firmware requires depth8 FIFOs")
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise ValueError("payload must be bytes-like")
    payload = bytes(payload)
    if not 1 <= len(payload) <= 32:
        raise ValueError("preloaded length must be 1..32 bytes")
    words = encode(payload, padding=padding)[1:]  # Count goes to actual CSRs.
    disabled = enabled_before & ~1
    return dict(length=len(payload), tx_words=words, rx_words=len(words),
                fifo_address=sim.core_reg(0, sim.C_FIFO),
                disable_writes=[(sim.A_CTRL, disabled)],
                register_writes=[(sim.core_reg(0, sim.C_R0), 8 * len(payload) - 1),
                                 (sim.core_reg(0, sim.C_R0 + 1), len(payload) % 4)],
                start_writes=host.start_writes(1, disabled),
                prestart_payload_capacity_bytes=32)
