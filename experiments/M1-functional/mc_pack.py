#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""MC-compact: exactly cycle-equivalent control-store packing (builder-only post-pass; experiments/MC-compact/README.md).

Called by m1_m11_microcode.build() after compact() when a pack mode is selected (`--mc-pack=MODE` or env GA2_MC_PACK=MODE);
with no mode nothing here runs and the images are byte-identical to the unpacked builder's.  MODE: comma/plus-separated subset of
  tail   identical-tail merging: the words are partitioned into the coarsest bisimulation (same control word apart from JADDR,
         successors in the same classes: fall-through / jump / branch / call target and return point), one word per class is kept.
         A fall-through word (NXT 0) whose successor class is placed elsewhere becomes an unconditional jump (NXT 1) to it.
  tramp  shared fall-through runs: a run e, e+1..e+k-1 of NXT 0 words entered only at e falls into x; e becomes a CALL (NXT 3) of a
         pooled copy of e+1..e+k-1 whose last word returns (NXT 4), and x moves to e+1 (the return point).  Pool bodies share suffixes.
  zero   as tramp, but only runs whose body is all-zero words (the narrow form of the capacity study).
Why this is exactly equivalent (v2_useq.sv): every word issues in one advancing cycle whatever its NXT (next is combinational from
cw: fall-through 157, jump 158, branch 159, call 160, return 161); stall / !ce re-issue the same word and gate the push / pop (166,
177-180); the datapath decodes NXT only as == 5 / == 6 (v2_core.sv 151, 459) and never reads JADDR (or upc), so a word sequence
that agrees in every other bit, in NXT == 2/5/6 and in the branch condition gives the same datapath behaviour cycle for cycle.
The pass asserts its own preconditions (no NXT 7, no fall-through off the end, call sites at static stack depth <= 3) and the
result is checked independently by experiments/MC-compact/mc_equiv.py (product exploration of both images)."""
from pathlib import Path
import collections
import os

from microasm import FIELDS

LAST_PACK = {}
JLSB, JW = FIELDS["JADDR"]
JM = ((1 << JW) - 1) << JLSB
NLSB, NW = FIELDS["NXT"]
NM = ((1 << NW) - 1) << NLSB
STACK_DEPTH = 4                                   # v2_useq.sv 145: reg [11:0] stack [0:3], 2-bit sp (wraps: a 5th live entry overwrites)
OBSERVED = ("halt", "trap", "trap_mode", "trap_immdest", "trap_priv", "trap_regid")   # testbenches compare upc with these addresses


def nxt(w):
    return (w >> NLSB) & ((1 << NW) - 1)


def jad(w):
    return (w >> JLSB) & ((1 << JW) - 1)


def setj(w, n=None, j=None):
    if n is not None:
        w = (w & ~NM) | (n << NLSB)
    if j is not None:
        w = (w & ~JM) | (j << JLSB)
    return w


def mode_from_env(argv):
    for a in argv:
        if a.startswith("--mc-pack="):
            return a.split("=", 1)[1]
    return os.environ.get("GA2_MC_PACK", "")


def parse_mode(mode):
    s = {x for x in mode.replace("+", ",").split(",") if x}
    assert s <= {"tail", "tramp", "zero"}, f"unknown pack mode {mode!r}"
    assert not ({"tramp", "zero"} <= s), "tramp includes zero"
    return s


def roots(sym, dtab, mtab):
    return sorted({0, sym["reset"], sym["disp"]} | ({sym["irq_entry"]} if "irq_entry" in sym else set())
                  | {v & 0xFFF for v in dtab.values()} | {v & 0xFFF for v in mtab.values()})


def stack_depths(p, dtab, mtab):
    """Exhaustive exploration of (micro-PC, live return-address stack): conditions free, dispatch to every dispatch-table uaddr,
    mode dispatch to every mode-table uaddr (a superset of the real behaviour). Returns {addr: set of depths}. Asserts no
    overflow (a call with 4 held), underflow, fall-through off the end, or NXT 7."""
    N = len(p)
    DT, MT = sorted({v & 0xFFF for v in dtab.values()}), sorted({v & 0xFFF for v in mtab.values()})
    seen, work, dep = set(), [(0, ())], collections.defaultdict(set)
    while work:
        s = work.pop()
        if s in seen:
            continue
        seen.add(s)
        a, st = s
        assert 0 <= a < N, f"control reaches {a} outside the image ({N} words)"
        dep[a].add(len(st))
        w = p[a]; n = nxt(w); j = jad(w)
        if n == 0:
            work.append((a + 1, st))
        elif n == 1:
            work.append((j, st))
        elif n == 2:
            work += [(j, st), (a + 1, st)]
        elif n == 3:
            assert len(st) < STACK_DEPTH, f"call at {a} with {len(st)} return addresses held"
            work.append((j, st + (a + 1,)))
        elif n == 4:
            assert st, f"return at {a} with an empty stack"
            work.append((st[-1], st[:-1]))
        elif n == 5:
            work += [(t, st) for t in DT]
        elif n == 6:
            work += [(t, st) for t in MT]
        else:
            raise AssertionError(f"NXT 7 at {a}: not supported by the packer")
    return dep


def relocate(q, old_of_new, sym, dtab, mtab, newaddr):
    """q: new words whose JADDR (NXT 1/2/3) still hold OLD addresses; newaddr: old -> new."""
    for i, w in enumerate(q):
        if nxt(w) in (1, 2, 3):
            assert jad(w) in newaddr, f"new word {i} refers to dropped word {jad(w)}"
            q[i] = setj(w, j=newaddr[jad(w)])
    for k in list(sym):
        assert sym[k] in newaddr, f"symbol {k} on a dropped word"
        sym[k] = newaddr[sym[k]]
    for t in (dtab, mtab):
        for k in list(t):
            assert t[k] & 0xFFF in newaddr, f"table entry {k} on a dropped word"
            t[k] = (t[k] & ~0xFFF) | newaddr[t[k] & 0xFFF]


# ---------------------------------------------------------------------------------------------------------------------------- tail
def tail_merge(p, sym, dtab, mtab):
    N = len(p)
    obs = {sym[k] for k in OBSERVED if k in sym}
    # pinned words stay in classes of their own and start a chain (no word falls into them), so keep_table_bits() can move them:
    # word 0 (reset), the testbench-observed words, the mode-table targets (few addresses: their uaddr bits are mostly constant)
    pinned = {0} | obs | {v & 0xFFF for v in mtab.values()}

    def succ(a):
        n = nxt(p[a]); j = jad(p[a])
        return {0: (a + 1,), 1: (j,), 2: (j, a + 1), 3: (j, a + 1)}.get(n, ())

    for a in range(N):
        assert nxt(p[a]) != 7, f"NXT 7 at {a}"
        if nxt(p[a]) in (0, 2, 3):
            assert a + 1 < N, f"word {a} falls through off the end"
    # initial partition: everything but JADDR; word 0 (reset) and the testbench-observed words stay alone
    seed = [(p[a] & ~JM, a if a in pinned else -1) for a in range(N)]
    extra = [0] * N                                 # conflict-splitting tag, see below
    rounds = 0
    while True:
        ids = {}
        cls = [ids.setdefault((seed[a], extra[a]), len(ids)) for a in range(N)]
        while True:                                 # refine to the coarsest bisimulation finer than (seed, extra)
            ids = {}
            new = [ids.setdefault((cls[a], tuple(cls[s] for s in succ(a))), len(ids)) for a in range(N)]
            if len(ids) == len(set(cls)):
                break
            cls = new
        # a class may be physically preceded by at most one class whose fall-through is hard (NXT 2 / 3: it cannot become a jump)
        hard = collections.defaultdict(set)
        for a in range(N - 1):
            if nxt(p[a]) in (2, 3):
                hard[cls[a + 1]].add(cls[a])
        bad = {c for c, v in hard.items() if len(v) > 1}
        if not bad:
            break
        rounds += 1
        assert rounds < 100
        # split each conflicting class by the class of its members' hard predecessor (members without one stay together); the next
        # round starts from a partition strictly finer than this round's result, so the loop terminates
        extra = [(cls[a], cls[a - 1] if (a > 0 and cls[a] in bad and nxt(p[a - 1]) in (2, 3)) else -1) for a in range(N)]
    members = collections.defaultdict(list)
    for a in range(N):
        members[cls[a]].append(a)
    C = list(members)
    first = {c: members[c][0] for c in C}
    F = {}                                          # class -> fall-through successor class (NXT 0 / 2 / 3)
    for c in C:
        a = first[c]
        if nxt(p[a]) in (0, 2, 3):
            F[c] = cls[a + 1]
    # choose the physical predecessor of each class: the hard one, else the soft one of the class's first member, else any soft one
    preds = collections.defaultdict(list)
    for c, s in F.items():
        preds[s].append(c)
    chosen = {}
    for s, lst in preds.items():
        h = [c for c in lst if nxt(p[first[c]]) in (2, 3)]
        if first[s] in pinned:
            assert not h, f"pinned word {first[s]} has a hard fall-through predecessor"
            continue
        if h:
            assert len(set(h)) == 1
            chosen[s] = h[0]
            continue
        a0 = first[s]
        if a0 > 0 and cls[a0 - 1] in lst:
            chosen[s] = cls[a0 - 1]
        else:
            chosen[s] = min(lst, key=lambda c: first[c])
    nextof = {c: s for s, c in chosen.items()}
    heads = [c for c in C if c not in chosen]
    heads.sort(key=lambda c: first[c])
    assert heads[0] == cls[0] and first[cls[0]] == 0 and chosen.get(cls[0]) is None
    order = []
    for h in heads:
        c = h
        while c is not None:
            order.append(c)
            c = nextof.get(c)
    assert len(order) == len(C) and len(set(order)) == len(C), "fall-through chains are not simple paths"
    addr = {c: i for i, c in enumerate(order)}
    q, jumps = [], 0
    for i, c in enumerate(order):
        a = first[c]; w = p[a]; n = nxt(w)
        if n in (1, 2, 3):
            w = setj(w, j=addr[cls[jad(w)]])
        if n in (0, 2, 3) and (i + 1 >= len(order) or order[i + 1] != F[c]):
            assert n == 0, f"hard fall-through of class at {a} not placed"
            w = setj(w, n=1, j=addr[F[c]])
            jumps += 1
        q.append(w)
    newaddr = {a: addr[cls[a]] for a in range(N)}
    for k in list(sym):
        sym[k] = newaddr[sym[k]]
    for t in (dtab, mtab):
        for k in list(t):
            t[k] = (t[k] & ~0xFFF) | newaddr[t[k] & 0xFFF]
    merged = sum(len(v) - 1 for v in members.values())
    p[:] = q
    return dict(words_before=N, words_after=len(q), merged=merged, fallthrough_to_jump=jumps, conflict_split_rounds=rounds)


# --------------------------------------------------------------------------------------------------------------------------- tramp
def trampolines(p, sym, dtab, mtab, zero_only):
    N = len(p)
    dep = stack_depths(p, dtab, mtab)
    entry = set(roots(sym, dtab, mtab)) | set(sym.values())
    for a, w in enumerate(p):
        if nxt(w) in (1, 2, 3):
            entry.add(jad(w))
        if nxt(w) == 3:
            entry.add(a + 1)                        # return point
    # segments: maximal runs of NXT 0 words with no entry except at the first word; body = all words but the first
    runs, a = [], 0
    while a < N:
        if nxt(p[a]) != 0:
            a += 1
            continue
        b = a + 1
        while b < N and nxt(p[b]) == 0 and b not in entry:
            b += 1
        if b - a >= 2:
            runs.append((a, b))                     # words a..b-1, falls into b
        a = b
    cand = []                                       # (call site, body start, body end): the body is p[start:end], site = start - 1
    for a, b in runs:
        if zero_only:                               # every maximal all-zero block inside the run's body, called from the word before it
            x = a + 1
            while x < b:
                if p[x] != 0:
                    x += 1
                    continue
                y = x
                while y < b and p[y] == 0:
                    y += 1
                cand.append((x - 1, x, y))
                x = y
        else:
            cand.append((a, a + 1, b))
    cand = [(c, x, y, tuple(p[x:y])) for c, x, y in cand if max(dep.get(c, {0})) < STACK_DEPTH]   # the call site needs a free stack entry
    # pool: bodies sorted longest first; a body that is a suffix of a pooled one enters it; keep a body only if it pays
    use = collections.Counter(c[3] for c in cand)
    pools, chosen_bodies = [], {}
    for body in sorted(use, key=lambda x: (-len(x), x)):
        hit = next(((pi, len(pb) - len(body)) for pi, pb in enumerate(pools) if pb[len(pb) - len(body):] == body), None)
        if hit:
            chosen_bodies[body] = hit
        elif use[body] >= 2 or (zero_only and pools == []):
            pools.append(body); chosen_bodies[body] = (len(pools) - 1, 0)
    sites = [c for c in cand if c[3] in chosen_bodies]
    drop, callsite = set(), {}
    for c, x, y, body in sites:
        assert c not in callsite and nxt(p[c]) == 0 and not (set(range(x, y)) & (drop | entry))
        drop.update(range(x, y)); callsite[c] = body
    keep = [x for x in range(N) if x not in drop]
    newaddr = {old: i for i, old in enumerate(keep)}
    q = []
    for old in keep:
        w = p[old]
        if old in callsite:
            w = setj(w, n=3)                        # JADDR filled below (pool addresses are new addresses)
        q.append(w)
    relocate(q, keep, sym, dtab, mtab, newaddr)
    base = []
    for pb in pools:
        base.append(len(q))
        for i, w in enumerate(pb):
            assert nxt(w) == 0
            q.append(setj(w, n=4) if i == len(pb) - 1 else w)
    for old in callsite:
        pi, off = chosen_bodies[callsite[old]]
        q[newaddr[old]] = setj(q[newaddr[old]], j=base[pi] + off)
    p[:] = q
    return dict(words_before=N, words_after=len(q), call_sites=len(sites), removed=len(drop), pool_words=sum(len(x) for x in pools),
                pools=len(pools), max_callsite_depth=max((max(dep.get(c, {0})) for c, _, _, _ in sites), default=None))


# ------------------------------------------------------------------------------------------------------------- table constant bits
def bit_states(words, width, depth):
    """per bit: '0' / '1' constant or 'v' varying over `words` padded with zero words to `depth` (as MC-post1/const_bits.py)"""
    ws = list(words) + [0] * (depth - len(words))
    return tuple("v" if len({(w >> b) & 1 for w in ws}) == 2 else str((ws[0] >> b) & 1) for b in range(width))


def newly_varying(ref, now):
    return [b for b, (x, y) in enumerate(zip(ref, now)) if x != "v" and y == "v"]


def table_states(dtab, mtab):
    return bit_states([dtab[i] for i in range(256)], 24, 256), bit_states([mtab[i] for i in range(512)], 24, 512)


def keep_table_bits(p, sym, dtab, mtab, ref, maxpad=3):
    """HW-001 caution: a dispatch / mode table output bit that was constant must not become varying (it would un-prune logic on the
    next-address path). If packing made one vary: move the fall-through blocks holding the mode-table targets and the dispatch
    table's default entry (`trap`) to the end of the image, and optionally put zero padding words (never executed) in front of the
    remaining dispatch targets (the IRQ stubs) and of each moved block; the order and padding with the fewest padding words that
    restore every constant table bit wins. Asserts that one exists."""
    if not any(newly_varying(r, n) for r, n in zip(ref, table_states(dtab, mtab))):
        return dict(moved_blocks=0, padding=0)
    import itertools
    N = len(p)
    falls = lambda a: nxt(p[a]) in (0, 2, 3)
    mts = sorted({v & 0xFFF for v in mtab.values()})
    blocks = []
    for t in mts + ([sym["trap"]] if "trap" in sym else []):
        s, e = t, t + 1
        while s > 0 and falls(s - 1):
            s -= 1
        while falls(e - 1):
            e += 1
        if not any(s < e2 and s2 < e for s2, e2 in blocks):
            blocks.append((s, e))
        else:
            assert (s, e) in blocks
    inb = {a for s, e in blocks for a in range(s, e)}
    assert 0 not in inb
    base = [a for a in range(N) if a not in inb]
    dts = sorted({v & 0xFFF for v in dtab.values()} - inb)
    stub0 = base.index(dts[0]) if dts else len(base)
    refm = [ref[0][:12], ref[1][:12]]
    sets = [sorted({v & 0xFFF for v in dtab.values()}), mts]

    def ok(newaddr):
        for r, st in zip(refm, sets):
            ws = [newaddr[a] for a in st]
            for b in range(12):
                if r[b] != "v" and len({(w >> b) & 1 for w in ws}) == 2:
                    return False
        return True

    def layout(order, pads, spad):
        newaddr, old = {}, []
        for i, a in enumerate(base):
            if i == stub0:
                old += [None] * spad
            newaddr[a] = len(old); old.append(a)
        for (s, e), pd in zip(order, pads):
            old += [None] * pd
            for a in range(s, e):
                newaddr[a] = len(old); old.append(a)
        return newaddr, old

    best = None
    for total in range(0, maxpad * (len(blocks) + 1) + 1):
        for spad in range(min(maxpad, total) + 1):
            for pads in itertools.product(range(maxpad + 1), repeat=len(blocks)):
                if sum(pads) + spad != total:
                    continue
                for order in itertools.permutations(blocks):
                    if ok(layout(order, pads, spad)[0]):
                        best = (order, pads, spad)
                        break
                if best:
                    break
            if best:
                break
        if best:
            break
    assert best is not None, "no block order / padding keeps the table constant bits"
    order, pads, spad = best
    newaddr, old = layout(order, pads, spad)
    q = [p[a] if a is not None else 0 for a in old]
    relocate(q, old, sym, dtab, mtab, newaddr)
    p[:] = q
    return dict(moved_blocks=len(order), block_words=sum(e - s for s, e in order), padding=sum(pads) + spad, stub_padding=spad)


def pack(p, sym, dtab, mtab, mode, keep_bits=True, ref_key=None):
    """keep_bits: restore the table constant bits (keep_table_bits); the builder turns it off for the TEST-ONLY --smc-test images, which
    have no IRQ stubs (every dispatch entry points straight at a routine) and are never fitted.
    ref_key ('prod' / 'rn'): R-play1: the constant-bit reference is the FROZEN d3f75a9 image (mc_basebits.json), not this build's unpacked image: with the
    gameplay forms the unpacked source exceeds the 2,048 words of the control store, so its uaddr bits >= 11 are meaningless as a reference. A bit that is
    constant in the reference and cannot be kept constant is not an error here: it is recorded (LAST_PACK['newly_varying_*']) and reported."""
    m = parse_mode(mode)
    LAST_PACK.clear()
    LAST_PACK["mode"] = mode
    st0 = stack_depths(p, dtab, mtab)
    ref_tables = table_states(dtab, mtab)
    ref_cs = bit_states(p, 101, 2048)
    jf = Path(__file__).with_name("mc_basebits.json")
    if ref_key and jf.exists():
        import json
        rj = json.load(open(jf))[ref_key]
        ref_tables = (tuple(rj["dispatch"]), tuple(rj["mode"]))
        ref_cs = tuple(rj["cs"])
        LAST_PACK["reference"] = f"d3f75a9 ({ref_key})"
    LAST_PACK["max_depth_before"] = max(max(v) for v in st0.values())
    if "tail" in m:
        LAST_PACK["tail"] = tail_merge(p, sym, dtab, mtab)
    if m & {"tramp", "zero"}:
        LAST_PACK["tramp"] = trampolines(p, sym, dtab, mtab, zero_only="zero" in m)
    if keep_bits:
        try:
            LAST_PACK["table_bits"] = keep_table_bits(p, sym, dtab, mtab, ref_tables)
        except AssertionError as e:
            if not ref_key:
                raise
            LAST_PACK["table_bits"] = dict(unsatisfied=str(e))
        LAST_PACK["newly_varying_tables"] = [newly_varying(r, n) for r, n in zip(ref_tables, table_states(dtab, mtab))]
        if not ref_key:
            assert not any(LAST_PACK["newly_varying_tables"])
    LAST_PACK["newly_varying_cs"] = newly_varying(ref_cs, bit_states(p, 101, 2048))
    if not ref_key:
        assert not LAST_PACK["newly_varying_cs"], "a constant control-store bit became varying"
    st1 = stack_depths(p, dtab, mtab)               # re-asserts: no overflow / underflow / NXT 7 / fall-off in the packed image
    LAST_PACK["max_depth_after"] = max(max(v) for v in st1.values())
    assert LAST_PACK["max_depth_after"] <= STACK_DEPTH
    for k in OBSERVED:
        if k in sym:
            assert sum(1 for kk in OBSERVED if kk in sym and sym[kk] == sym[k]) == 1, f"observed symbol {k} shares its address"
    LAST_PACK["words"] = len(p)
    return LAST_PACK
