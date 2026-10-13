"""Explicit build profiles; the legacy ID does not encode bank/FIFO geometry."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    ncores: int
    nbanks: int
    bank_words: int = 256
    fifo_depth: int = 8

    def __post_init__(self):
        if not (1 <= self.nbanks <= self.ncores <= 4):
            raise ValueError("profile requires 1 <= nbanks <= ncores <= 4")
        if self.bank_words not in (16, 32, 64, 128, 256):
            raise ValueError("unsupported bank size")


TAPEOUT = Profile("fixed-bank-v1-2c2b8f", 2, 2)
FULL = Profile("fixed-bank-v1-4c4b8f", 4, 4)


def validate_image(core, words, base, wrap, profile=TAPEOUT):
    """Validate the whole image before a transport can perform any writes.

    Direct JMPs and CALLs must stay in the core's bank. Computed PC writes are checked
    by the chip at execution time and cannot be checked statically here.
    """
    if not isinstance(core, int) or not 0 <= core < min(profile.ncores, profile.nbanks):
        raise ValueError("core has no instruction bank in selected profile")
    if not words:
        raise ValueError("program must not be empty")
    # Bank selection is ALWAYS address[9:8], including reduced-AW simulations.
    lo = core * 256
    hi = lo + profile.bank_words
    if not isinstance(base, int) or not lo <= base < hi or base + len(words) > hi:
        raise ValueError("program exceeds core's fixed instruction bank")
    if not isinstance(wrap, int) or wrap < 0 or wrap >> 32:
        raise ValueError("invalid wrap encoding")
    bottom, top = wrap & 0xFFFF, wrap >> 16
    if not base <= bottom <= top < base + len(words):
        raise ValueError("wrap must lie inside the loaded image")
    for word in words:
        if not isinstance(word, int) or not 0 <= word <= 0xFFFFFFFF:
            raise ValueError("instruction must be a 32-bit word")
        if word >> 27 in (0, 13) and not lo <= (word & 0x3FF) < hi:
            raise ValueError("JMP/CALL target leaves core's fixed instruction bank")
