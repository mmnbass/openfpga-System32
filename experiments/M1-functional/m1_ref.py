#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the Python V60 reference (ref/v60_ref.py, unmodified upstream copy) on a
program image and print the canonical M1 dump that the RTL benches also print:

  R00=xxxxxxxx ... R31=xxxxxxxx   final general registers
  PSW=xxxxxxxx                    final PSW
  W addr=xxxxxxxx size=n data=xxxxxxxx    every data write, in order
  END halted=1 instrs=N            retired instruction count (HALT included)

Usage: m1_ref.py <image.bin> [max_instrs] [--trace] [--lockstep] [--counters]
                 [--pc N] [--base A] [--mirror24] [--rom-mirror] [--segs FILE] [--ext]
  --trace    prints "T n pc=<pc> op=<opcode>" before each instruction
  --lockstep prints one record per retired instruction, as the RTL benches do:
             "L n pc=<pc> op=<opcode>[ Rnn=<value>...][ PSW=<value>]", listing
             every general register whose value the instruction changed, then
             the PSW if it changed (memory writes are compared separately, in
             the final dump)
Data written by a store is masked to its size (1/2/4 bytes).

R1a options (all default off; without them the output is unchanged):
  --pc N         start at PC N (32-bit, hex with 0x or decimal) instead of 0
  --base A       load the image at address A instead of 0
  --mirror24     memory is 16 MB and every address is taken mod 2^24 (the 24-bit
                 physical bus: s0_mem / s32_v60_bus truncate the same way); a
                 fetch that crosses 0xFFFFFFFF wraps to 0
  --rom-mirror   (with --mirror24) also S0's ROM mirror: 0xF00000-0xFFFFFF reads
                 and writes address 0x000000-0x0FFFFF
  --segs FILE    load segments from FILE (lines "<addr-hex> <hex-bytes>"), in
                 place of / in addition to the image (use "-" for no image)
  --ext          run the extended reference (m1_ref_ext.V60X) instead of v60_ref
  --privev       (R2a, with --ext) print every privileged-register write as an EVENT before the
                 instruction's L record, "P n id=<regID> val=<v>" (all writes, changed value or not),
                 and "PR id=<regID> val=<v>" for every privileged register before END
  --gprev        (R3a) print every general-register write as an EVENT before the instruction's L record,
                 "G n id=<reg> val=<v>" (all writes, changed value or not; a write restored by a later write is
                 two events), after a "GPREV on" line. Byte enables are not modelled (a word write is be=f).
  --counters     after each retired instruction print
                 "C n dr=<data reads> dw=<data writes> priv=<privileged-register writes>"
                 (instruction fetches are not data reads; v60_ref has no privileged
                 registers, so priv is always 0 there; V60X counts LDPR writes)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "ref"))
from v60_ref import V60, Mem, M32  # noqa: E402


TRACE = False
LOCKSTEP = False
COUNTERS = False
PRIVEV = False
GPREV = False


class MaskedBuf:
    """Byte store addressed mod 2^24 (R1a adapter; the 24-bit physical bus).
    Indexing and slicing take any non-negative integer address, including the
    unmasked PC the reference keeps after running past 0xFFFFFFFF. With
    rom_mirror, 0xF00000-0xFFFFFF aliases 0x000000-0x0FFFFF (S0's s0_mem)."""

    def __init__(self, rom_mirror=False):
        self.a = bytearray(1 << 24)
        self.rom_mirror = rom_mirror

    def _m(self, i):
        i &= 0xFFFFFF
        return (i & 0xFFFFF) if (self.rom_mirror and i >= 0xF00000) else i

    def __getitem__(self, k):
        if isinstance(k, slice):
            return bytes(self.a[self._m(i)] for i in range(k.start, k.stop))
        return self.a[self._m(k)]

    def __setitem__(self, k, v):
        if isinstance(k, slice):
            for i, b in zip(range(k.start, k.stop), v):
                self.a[self._m(i)] = b
        else:
            self.a[self._m(k)] = v


class CountingMem(Mem):
    """Mem that counts data reads and writes (instruction fetch goes through
    .b directly, so it is not counted)."""

    def __init__(self, size=1 << 20, mirror24=False, rom_mirror=False):
        super().__init__(1 if mirror24 else size)
        if mirror24:
            self.b = MaskedBuf(rom_mirror)
        elif size != (1 << 20):
            self.b = bytearray(size)
        self.nr = 0
        self.nw = 0

    def r(self, a, d):
        self.nr += 1
        return super().r(a, d)

    def w(self, a, d, v):
        self.nw += 1
        super().w(a, d, v)


class LoggedRegs(list):
    """The register file with a write log (R3a +gprev): every store is an event (index, value), whether or not the
    value changes. A list subclass, so the references' own code (self.r[i] = v, list(self.r)) is unchanged."""

    def __init__(self, *a):
        super().__init__(*a)
        self.ev = []

    def __setitem__(self, k, v):
        self.ev.append((k, v & 0xFFFFFFFF))
        super().__setitem__(k, v)


class CountingPriv(dict):
    """V60X.priv that counts every store to a privileged register (ev: each store as (name, value))."""
    n = 0

    def __init__(self, *a):
        super().__init__(*a)
        self.ev = []

    def __setitem__(self, k, v):
        self.n += 1
        self.ev.append((k, v & 0xFFFFFFFF))
        super().__setitem__(k, v)


def parse_segs(path):
    segs = []
    for line in open(path):
        line = line.split("#")[0].strip()
        if line:
            a, h = line.split()
            segs.append((int(a, 16), bytes.fromhex(h)))
    return segs


def run(img: bytes, max_instrs=200000, start_pc=0, base=0, segs=(), mirror24=False,
        rom_mirror=False, ext=False):
    plain = not (start_pc or base or segs or mirror24 or rom_mirror or ext or COUNTERS)
    if plain:
        m = Mem(1 << 17)                     # 128 KB, as the RTL benches' memory model
        m.b[:len(img)] = img
        cpu = V60(m, 0)
    else:
        m = CountingMem(1 << 17, mirror24, rom_mirror)
        for i, b in enumerate(img):
            m.b[base + i] = b
        for a, bs in segs:
            for i, b in enumerate(bs):
                m.b[a + i] = b
        if ext:
            from m1_ref_ext import V60X
            cpu = V60X(m, start_pc)
            cpu.priv = CountingPriv(cpu.priv)
        else:
            cpu = V60(m, start_pc)
    if GPREV:
        cpu.r = LoggedRegs(cpu.r)
    n = 0
    while not cpu.halted and n < max_instrs:
        if TRACE:
            print(f"T {n} pc={cpu.pc & M32:08x} op={cpu.mem.b[cpu.pc]:02x}")
        pc0, op0, r0, p0 = cpu.pc & M32, cpu.mem.b[cpu.pc], list(cpu.r), cpu.psw()
        nr0, nw0, np0 = (m.nr, m.nw, cpu.priv.n if ext else 0) if COUNTERS else (0, 0, 0)
        ne0 = len(cpu.priv.ev) if (PRIVEV and ext) else 0
        ng0 = len(cpu.r.ev) if GPREV else 0
        cpu.step()
        if GPREV:
            for rid, val in cpu.r.ev[ng0:]:
                print(f"G {n} id={rid} val={val:08x}")
        if PRIVEV and ext:
            from m1_ref_ext import PRIV
            ids = {nm: i for i, nm in PRIV.items()}
            for nm, val in cpu.priv.ev[ne0:]:
                print(f"P {n} id={ids[nm]} val={val:08x}")
        if LOCKSTEP:
            ch = "".join(f" R{i:02d}={cpu.r[i] & M32:08x}" for i in range(32) if cpu.r[i] != r0[i])
            if cpu.psw() != p0:
                ch += f" PSW={cpu.psw() & M32:08x}"
            print(f"L {n} pc={pc0:08x} op={op0:02x}{ch}")
        if COUNTERS:
            print(f"C {n} dr={m.nr - nr0} dw={m.nw - nw0} priv={(cpu.priv.n - np0) if ext else 0}")
        n += 1                          # HALT counts as one retired instruction
    return cpu, m, n


def dump(cpu, m, n):
    out = [f"R{i:02d}={cpu.r[i] & M32:08x}" for i in range(32)]
    out.append(f"PSW={cpu.psw() & M32:08x}")
    for a, d, v in m.writes:
        size = [1, 2, 4][d]
        out.append(f"W addr={a & M32:08x} size={size} data={v & ((1 << (8 * size)) - 1):08x}")
    out.append(f"END halted={int(cpu.halted)} instrs={n}")
    return "\n".join(out) + "\n"


def main():
    argv = sys.argv[1:]

    def opt(name, default=None):
        if name in argv:
            i = argv.index(name)
            v = argv[i + 1]
            del argv[i:i + 2]
            return v
        return default
    pc = int(opt("--pc", "0"), 0)
    base = int(opt("--base", "0"), 0)
    segf = opt("--segs")
    segs = parse_segs(segf) if segf else []
    flags = {f: f in argv for f in ("--trace", "--lockstep", "--counters", "--mirror24",
                                    "--rom-mirror", "--ext", "--privev", "--gprev")}
    args = [x for x in argv if x not in flags]
    img = b"" if args[0] == "-" else Path(args[0]).read_bytes()
    global TRACE, LOCKSTEP, COUNTERS, PRIVEV, GPREV
    PRIVEV = flags["--privev"]
    GPREV = flags["--gprev"]
    if GPREV:
        print("GPREV on")
    TRACE = flags["--trace"]
    LOCKSTEP = flags["--lockstep"]
    COUNTERS = flags["--counters"]
    lim = int(args[1]) if len(args) > 1 else 200000
    cpu, m, n = run(img, lim, pc, base, segs, flags["--mirror24"] or flags["--rom-mirror"],
                    flags["--rom-mirror"], flags["--ext"])
    if PRIVEV and flags["--ext"]:
        from m1_ref_ext import PRIV
        for i, nm in PRIV.items():
            print(f"PR id={i} val={cpu.priv[nm] & 0xFFFFFFFF:08x}")
    sys.stdout.write(dump(cpu, m, n))


if __name__ == "__main__":
    main()
