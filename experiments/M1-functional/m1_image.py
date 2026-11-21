#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write a program image as .bin and as the 16-bit little-endian $readmemh
file the benches load (the same packing upstream's gen_diff_program.py uses).

  m1_image.py random <seed> <out-prefix>    image from upstream gen() (unmodified)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "ref"))


def write(img: bytes, prefix: str):
    Path(prefix + ".bin").write_bytes(img)
    words = len(img) // 2 + 1
    padded = img + b"\x00" * (words * 2 - len(img))
    with open(prefix + ".hex", "w") as f:
        for i in range(words):
            f.write("%04x\n" % (padded[2 * i] | (padded[2 * i + 1] << 8)))


def main():
    if sys.argv[1] == "random":
        from gen_diff_program import gen  # noqa: E402  (upstream generator, unmodified)
        write(gen(int(sys.argv[2])), sys.argv[3])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
