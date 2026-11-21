#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""M1.0 microcode and decode-table contents for V2 (loaded through V2's
existing control-store / table write ports; no RTL involved).

Contains only what M1.0 needs:
  reset   redirect the fetch queue to 0 (the M1 benches start every CPU at 0,
          as upstream's benches do with START_PC = 0; a real V60 starts at
          0xFFFFFFF0, the queue's reset address)
  disp    wait until the opcode and the next byte are queued (decode-ready,
          ucond 27, M1.1), then dispatch
  HALT    (0x00) a self-loop: the CPU stays halted (interrupts are not in M1.0)
  NOP     (0xCD) consume the opcode byte, then wait and dispatch
Every other opcode dispatches to a TRAP self-loop at a distinct address, so an
unsupported opcode is visible (the bench reports it) instead of running
wild.

Usage: m1_microcode.py <out-dir> [settle-steps]
Writes m1_cstore.hex, m1_dispatch.hex, m1_mode.hex and m1_symbols.txt
(name=address, read by the bench for +haltpc / +trappc).
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "V2-datapath-floor" / "sim"))
from microasm import word, A, ALU, RES, CW_W, write_hex  # noqa: E402  (V2 microassembler, unmodified)

# micro-condition 27 = decode-ready (v2_core ucond[27], M1.1): in the previous
# cycle the bytes up to IMM_OFS were queued and the head did not move
C_READY = 27


def build(settle=0):
    p, sym = [], {}

    def at(name):
        sym[name] = len(p)

    def emit(**f):
        f.setdefault("SIZE_SEL", 2)
        p.append(word(**f))

    def wait_and_dispatch(prefix):
        # wait: a single self-looping micro-branch, taken until the opcode and
        # the byte after it (IMM_OFS = 1) were queued in the previous cycle
        # with the head settled (COND 27 with CPOL 1). The observer counts
        # such self-loop cycles as instruction-fetch waits. The decode table
        # output is registered from the window, so the dispatch issues one
        # cycle after the wait ends.
        at(prefix + "_poll")
        p.append(word(NXT=2, COND=C_READY & 15, COND_HI=C_READY >> 4, CPOL=1,
                      IMM_OFS=1, JADDR=sym[prefix + "_poll"]))
        p.append(word(NXT=5, IMM_OFS=1))                        # dispatch

    # ---- reset (microcode address 0 after reset) ----
    at("reset")
    # settle > 0: idle that many steps first, so the queue's first prefetch
    # (from the reset address) completes before the redirect. This only
    # avoids the known redirect-during-prefetch bus defect at reset (README);
    # it does not fix it, and branches would still hit it.
    p.extend([word()] * settle)
    emit(ASEL=A["CONST"], RCONST=0, ALU_OP=ALU["PASSA"], RES_SEL=RES["ALU"], REDIRECT=1)
    p.extend([word()] * 3)                                      # redirect takes effect at E3
    wait_and_dispatch("reset")

    # ---- HALT: complete, then stay ----
    at("halt")
    p.append(word(NXT=1, JADDR=sym["halt"]))

    # ---- NOP: consume the opcode byte; let the head move; wait; dispatch ----
    at("nop")
    p.append(word(CONSUME=1))
    p.append(word())
    wait_and_dispatch("nop")

    # ---- unsupported opcode ----
    at("trap")
    p.append(word(NXT=1, JADDR=sym["trap"]))

    dtab = {op: sym["trap"] for op in range(256)}
    dtab[0x00] = sym["halt"]
    dtab[0xCD] = sym["nop"]
    return p, sym, dtab


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    p, sym, dtab = build(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
    write_hex(out / "m1_cstore.hex", p, (CW_W + 3) // 4)
    write_hex(out / "m1_dispatch.hex", [dtab[i] for i in range(256)], 6)   # fmt fields 0
    write_hex(out / "m1_mode.hex", [sym["trap"]] * 512, 6)
    (out / "m1_symbols.txt").write_text("".join(f"{k}={v}\n" for k, v in sym.items()))
    print(f"m1 microcode: {len(p)} words; " + ", ".join(f"{k}={v}" for k, v in sym.items()))


if __name__ == "__main__":
    main()
