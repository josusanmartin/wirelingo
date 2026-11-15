#!/usr/bin/env python3
"""Print, for each GDS given, the sha256 with its BGNLIB/BGNSTR timestamps zeroed
(the normalization of flow/build_manifest.py's normalized_gds_digest, copied
here), the first 16 hex of its raw sha256, and its run directory."""
import hashlib
import sys
from pathlib import Path

GDS_TIMESTAMPED = (0x0102, 0x0502)     # BGNLIB and BGNSTR: two 6-word dates each


def normalized_gds_digest(path):
    data = bytearray(Path(path).read_bytes())
    i = 0
    while i + 4 <= len(data):
        length = int.from_bytes(data[i:i + 2], "big")
        kind = int.from_bytes(data[i + 2:i + 4], "big")
        if length < 4:
            break
        if kind in GDS_TIMESTAMPED:
            data[i + 4:i + length] = bytes(length - 4)
        i += length
    return hashlib.sha256(data).hexdigest()


for p in sys.argv[1:]:
    raw = hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]
    print(f"{normalized_gds_digest(p)}  raw {raw}  {p.split('/runs/')[0]}")
