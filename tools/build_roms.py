#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""build_roms.py: make the ROM images of the Sega System 32 core for Analogue Pocket (mmnbass.System32) from your own MAME ROM sets.

    python3 build_roms.py --out <SD card>/Assets/segas32/common  <MAME set> [<MAME set> ...]

<MAME set> is a .zip file or a folder: ga2u (Golden Axe: The Revenge of Death Adder, US) and/or spidman (Spider-Man: The Video Game,
World). Merged, split and non-merged sets all work: every ROM part is found by its CRC32, whatever the file is called, so give the
tool every zip you have for a game (a SPLIT ga2u set needs its parent ga2.zip as well).

For each game whose parts are all present the tool writes four images (main, tiles, sprites, sound) named as the core's Game files
expect (ga2u_main.bin ... spidman_sound.bin). Before writing it checks the length and the CRC32 of every part's actual contents, and
afterwards the SHA-256 of every finished image against the image that was tested on hardware. Nothing is written for a game unless all
four images match. Missing or wrong parts are listed by name, size and CRC32.

  --game ga2u|spidman   build only this game (default: every game whose parts are all present; an error if none is)
  --force               replace existing image files in --out (default: refuse)

Layouts: the upstream core's MRA files (references/s32 releases/: Golden Axe ... (US, Rev A).mra and Spider-Man The Videogame.mra),
rom index 4 = main, 6 = tiles, 9 = sprites, 5 = sound. Exit status: 0 = all requested images written and verified, 1 = parts missing or
wrong, 2 = usage. Needs only Python 3.8+. No ROM data is contained in this file.
"""
import argparse
import hashlib
import os
import sys
import zipfile
import zlib
from pathlib import Path

# A layout is a list of steps, concatenated: ("part", crc) appends that part whole; ("inter", width, [(crc, map), ...]) interleaves parts
# MRA-style: output words of `width` bits; in a map, the digit at position i from the RIGHT is the part's byte (digit - 1) that goes to
# output byte i (0 = no byte from this part).
def _rep(step, n):
    return [step] * n

GAMES = {
    "ga2u": {
        "title": "Golden Axe: The Revenge of Death Adder (US)",
        "sets": "ga2u (a split set also needs its parent ga2)",
        "parts": {  # crc: (MAME name, size)
            0x87182FEA: ("epr-14960a.ic17", 0x20000), 0xAB787CF4: ("epr-14957a.ic8", 0x20000),
            0x7293D5C3: ("epr-15146a.ic18", 0x40000), 0x0DA61782: ("epr-15145a.ic9", 0x40000),
            0x4781D4CB: ("epr-14945.ic36", 0x10000), 0xFD4D4B86: ("mpr-14944.ic35", 0x100000),
            0x24D40333: ("mpr-14943.ic34", 0x100000), 0xA89B0E90: ("mpr-14942.ic24", 0x100000),
            0x75050D4A: ("mpr-14948.ic14", 0x200000), 0xB53E62F4: ("mpr-14947.ic5", 0x200000),
            0x152C716C: ("mpr-14949.ic32", 0x200000), 0xFDB1A534: ("mpr-14951.ic30", 0x200000),
            0x33BD1C15: ("mpr-14953.ic28", 0x200000), 0xE42684AA: ("mpr-14955.ic26", 0x200000),
            0x15FD0026: ("mpr-14950.ic31", 0x200000), 0x96F96613: ("mpr-14952.ic29", 0x200000),
            0x39B2AC9E: ("mpr-14954.ic27", 0x200000), 0x298FCA50: ("mpr-14956.ic25", 0x200000),
        },
        "images": {
            "main": (_rep(("part", 0x87182FEA), 4) + _rep(("part", 0xAB787CF4), 4)
                     + _rep(("inter", 16, [(0x7293D5C3, "01"), (0x0DA61782, "10")]), 2),
                     0x200000, "72b2bbc22b3df2d15f9fca9626e0e6cf2c34f79c5a4d15f0718938bedcec2dfa"),
            "tiles": ([("inter", 16, [(0x75050D4A, "01"), (0xB53E62F4, "10")])],
                      0x400000, "55f80c6e7675826dbb74e470e319672ab46fed98839ad6fd6a2377da8a0242d5"),
            "sprites": ([("inter", 64, [(0x152C716C, "00000021"), (0xFDB1A534, "00002100"), (0x33BD1C15, "00210000"), (0xE42684AA, "21000000")]),
                         ("inter", 64, [(0x15FD0026, "00000021"), (0x96F96613, "00002100"), (0x39B2AC9E, "00210000"), (0x298FCA50, "21000000")])],
                        0x1000000, "b21be8a2f47aab2f3a253ea68a96478e5bb43dec8715cd8ce4b3ed52836ca35b"),
            "sound": (_rep(("part", 0x4781D4CB), 16) + [("part", 0xFD4D4B86), ("part", 0x24D40333), ("part", 0xA89B0E90)],
                      0x400000, "b19dffa4b9376952df5f10f59f40958fb58a2aa05238438b4933607b634b4268"),
        },
    },
    "spidman": {
        "title": "Spider-Man: The Video Game (World)",
        "sets": "spidman",
        "parts": {
            0xD900219C: ("epr-14307.ic13", 0x20000), 0x64379DC6: ("epr-14306.ic7", 0x20000),
            0x8A746C42: ("epr-14281.ic14", 0x20000), 0x3C8148F7: ("epr-14280.ic6", 0x20000),
            0x25AEFAD6: ("epr-14285.ic35", 0x40000), 0x760542D4: ("mpr-14284.ic31", 0x80000),
            0xC863A91C: ("mpr-14283.ic26", 0x80000), 0xEA20979E: ("mpr-14282.ic22", 0x80000),
            0x490F95A1: ("mpr-14291-s.ic38", 0x100000), 0x38570582: ("mpr-14289-s.ic29", 0x100000),
            0xA144162D: ("mpr-14290-s.ic34", 0x100000), 0x3188B636: ("mpr-14288-s.ic25", 0x100000),
            0xCE59231B: ("mpr-14299-h.ic36", 0x100000), 0x2745C84C: ("mpr-14298-h.ic32", 0x100000),
            0x29CB9450: ("mpr-14297-h.ic27", 0x100000), 0x9D8CBD31: ("mpr-14296-h.ic23", 0x100000),
            0x29591F50: ("mpr-14295-h.ic37", 0x100000), 0xFA86B794: ("mpr-14294-h.ic33", 0x100000),
            0x52899269: ("mpr-14293-s.ic28", 0x100000), 0x41F71443: ("mpr-14292-s.ic24", 0x100000),
        },
        "images": {
            "main": (_rep(("part", 0xD900219C), 4) + _rep(("part", 0x64379DC6), 4)
                     + _rep(("inter", 16, [(0x8A746C42, "01"), (0x3C8148F7, "10")]), 4),
                     0x200000, "a0211f216c7380e6a40580745625eaea06cad2ff033fd7a02704074d719d6db9"),
            "tiles": ([("inter", 32, [(0x490F95A1, "0001"), (0x38570582, "0010"), (0xA144162D, "0100"), (0x3188B636, "1000")])],
                      0x400000, "768d4fe9e2e57df3912d8950d979a8e74b724f1db38f6bec798993888733f5dd"),
            "sprites": ([("inter", 64, [(0xCE59231B, "00000001"), (0x2745C84C, "00000010"), (0x29CB9450, "00000100"), (0x9D8CBD31, "00001000"),
                                        (0x29591F50, "00010000"), (0xFA86B794, "00100000"), (0x52899269, "01000000"), (0x41F71443, "10000000")])],
                        0x800000, "72746d1c815e81fd1d3ab62605fa3250e395cedc1d2647ac3d47a2821ce7adeb"),
            "sound": (_rep(("part", 0x25AEFAD6), 4) + _rep(("part", 0x760542D4), 2) + _rep(("part", 0xC863A91C), 2) + _rep(("part", 0xEA20979E), 2),
                      0x400000, "e9ab7793764fad5fa03a17b7801a2cd415b516cb6ad4db0f91df1e2c048536dc"),
        },
    },
}
ORDER = ("main", "tiles", "sprites", "sound")


def scan(sources):
    """{crc32 of the actual bytes: (bytes, where)} for every file in the given zips / folders (zips inside folders too)."""
    found = {}
    def add(data, where):
        found.setdefault(zlib.crc32(data) & 0xFFFFFFFF, (data, where))
    def zip_(path):
        with zipfile.ZipFile(path) as z:
            for i in z.infolist():
                if not i.is_dir():
                    try:
                        add(z.read(i), f"{path}:{i.filename}")
                    except (zipfile.BadZipFile, zlib.error, RuntimeError) as e:
                        print(f"warning: cannot read {path}:{i.filename}: {e}", file=sys.stderr)
    for s in sources:
        p = Path(s).expanduser()
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    if zipfile.is_zipfile(f):
                        zip_(f)
                    else:
                        add(f.read_bytes(), str(f))
        elif p.is_file() and zipfile.is_zipfile(p):
            zip_(p)
        elif p.is_file():
            add(p.read_bytes(), str(p))
        else:
            sys.exit(f"error: {s}: no such file or folder")
    return found


def interleave(width, parts):
    n = width // 8
    srcs = []
    for data, m in parts:
        m = m.rjust(n, "0")
        k = sum(c != "0" for c in m)
        srcs.append((data, k, [(i, int(c) - 1) for i, c in enumerate(reversed(m)) if c != "0"]))
    words = {len(d) // k for d, k, _ in srcs}
    if len(words) != 1:
        raise ValueError("interleaved parts of different lengths")
    w = words.pop()
    out = bytearray(w * n)
    for data, k, pairs in srcs:
        for ob, pb in pairs:
            out[ob::n] = data[pb::k]
    return bytes(out)


def build(game, found):
    g = GAMES[game]
    missing, wrong = [], []
    for crc, (name, size) in g["parts"].items():
        if crc not in found:
            missing.append(f"  {name}  ({size:,} bytes, CRC32 {crc:08x})")
        elif len(found[crc][0]) != size:
            wrong.append(f"  {name}: {found[crc][1]} has {len(found[crc][0]):,} bytes, expected {size:,}")
    if missing or wrong:
        return None, missing, wrong
    imgs = {}
    for kind in ORDER:
        steps, size, sha = g["images"][kind]
        b = bytearray()
        for st in steps:
            b += found[st[1]][0] if st[0] == "part" else interleave(st[1], [(found[c][0], m) for c, m in st[2]])
        if len(b) != size or hashlib.sha256(b).hexdigest() != sha:
            wrong.append(f"  {game}_{kind}.bin: the finished image does not match the tested image (sha256 {hashlib.sha256(b).hexdigest()})")
        imgs[kind] = bytes(b)
    return (None if wrong else imgs), missing, wrong


def main(argv=None):
    ap = argparse.ArgumentParser(description="Make the Sega System 32 (mmnbass.System32) ROM images from your own MAME ROM sets.")
    ap.add_argument("sources", nargs="+", help="MAME set .zip files or folders (ga2u [+ ga2], spidman)")
    ap.add_argument("--out", required=True, help="output folder, normally <SD card>/Assets/segas32/common")
    ap.add_argument("--game", choices=sorted(GAMES), help="build only this game")
    ap.add_argument("--force", action="store_true", help="replace existing image files")
    a = ap.parse_args(argv)
    found = scan(a.sources)
    games = [a.game] if a.game else list(GAMES)
    out = Path(a.out).expanduser()
    done, rc = [], 0
    for game in games:
        g = GAMES[game]
        imgs, missing, wrong = build(game, found)
        have = sum(c in found for c in g["parts"])
        if imgs is None:
            if not a.game and have == 0:
                continue                                       # this game's set was not given: skip it quietly
            rc = 1
            print(f"{g['title']}: NOT built ({have} of {len(g['parts'])} parts found; MAME set {g['sets']})")
            if missing:
                print(" missing parts:\n" + "\n".join(missing))
            if wrong:
                print(" wrong parts:\n" + "\n".join(wrong))
            continue
        out.mkdir(parents=True, exist_ok=True)
        targets = [out / f"{game}_{k}.bin" for k in ORDER]
        clash = [t for t in targets if t.exists()]
        if clash and not a.force:
            print(f"{g['title']}: NOT written: {', '.join(t.name for t in clash)} already present in {out} (use --force to replace them)")
            rc = 1
            continue
        for k, t in zip(ORDER, targets):
            tmp = t.with_name(t.name + ".tmp")
            tmp.write_bytes(imgs[k])
            os.replace(tmp, t)
            if hashlib.sha256(t.read_bytes()).hexdigest() != g["images"][k][2]:
                sys.exit(f"error: {t} did not read back correctly")
        done.append(game)
        print(f"{g['title']}: written and verified: " + ", ".join(f"{t.name} ({len(imgs[k]):,} bytes)" for k, t in zip(ORDER, targets)))
    if not done and rc == 0:
        print("No game built: none of the given files contains a ga2u or spidman ROM part.")
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
