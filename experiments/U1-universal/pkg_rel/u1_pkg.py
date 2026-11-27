#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""release/s32-v1 package (pkg_rel; copy of U1b's pkg with the release identity and the sound ROM slot): the per-game files of the one System32 core, made from experiments/U1-universal/games/<set>.json.
No ROM data: an instance JSON names files the user supplies; a configuration file holds the descriptor, input profile and checksums.

  u1_pkg.py write      writes pkg/Assets/s32univ/mmnbass.S32Univ/<set>.json for every game (the committed instance files)
  u1_pkg.py check      the committed instance files equal what `write` would produce, and there is one per game, nothing else
  u1_pkg.py cfg <dir>  writes <dir>/<set>_u1cfg.bin for every game (u1_cfg.make; package_rel.sh stages them in Assets/s32univ/common)

Instance JSON (Analogue "instance json", fetched 2026-11-14; the pattern of a released multi-game core, TheDiscordian/openfpga-pacman:
data slot 0 "Game" with parameters 0x113 and extension json, ROM slots without filenames, instance files in the core folder,
data_path ""): magic APF_VER_1, data_path "" (the named files are found as each data slot would find them: data.json's ROM,
EEPROM and configuration slots are not core-specific, so Assets/s32univ/common), core_select id 0 / select false (one bitstream),
data_slots = ids 1, 2, 3, 7 (<set>_main.bin, <set>_tiles.bin, <set>_sprites.bin, <set>_sound.bin), 6 (<set>_u1cfg.bin) and, where the game has one,
4 (its default-EEPROM file); no memory_writes. The save (slot 10) is never named here: APF names it after slot 0's file."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
U1 = HERE.parent
sys.path.insert(0, str(U1))
import u1_cfg  # noqa: E402

import os
PLATFORM, CORE = os.environ.get("PKG_PLATFORM", "segas32"), os.environ.get("PKG_CORE", "mmnbass.System32")   # SN: overridable (package_sn.sh)
INST_DIR = Path(os.environ.get("PKG_TEMPLATES", HERE)) / "Assets" / PLATFORM / CORE
EEPROM_DEFAULT = {"spidman": "spidman_eeprom.bin", "spidmanu": "spidmanu_eeprom.bin", "spidmanj": "spidmanj_eeprom.bin"}          # SpidPlay1's optional factory-default image; GA2 has none


def rom_files(s):
    return {1: f"{s}_main.bin", 2: f"{s}_tiles.bin", 3: f"{s}_sprites.bin", 7: f"{s}_sound.bin"}   # rel: + the sound ROM (slot id 7)


def cfg_name(s):
    return f"{s}_u1cfg.bin"


def instance(s):
    slots = [{"id": i, "filename": f} for i, f in rom_files(s).items()]
    if s in EEPROM_DEFAULT:
        slots.append({"id": 4, "filename": EEPROM_DEFAULT[s]})
    slots.append({"id": 6, "filename": cfg_name(s)})
    return {"instance": {"magic": "APF_VER_1", "data_path": "", "core_select": {"id": 0, "select": False}, "data_slots": slots}}


def text(s):
    return json.dumps(instance(s), indent=2) + "\n"


def sets():
    return [p.stem for p in u1_cfg.games()]


def inst_name(s):   # the instance file of set s: its full title (u1_cfg.instance_stem)
    return u1_cfg.instance_stem(json.loads((U1 / "games" / f"{s}.json").read_text())) + ".json"


def main(a):
    if a and a[0] == "write":
        INST_DIR.mkdir(parents=True, exist_ok=True)
        for s in sets():
            (INST_DIR / inst_name(s)).write_text(text(s)); print(f"wrote {INST_DIR / inst_name(s)}")
        return 0
    if a and a[0] == "check":
        bad = 0
        have = sorted(p.name for p in INST_DIR.iterdir()) if INST_DIR.is_dir() else []
        want = sorted(inst_name(s) for s in sets())
        if have != want:
            bad += 1; print(f"FAIL instance files {have} != {want}")
        for s in sets():
            p = INST_DIR / inst_name(s)
            ok = p.is_file() and p.read_text() == text(s); bad += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {p.relative_to(INST_DIR.parents[2])} == generated")
        print("U1 PKG CHECK", "PASS" if not bad else f"FAIL ({bad})"); return 1 if bad else 0
    if len(a) == 2 and a[0] == "cfg":
        d = Path(a[1]); d.mkdir(parents=True, exist_ok=True)
        for p in u1_cfg.games():
            (d / cfg_name(p.stem)).write_bytes(u1_cfg.make(json.loads(p.read_text())))
        print(f"{len(sets())} configuration files -> {d}"); return 0
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
