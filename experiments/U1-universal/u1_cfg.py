#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""U1 configuration files: one 64-byte file per game (format version 1, see rtl/u1_board_cfg.sv), made from games/<set>.json.
No ROM data: a game file holds the board descriptor (verbatim from the pinned upstream MRA), the input-wiring profile and the
expected slot checksums (sums, already committed in the GA2Play8 / SpidPlay1 tops).

  u1_cfg.py make <game.json> <out.bin>            the valid configuration file
  u1_cfg.py neg <kind> <game.json> <out.bin>      a negative-control file (kinds below)
  u1_cfg.py decode <file.bin>                     the reference decode (the same checks as u1_board_cfg, in Python)
  u1_cfg.py check-games                           every games/*.json: descriptor == its MRA's rom index 0 bytes 0-15;
                                                  the file decodes ok; every negative control decodes to its expected reason
  u1_cfg.py vectors <out.hex>                     unit-test vectors for tb/tb_u1_board_cfg.sv (all games x all kinds)
  u1_cfg.py check-files [manifest] [file ...]     U1b configuration-file identity: every games/*.json, made into its 64-byte file, has the
                                                  sha256 the committed manifest (default games/cfg_files.sha256) records for <set>.cfg, the
                                                  manifest names exactly those games, and each given file (e.g. the files the simulations
                                                  loaded, or a package's) equals the made file of the game its name starts with
Completeness (U1b review finding 1): u1_board_cfg declares a file complete when every one of the 32 words has been WRITTEN over the
bridge. APF writes whole 32-bit words, so a 61-63-byte file reaches the core as 64 bytes (its last word padded by APF [U]) and is judged
on those 64 bytes: it can never pass with an invalid configuration, but it is not reported as reason 1. `decode` models the file as
given (a short file is reason 1); `bridge` models what the core sees (zero padding assumed); check-games reports every truncation of
each game's file at the bridge (all must be rejected). The package's configuration slot sets size_exact 0x40, so APF loads only a
64-byte file. A size check in the RTL itself (the slot-6 requestwrite size) is deferred (U1B.md section 7).
Negative-control kinds (expected reason): short (1, first 32 bytes only), badmagic (2), badversion (2), badsum (3, one descriptor
byte changed, check word not fixed), prot_sonic (4: prot_sel = 7, check word fixed), trackball (4: analog_profile = trackball),
multi32 (4), v25_table (4), sprmask2 (4: sprite field 0x82), profile2 (5: input profile 2), reserved (5: reserved word 3 != 0).
"""
import json, re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GAMES = HERE / "games"
MAGIC = b"S32C"
VERSION = 1
REASONS = {0: "ok", 1: "incomplete", 2: "bad magic/version", 3: "bad check word", 4: "board feature outside the compiled scope",
           5: "bad input profile / reserved"}


def words(b):
    return [b[2 * i] | (b[2 * i + 1] << 8) for i in range(len(b) // 2)]


def fix_check(b):
    b = bytearray(b); b[62:64] = b"\0\0"
    s = sum(words(bytes(b))) & 0xFFFF
    c = (-s) & 0xFFFF; b[62] = c & 0xFF; b[63] = c >> 8
    return bytes(b)


def make(g):
    d = bytes.fromhex(g["descriptor"]); assert len(d) == 16
    b = bytearray(64)
    b[0:4] = MAGIC; b[4] = VERSION
    b[8:24] = d
    b[24] = g["input_profile"]
    s = g["expected_sums"]
    for i, k in enumerate(("sum0", "fl0", "sum1", "fl1", "sum2", "fl2")):
        b[32 + 4 * i:36 + 4 * i] = int(s[k], 16).to_bytes(4, "little")
    return fix_check(bytes(b))


NEG = {   # kind -> (transform, expected reason)
    "short": (lambda b: b[:32], 1),
    "badmagic": (lambda b: fix_check(b"S32X" + b[4:]), 2),
    "badversion": (lambda b: fix_check(b[:4] + bytes([2]) + b[5:]), 2),
    "badsum": (lambda b: b[:9] + bytes([b[9] ^ 0x01]) + b[10:], 3),
    "prot_sonic": (lambda b: fix_check(b[:10] + bytes([0x07]) + b[11:]), 4),
    "trackball": (lambda b: fix_check(b[:9] + bytes([0x30]) + b[10:]), 4),
    "multi32": (lambda b: fix_check(b[:8] + bytes([b[8] | 0x01]) + b[9:]), 4),
    "v25_table": (lambda b: fix_check(b[:8] + bytes([b[8] | 0x04]) + b[9:]), 4),
    "sprmask2": (lambda b: fix_check(b[:11] + bytes([0x82]) + b[12:]), 4),
    "profile2": (lambda b: fix_check(b[:24] + bytes([2]) + b[25:]), 5),
    "reserved": (lambda b: fix_check(b[:6] + bytes([1]) + b[7:]), 5),
}


def decode(b):
    """Reference model of u1_board_cfg (same checks, same order). Returns (reason, fields)."""
    n = len(b) // 2
    w = words(b[:64]) + [0] * max(0, 32 - n)
    complete = len(b) == 64
    csum = sum(words(b[:64])) & 0xFFFF
    d = b[8:24] if len(b) >= 24 else bytes(16)
    magic_ok = w[0] == 0x3353 and w[1] == 0x4332 and w[2] == VERSION
    scope_ok = (d[0] & ~0x22) & 0xFF == 0 and d[1] == 0 and d[2] == 0 and d[3] in (0x80, 0x81, 0x83) and all(x == 0 for x in d[4:16])
    rest_ok = (w[12] >> 1) == 0 and w[3] == 0 and w[13] == w[14] == w[15] == 0 and w[28] == w[29] == w[30] == 0
    reason = 1 if not complete else 2 if not magic_ok else 3 if csum != 0 else 4 if not scope_ok else 5 if not rest_ok else 0
    ok = reason == 0
    f = dict(has_v25=int(ok and bool(d[0] & 2)), has_ppi=int(ok and bool(d[0] & 0x20)), spr_valid=int(ok and bool(d[3] & 0x80)),
             spr_mask=d[3] & 3 if ok else 0, profile=w[12] & 3 if ok else 0)
    return reason, f


def bridge(b):
    """the bytes the core receives for file b: APF writes whole 32-bit words, the final partial word padded (zero assumed)"""
    return b + bytes((-len(b)) % 4)


def mra_descriptor(path):
    t = (ROOT / path).read_text()
    m = re.search(r'<rom index="0">\s*<part>([0-9A-Fa-f]+)</part>', t)
    return bytes.fromhex(m.group(1))[:16]


def games():
    return sorted(GAMES.glob("*.json"))


def main(a):
    if a[0] == "make":
        Path(a[2]).write_bytes(make(json.loads(Path(a[1]).read_text()))); return 0
    if a[0] == "neg":
        fn, _ = NEG[a[1]]; Path(a[3]).write_bytes(fn(make(json.loads(Path(a[2]).read_text())))); return 0
    if a[0] == "decode":
        r, f = decode(Path(a[1]).read_bytes()); print(f"reason={r} ({REASONS[r]})", " ".join(f"{k}={v}" for k, v in f.items())); return 0
    if a[0] == "check-games":
        bad = 0
        for p in games():
            g = json.loads(p.read_text()); b = make(g)
            mra = mra_descriptor(g["mra"]); same = mra == bytes.fromhex(g["descriptor"])
            r, f = decode(b)
            print(f"{p.stem}: descriptor {g['descriptor'][:8]}.. {'== MRA' if same else '!= MRA ' + mra.hex()} decode reason={r} {f}")
            bad += (not same) + (r != 0)
            for k, (fn, exp) in NEG.items():
                rr, _ = decode(fn(b)); ok = rr == exp; bad += not ok
                print(f"  neg {k:<10} reason={rr} expected {exp} {'OK' if ok else 'FAIL'}")
            tr = {}
            for n in range(64):
                tr.setdefault(decode(bridge(b[:n]))[0], []).append(n)
            ok = 0 not in tr; bad += not ok
            print(f"  truncations 0-63 bytes at the bridge (zero padding): " + "; ".join(f"reason {r}: {v[0]}-{v[-1]} bytes" for r, v in sorted(tr.items()))
                  + f" {'OK (all rejected)' if ok else 'FAIL (a truncated file is accepted)'}")
        print("CHECK-GAMES", "PASS" if bad == 0 else f"FAIL ({bad})"); return 1 if bad else 0
    if a[0] == "check-files":
        import hashlib
        man = Path(a[1]) if len(a) > 1 else GAMES / "cfg_files.sha256"
        want = {}
        for ln in man.read_text().splitlines():
            if ln.strip():
                h, n = ln.split(); want[n] = h
        made = {f"{p.stem}.cfg": make(json.loads(p.read_text())) for p in games()}
        bad = 0
        for n, b in sorted(made.items()):
            h = hashlib.sha256(b).hexdigest(); ok = want.get(n) == h; bad += not ok
            print(f"{n}: made {h[:16]}.. manifest {(want.get(n) or 'MISSING')[:16]}.. {'OK' if ok else 'FAIL'}")
        for n in sorted(set(want) - set(made)):
            bad += 1; print(f"{n}: in the manifest but no games/{n[:-4]}.json FAIL")
        for f in a[2:]:
            fp = Path(f); key = next((n for n in made if fp.name.startswith(n[:-4] + ".") or fp.name.startswith(n[:-4] + "_")), None)
            ok = key is not None and fp.read_bytes() == made[key]; bad += not ok
            print(f"{fp}: {'== made ' + key if ok else 'FAIL (no game of that name, or the bytes differ)'}")
        print("CHECK-FILES", "PASS" if bad == 0 else f"FAIL ({bad})"); return 1 if bad else 0
    if a[0] == "vectors":
        # $readmemh byte stream for tb/tb_u1_board_cfg.sv, 100 bytes per case: nbytes, 64 file bytes (zero-padded), reason, has_v25,
        # has_ppi, spr_valid, spr_mask, profile, 24 expected-sum bytes (as stored in the file), 5 pad; case names -> <out>.names
        bs, names = [], []
        for p in games():
            g = json.loads(p.read_text()); b = make(g)
            for kind, blob in [("valid", b)] + [(k, fn(b)) for k, (fn, _) in NEG.items()] + [("oversize", b + bytes(16))]:
                r, f = decode(blob)
                rec = [len(blob)] + list(blob[:64].ljust(64, b"\0")) + [r, f["has_v25"], f["has_ppi"], f["spr_valid"], f["spr_mask"], f["profile"]]
                rec += list(blob[32:56].ljust(24, b"\0")) + [0] * 5
                assert len(rec) == 100; bs += rec; names.append(f"{p.stem}:{kind}")
        Path(a[1]).write_text("\n".join(f"{x:02x}" for x in bs) + "\n"); Path(a[1] + ".names").write_text("\n".join(names) + "\n")
        print(f"{len(names)} vectors -> {a[1]}"); return 0
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
