"""Plans and strict word decoding for the optional core0 declared UART capture.

Load/configure uart_rx_capture.pasm separately while core0 is disabled.
Before arming/rearming, require core0 stopped/disabled and both its FIFOs
empty. This module does not clear FIFO leftovers or infer completion.
F0 is owned by capture; an independently running peer must not use it.
"""
from . import sim


def _length(length):
    if type(length) is not int or not 1 <= length <= 32:
        raise ValueError("capture length must be an integer in 1..32")
    return length


def capture_plan(length, *, core=0, enabled_before=0):
    """Return five ordinary writes, validated before any plan is produced.

    Caller observes stopped/disabled core0 and empty RX/TX FIFOs first.
    enabled_before is the current tapeout enable mask (0..3). Preserve the
    peer enable, pay R1/R2 setup, clear only F0, restart/enable only core0.
    No program/configuration/FIFO/other-core register writes are returned.
    """
    length = _length(length)
    if type(core) is not int or core != 0:
        raise ValueError("declared UART capture supports only core0")
    if type(enabled_before) is not int or not 0 <= enabled_before <= 3:
        raise ValueError("enabled_before must be a two-core enable mask in 0..3")
    peer = enabled_before & 2
    return [(sim.A_CTRL, peer),
            (sim.core_reg(0, sim.C_R0 + 1), length),
            (sim.core_reg(0, sim.C_R0 + 2), length % 4),
            (sim.A_FCLR, 1),
            (sim.A_CTRL, peer | 1 | (1 << 8))]


def decode_capture(length, words):
    """Decode exactly ceil(length/4) uint32 words; reject partial low padding.

    Full words are little endian; the final partial word is high aligned.
    This validates representation, not completion or framing: first read
    HALT/no fault, exact RX occupancy and F0 through the ordinary host API.
    Valid-looking corrupted data cannot be detected without a payload oracle.
    """
    length = _length(length)
    try:
        words = tuple(words)
    except TypeError as exc:
        raise ValueError("capture words must be iterable uint32 values") from exc
    if len(words) != (length + 3) // 4:
        raise ValueError("capture word count does not match declared length")
    if any(type(word) is not int or not 0 <= word <= 0xFFFFFFFF for word in words):
        raise ValueError("capture words must be uint32 integers")
    full, tail = divmod(length, 4)
    result = b"".join(word.to_bytes(4, "little") for word in words[:full])
    if tail:
        padding = 8 * (4 - tail)
        if words[-1] & ((1 << padding) - 1):
            raise ValueError("nonzero padding in high-aligned partial word")
        result += (words[-1] >> padding).to_bytes(tail, "little")
    return result
