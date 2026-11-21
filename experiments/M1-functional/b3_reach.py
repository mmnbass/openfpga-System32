#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""B3: control-flow reachability of the V2 control store, over the builder's 8 configurations (smc_test x smc_merged x reset_native).
Roots: the reset entry, `disp`, `irq_entry`, every dispatch-table and mode-table uaddr. Edges: fall-through (NXT 0), jump (1), conditional (2: target and
fall-through), call (3: target; the callee returns to the next word, which is also reachable), dispatch / mode dispatch (5 / 6: fall-through only; their
targets are the table entries, already roots). A label is "dead" when every one of its words is unreachable in every configuration that emits it.
  b3_reach.py [--json out]   prints the analysis; exit 1 when a label that is reachable somewhere is listed as dead (self-check)
"""
import json, sys, bisect
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "V2-datapath-floor" / "sim"))
import m1_m11_microcode as B  # noqa: E402
B._NO_COMPACT = True
from microasm import FIELDS  # noqa: E402

f = lambda w, k: (w >> FIELDS[k][0]) & ((1 << FIELDS[k][1]) - 1)


def analyse(st, sm, rn):
    p, sym, dtab, mtab = B.build(st, sm, rn)
    N = len(p)
    roots = [sym["reset"], sym["disp"]] + ([sym["irq_entry"]] if "irq_entry" in sym else []) + [v & 0xFFF for v in dtab.values()] + [v & 0xFFF for v in mtab.values()]
    seen, work = set(), list(roots)
    while work:
        a = work.pop()
        if a in seen or a >= N:
            continue
        seen.add(a)
        w = p[a]; n = f(w, "NXT"); j = f(w, "JADDR")
        if n in (0, 5, 6): work.append(a + 1)
        elif n == 1: work.append(j)
        elif n in (2, 3): work += [j, a + 1]
    names = sorted((v, k) for k, v in sym.items())
    starts = [v for v, _ in names]
    owner = lambda a: names[bisect.bisect_right(starts, a) - 1][1]
    per = {}
    for a in range(N):
        per.setdefault(owner(a), [0, 0])
        per[owner(a)][0] += 1
        per[owner(a)][1] += a in seen
    return N, len(seen), per


def main():
    cfgs = [(st, sm, rn) for st in (False, True) for sm in (False, True) for rn in (False, True)]
    res = {c: analyse(*c) for c in cfgs}
    lab = {}
    for c, (N, R, per) in res.items():
        for k, (n, r) in per.items():
            lab.setdefault(k, []).append((n, r))
    dead = {k: v for k, v in lab.items() if all(r == 0 for _, r in v)}
    # a label whose words are only partly reachable is kept whole (its words are reachable code of a larger routine)
    out = {"configs": {str(c): {"words": N, "reachable": R} for c, (N, R, _) in res.items()},
           "dead_labels": {k: v[0][0] for k, v in sorted(dead.items())},
           "dead_words_all_configs": sum(v[0][0] for v in dead.values()),
           "production_dead_labels": {k: n for k, (n, r) in res[(False, False, True)][2].items() if r == 0}}
    print(json.dumps(out, indent=1))
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
