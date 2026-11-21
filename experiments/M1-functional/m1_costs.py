#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measured V2 instruction costs, per instruction form.

Runs tb_m1_v2 with +retire on the given programs and aggregates the
observer's per-instruction RETIRE records (v2_sim_observer: cycles from the
cycle after the dispatch that starts an instruction through the dispatch
that ends it, split into exec / mem / fetch / iter):

  exec   advancing microinstructions that are not waits (the microsteps)
  fetch  fetch_short stalls plus decode-ready / queue polls
  mem    data_busy (pipeline frozen on a data transfer)
  iter   multiply/divide/shift iteration

Each instruction is classified from its bytes (operation, F1/F2, D, the
source and destination modes). Cycles are clk_sys at 48.317 MHz (20.7 ns).

Usage: m1_costs.py <sim> <mc-dir> <prog-dir> <config> <name>...
Prints a table: form, count, cycles min/median/max, mean exec/fetch/mem/iter.

Every run is validated before its samples are used (review C3-2): the
simulator must exit 0, reach HALT, report no TRAP, report SIMCHECK and ipc
errors of 0, and give exactly one RETIRE record per retired instruction (END
instrs). Any failed, trapped, timed-out or incomplete run is reported and
the script exits 1 without printing a table. Extra simulator plusargs (e.g. a
small +maxcyc) can be given in <config>.
"""
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

OPS = {0x2D: "MOV.W", 0x84: "ADD.W", 0xAC: "SUB.W", 0xBC: "CMP.W", 0xA4: "AND.W"}


def spec(b, i, modm):
    """(kind, length) of the operand specifier at b[i]."""
    m = b[i]
    if modm and (m >> 5) == 3:
        return "R", 1
    if not modm and 0xE0 <= m <= 0xEF:
        return "#q", 1
    if not modm and m == 0xF4:
        return "#i32", 5
    return "?", 1


def form(b, pc):
    op = b[pc]
    if op == 0x00:
        return "HALT"
    if op == 0xCD:
        return "NOP"
    if op == 0x6A:
        return "BR8"
    if op == 0xF7:
        return "GETPSW R"
    if op not in OPS:
        return f"op {op:02x}"
    f = b[pc + 1]
    if f & 0x80:
        s, ln = spec(b, pc + 2, (f >> 6) & 1)
        d, _ = spec(b, pc + 2 + ln, (f >> 5) & 1)
        return f"{OPS[op]} F1 {s},{d}"
    s, _ = spec(b, pc + 2, (f >> 6) & 1)
    if f & 0x20:
        return f"{OPS[op]} F2 D=1 {s},R"
    return f"{OPS[op]} F2 D=0 R,{s}"


def main():
    sim, mc, pdir, cfg = sys.argv[1:5]
    names = sys.argv[5:]
    syms = dict(l.split("=") for l in Path(mc, "m1_symbols.txt").read_text().split())
    args = [f"+haltpc={syms['halt']}", f"+trappc={syms['trap']}", f"+trapmodepc={syms['trap_mode']}",
            f"+trapimmpc={syms['trap_immdest']}", "+retire"] + \
           ([] if "+maxcyc=" in cfg else ["+maxcyc=400000"]) + cfg.split()
    rows = defaultdict(list)
    bad = []
    for n in names:
        b = Path(pdir, n + ".bin").read_bytes() + bytes(16)
        r = subprocess.run([sim, f"+img={pdir}/{n}.hex", f"+mc={mc}"] + args, capture_output=True, text=True)
        out = r.stdout
        end = re.search(r"^END halted=(\d) instrs=(\d+)", out, re.M)
        nret = len(re.findall(r"^RETIRE ", out, re.M))
        why = ("simulator exit %d" % r.returncode if r.returncode != 0 else
               "no END line" if not end else
               "did not halt" if end.group(1) != "1" else
               "trapped" if re.search(r"^TRAP", out, re.M) else
               "SIMCHECK/ipc errors" if not re.search(r"^SIMCHECK errors=0 ipc_errors=0", out, re.M) else
               f"{nret} RETIRE records for {end.group(2)} retired" if nret != int(end.group(2)) else None)
        if why:
            bad.append((n, why))
            continue
        for m in re.finditer(r"RETIRE \d+ pc=(\w+) op=\w+ cycles=(\d+) exec=(\d+) mem=(\d+) fetch=(\d+) iter=(\d+)", out):
            pc, c, e, me, fe, it = int(m.group(1), 16), *map(int, m.groups()[1:])
            rows[form(b, pc)].append((c, e, me, fe, it))
    if bad:
        for n, why in bad:
            print(f"m1_costs: REJECTED {n}: {why}", file=sys.stderr)
        sys.exit(1)
    print(f"validated {len(names)} runs: each halted, no trap, SIMCHECK/ipc 0, one RETIRE record per retired instruction")
    print(f"{'form':28s} {'n':>6s} {'min':>4s} {'med':>5s} {'max':>4s} {'exec':>6s} {'fetch':>6s} {'mem':>5s} {'iter':>5s}")
    for k in sorted(rows):
        v = rows[k]
        cs = [x[0] for x in v]
        mean = lambda i: sum(x[i] for x in v) / len(v)
        print(f"{k:28s} {len(v):6d} {min(cs):4d} {statistics.median(cs):5.1f} {max(cs):4d} "
              f"{mean(1):6.2f} {mean(3):6.2f} {mean(2):5.2f} {mean(4):5.2f}")


if __name__ == "__main__":
    main()
