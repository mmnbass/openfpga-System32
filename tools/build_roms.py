#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""build_roms.py: make the ROM images of the Sega System 32 core for Analogue Pocket (mmnbass.System32) from your own MAME ROM sets.

    python3 build_roms.py --out <SD card>/Assets/segas32/common  <MAME set> [<MAME set> ...]

<MAME set> is a .zip file or a folder. Supported sets (MAME names):

  ga2, ga2u, ga2j              Golden Axe: The Revenge of Death Adder (World Rev B, US Rev A, Japan)
  spidman, spidmanu, spidmanj  Spider-Man: The Videogame (World, US Rev A, Japan)
  sonic, sonicp                SegaSonic The Hedgehog (Japan rev. C, Japan prototype)

Merged, split and non-merged sets all work: every ROM part is found by its CRC32, whatever the file is called, so give the tool every
zip you have for a game (a SPLIT regional set needs its parent zip as well: ga2.zip, spidman.zip).

For each game whose parts are all present the tool writes four images (main, tiles, sprites, sound) named as the core's Game files
expect (<set>_main.bin, <set>_tiles.bin, <set>_sprites.bin, <set>_sound.bin). Before writing it checks the length and the CRC32 of
every part's actual contents, and afterwards the SHA-256 of every finished image that has been tested on hardware (a set not yet
tested on hardware is built from the MAME set definition alone, and the tool says so). Nothing is written for a game unless all
four images match. Missing or wrong parts are listed by name, size and CRC32.

  --game <set>          build only this set   (default: every game whose parts are all present; an error if none is)
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
        "title": "Golden Axe: The Revenge of Death Adder (US, Rev A)",
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
        "title": "Spider-Man: The Videogame (World)",
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
GAMES["sonic"] = {
    "title": "SegaSonic The Hedgehog (Japan, rev. C)",
    "sets": "sonic",
    "parts": {
        0x25E3C27E: ("epr-15787c.ic17", 0x20000), 0xEFE9524C: ("epr-15786c.ic8", 0x20000),
        0x65B06C25: ("epr-15781c.ic18", 0x40000), 0x2DB66FD2: ("epr-15780c.ic9", 0x40000),
        0x0FE7422E: ("epr-15785.ic36", 0x40000), 0x42F06714: ("mpr-15784.ic35", 0x100000),
        0xE4220EEA: ("mpr-15783.ic34", 0x100000), 0xCF56B5A0: ("mpr-15782.ic33", 0x100000),
        0x4378F12B: ("mpr-15789.ic14", 0x100000), 0xA6ED5D7A: ("mpr-15788.ic5", 0x100000),
        0xC69D51B1: ("mpr-15790.ic32", 0x200000), 0x1006BB67: ("mpr-15792.ic30", 0x200000),
        0x8672B480: ("mpr-15794.ic28", 0x200000), 0x95B8011C: ("mpr-15796.ic26", 0x200000),
        0x42217066: ("mpr-15791.ic31", 0x100000), 0x75BAFE55: ("mpr-15793.ic29", 0x100000),
        0x7F3DAD30: ("mpr-15795.ic27", 0x100000), 0x013C6CAB: ("mpr-15797.ic25", 0x100000),
    },
    "images": {
        "main": (_rep(("part", 0x25E3C27E), 4) + _rep(("part", 0xEFE9524C), 4)
                 + _rep(("inter", 16, [(0x65B06C25, "01"), (0x2DB66FD2, "10")]), 2),
                 0x200000, "80de6ab3f66e400f207d2cf120b364ae9b9469eead3c774e674b71861498ff60"),
        "tiles": ([("inter", 16, [(0x4378F12B, "01"), (0xA6ED5D7A, "10")]), ("fill", 0x200000, 0xFF)],
                  0x400000, "2c743d749f47298014b66ad755f2a07acd07c2789f8cb1b61b65655ed1fab9ef"),
        "sprites": ([("inter", 64, [(0xC69D51B1, "00000021"), (0x1006BB67, "00002100"), (0x8672B480, "00210000"), (0x95B8011C, "21000000")]),
                     ("inter", 64, [(0x42217066, "00000021"), (0x75BAFE55, "00002100"), (0x7F3DAD30, "00210000"), (0x013C6CAB, "21000000")]),
                     ("fill", 0x400000, 0xFF)],
                    0x1000000, "c98f4d6afb292919f7fcbac1682829a19a6c40ebd4219e620c6db713348cda4d"),
        "sound": (_rep(("part", 0x0FE7422E), 4) + [("part", 0x42F06714), ("part", 0xE4220EEA), ("part", 0xCF56B5A0)],
                  0x400000, "1c38973d757e0c1a1cc1efeef5a3fb366bd5b52f5e87c27b204cafbd4586d982"),
    },
}
# The prototype: every ROM but one differs; tile and sprite ROMs are byte-wide (MAME: ROM_LOAD32_BYTE at offsets 0, 2, 1, 3 and
# ROM_LOAD64_BYTE at 0-7), 2 MB of tiles (the image's upper half is filled FF, as in the retail set) and 8 MB of sprites.
# Its SHA-256 values are the images tested on hardware with core 0.2.0.
GAMES["sonicp"] = {
    "title": "SegaSonic The Hedgehog (Japan, prototype)",
    "sets": "sonicp",
    "parts": {
        0xDA05DCBB: ("sonpg0.bin", 0x20000), 0xC57DC5C5: ("sonpg1.bin", 0x20000),
        0xA7DA7546: ("sonpd0.bin", 0x40000), 0xC30E4C70: ("sonpd1.bin", 0x40000),
        0x569C8D4B: ("sonsnd0.bin", 0x40000), 0xF4FA5A21: ("sonsnd1.bin", 0x100000),
        0xE1BD45A5: ("sonsnd2.bin", 0x100000), 0xCF56B5A0: ("sonsnd3.bin", 0x100000),
        0x445E31B9: ("sonscl0.bin", 0x80000), 0x3D234181: ("sonscl1.bin", 0x80000),
        0xA5DE28B2: ("sonscl2.bin", 0x80000), 0x7CE7554B: ("sonscl3.bin", 0x80000),
        0xCEEA18E3: ("sonobj0.bin", 0x100000), 0x6BBC226B: ("sonobj1.bin", 0x100000),
        0xFCD5EF0E: ("sonobj2.bin", 0x100000), 0xB99B42AB: ("sonobj3.bin", 0x100000),
        0xC7EC1456: ("sonobj4.bin", 0x100000), 0xBD5DA27F: ("sonobj5.bin", 0x100000),
        0x313C92D1: ("sonobj6.bin", 0x100000), 0x3784C507: ("sonobj7.bin", 0x100000),
    },
    "images": {
        "main": (_rep(("part", 0xDA05DCBB), 4) + _rep(("part", 0xC57DC5C5), 4)
                 + _rep(("inter", 16, [(0xA7DA7546, "01"), (0xC30E4C70, "10")]), 2), 0x200000, "e8f35ebe3c8934185d303ffca86a35bf5b90ba324365ba8837c7172b8ce262e3"),
        "tiles": ([("inter", 32, [(0x445E31B9, "0001"), (0xA5DE28B2, "0010"), (0x3D234181, "0100"), (0x7CE7554B, "1000")]),
                   ("fill", 0x200000, 0xFF)], 0x400000, "04aa47d8f4cbb5868220cd8039d2a6ee6fbc437d2b3d1822f6fd8ff1bd9ab1ec"),
        "sprites": ([("inter", 64, [(0xCEEA18E3, "00000001"), (0x6BBC226B, "00000010"), (0xFCD5EF0E, "00000100"), (0xB99B42AB, "00001000"),
                                    (0xC7EC1456, "00010000"), (0xBD5DA27F, "00100000"), (0x313C92D1, "01000000"), (0x3784C507, "10000000")])],
                    0x800000, "4dd0d687ae4d70cb1ca361d28760b26296bc1fa125438d3f40cc2e25a67f5de9"),
        "sound": (_rep(("part", 0x569C8D4B), 4) + [("part", 0xF4FA5A21), ("part", 0xE1BD45A5), ("part", 0xCF56B5A0)], 0x400000, "e2b3f6ec21ab9a6c1ae16e5a7c6a0f89b4992ba1235bc3e146ebe5892907fc41"),
    },
}
ORDER = ("main", "tiles", "sprites", "sound")


def _clone(parent, name, title, swap, main_sha):
    """A regional set: the parent's layout with its program ROM parts replaced (swap = {parent crc: (crc, MAME name)}). Its tile, sprite
    and sound images are the parent's (same parts, same tested SHA-256); main_sha = the SHA-256 of its main image as tested on
    hardware (Pocket, sn3 build, 2026-11-24)."""
    p = GAMES[parent]
    def sub(x):
        if isinstance(x, tuple): return tuple(sub(y) for y in x)
        if isinstance(x, list): return [sub(y) for y in x]
        return swap[x][0] if isinstance(x, int) and x in swap else x
    parts = {(swap[c][0] if c in swap else c): ((swap[c][1], sz) if c in swap else (n, sz)) for c, (n, sz) in p["parts"].items()}
    images = dict(p["images"]); steps, size, _ = p["images"]["main"]; images["main"] = (sub(steps), size, main_sha)
    GAMES[name] = {"title": title, "sets": f"{name} (a split set also needs its parent)", "parts": parts, "images": images}


_clone("ga2u", "ga2", "Golden Axe: The Revenge of Death Adder (World, Rev B)",
       {0x87182FEA: (0xD9CD8885, "epr-14961b.ic17"), 0xAB787CF4: (0x0BE324A3, "epr-14958b.ic8"),
        0x7293D5C3: (0xC477A9FD, "epr-15148b.ic18"), 0x0DA61782: (0x1BB676EA, "epr-15147b.ic9")}, "871d43dff2276de15b920452c37fd87312dbe285e3d6db7c15a340593e39c2e9")
_clone("ga2u", "ga2j", "Golden Axe: The Revenge of Death Adder (Japan)",
       {0x87182FEA: (0xF1929177, "epr-14959.ic17"), 0xAB787CF4: (0xEACAFE94, "epr-14946.ic8"),
        0x7293D5C3: (0x0FFB8203, "epr-14941.ic18"), 0x0DA61782: (0x3B5B3084, "epr-14940.ic9")}, "48dc1f01b28b3c79fad8c3c2c011777217804a19292cf59650484076ea6438c2")
_clone("spidman", "spidmanu", "Spider-Man: The Videogame (US, Rev A)",
       {0xD900219C: (0x7F1BD28F, "epr-14303a.ic13"), 0x64379DC6: (0xD954C40A, "epr-14302a.ic7")}, "eff4e531e2b8ac02a9299710ddcc9efd81c1984ae5abae319316f842b7c7d874")
_clone("spidman", "spidmanj", "Spider-Man: The Videogame (Japan)",
       {0xD900219C: (0x403CCDC9, "epr-14287.ic13"), 0x64379DC6: (0x5C2B4E2C, "epr-14286.ic7")}, "1e891489743ce5613ee3d9347656a369d32082f538b25e314b774d599f216385")


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
            b += (found[st[1]][0] if st[0] == "part" else bytes([st[2]]) * st[1] if st[0] == "fill"
                  else interleave(st[1], [(found[c][0], m) for c, m in st[2]]))
        if len(b) != size or (sha is not None and hashlib.sha256(b).hexdigest() != sha):
            wrong.append(f"  {game}_{kind}.bin: the finished image does not match the tested image (sha256 {hashlib.sha256(b).hexdigest()})")
        imgs[kind] = bytes(b)
    return (None if wrong else imgs), missing, wrong


def main(argv=None):
    ap = argparse.ArgumentParser(description="Make the Sega System 32 (mmnbass.System32) ROM images from your own MAME ROM sets.")
    ap.add_argument("sources", nargs="+", help="MAME set .zip files or folders (any of: " + ", ".join(sorted(GAMES)) + ")")
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
            own = [c for c in g["parts"] if not any(c in o["parts"] for n, o in GAMES.items() if n != game)]
            if not a.game and not any(c in found for c in own):
                continue                                       # none of this set's own parts was given (at most parts it shares): skip it quietly
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
            if t.read_bytes() != imgs[k]:
                sys.exit(f"error: {t} did not read back correctly")
        done.append(game)
        if g.get("untested"):
            print(f"  note: {game} is built from the MAME set definition; its main image has no hardware-tested SHA-256 yet "
                  f"(sha256 {hashlib.sha256(imgs['main']).hexdigest()})")
        print(f"{g['title']}: written and verified: " + ", ".join(f"{t.name} ({len(imgs[k]):,} bytes)" for k, t in zip(ORDER, targets)))
    if not done and rc == 0:
        print("No game built: none of the given files contains a ROM part of a supported set (" + ", ".join(sorted(GAMES)) + ").")
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
