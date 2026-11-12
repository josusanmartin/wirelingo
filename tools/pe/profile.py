"""Build profiles, and the one rule that picks a chip's geometry.

The ID register gives the core and bank counts (docs/isa.md §6.2); it does
not give the bank size or the FIFO depth, which are 256 words and 8 on every
build that has an ID (the v0.2 6x4 tapeout and the v0.3 builds).

The rule (resolve()): an explicit profile wins; otherwise, when the caller
has read a chip's ID, the geometry comes from that ID (for_id()); otherwise
(an offline tool, no chip) the default is TAPEOUT, the 2-core profile, and
PKG3 is selected by passing it (or `--profile pkg3` on the command lines).
"""
from dataclasses import dataclass

from .sim import ID_V2, known_id


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


TAPEOUT = Profile("fixed-bank-v1-2c2b8f", 2, 2)      # the 6x4 v0.2 tapeout (and the 8x4 fallback's geometry)
PKG3 = Profile("fixed-bank-v1-3c3b8f", 3, 3)         # the 8x4 package: three cores and banks (docs/isa.md §7)
FULL = Profile("fixed-bank-v1-4c4b8f", 4, 4)

PROFILES = {"tapeout": TAPEOUT, "pkg3": PKG3, "full": FULL}   # the names the command lines take
OFFLINE_DEFAULT = TAPEOUT     # no profile and no chip ID: today's default, so 2-core outputs stay as they were


def for_id(chip_id, bank_words=256, fifo_depth=8):
    """The Profile of the chip whose ID register reads `chip_id`: v0.2's ID
    (0x50450002, the 2-core 6x4 tapeout) or a v0.3 build's 0x5045BC03 (B
    banks, C cores, as sim.known_id). A named profile when one matches, else
    an unnamed one of that geometry. ValueError for any other value."""
    if isinstance(chip_id, bool) or not isinstance(chip_id, int) or not known_id(chip_id):
        shown = repr(chip_id) if isinstance(chip_id, bool) or not isinstance(chip_id, int) or chip_id < 0 else f"0x{chip_id:08X}"
        raise ValueError(f"chip ID {shown} is not a protoemu ID (v0.2 0x{ID_V2:08X} or a v0.3 0x5045BC03)")
    nc, nb = (2, 2) if chip_id == ID_V2 else ((chip_id >> 8) & 15, (chip_id >> 12) & 15)
    for p in PROFILES.values():
        if (p.ncores, p.nbanks, p.bank_words, p.fifo_depth) == (nc, nb, bank_words, fifo_depth):
            return p
    return Profile(f"id-{chip_id:08x}", nc, nb, bank_words, fifo_depth)


def resolve(profile=None, chip_id=None):
    """The profile a tool uses: `profile` if given (it wins, even over a chip
    ID); else the geometry of `chip_id` (for_id) if the caller read one; else
    OFFLINE_DEFAULT."""
    if profile is not None:
        return profile
    if chip_id is not None:
        return for_id(chip_id)
    return OFFLINE_DEFAULT


def validate_image(core, words, base, wrap, profile=None, data=(), *, chip_id=None):
    """Validate the whole image before a transport can perform any writes.
    The profile is resolve(profile, chip_id).

    Direct JMPs and CALLs must stay in the core's bank. Computed PC writes are checked
    by the chip at execution time and cannot be checked statically here.
    """
    profile = resolve(profile, chip_id)
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
    for i, word in enumerate(words):
        if not isinstance(word, int) or not 0 <= word <= 0xFFFFFFFF:
            raise ValueError("instruction must be a 32-bit word")
        if word >> 27 in (0, 13) and i not in data and not lo <= (word & 0x3FF) < hi:
            raise ValueError("JMP/CALL target leaves core's fixed instruction bank")
