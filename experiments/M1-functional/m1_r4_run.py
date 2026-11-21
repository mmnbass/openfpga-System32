#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R4 driver (called by run_r4.sh after the benches are built): runs V60X, upstream and V2 on the m1_r4.py programs, checks every log
against the INDEPENDENT expectations (m1_r3_check.py + m1_r4_check.py), runs the structural / tag-scan stage, the R3 programs on the LIVE
builder (with the supersession table reported at every use), the SMC matrix and the infrastructure-level negative controls, and prints a
PASS/FAIL summary. Exit status 0 only if everything passes and every control is rejected.

Phase r4a (today): V2 = the R3b microcode (mtab[0x0F2] = trap_mode): every PC-relative MOVEA traps `mode` at its own start PC after the
preceding instructions retire; every C-case behaves as plan 6.2 says (the C7 JMP / MOV.W / ALU / GETPSW / LDPR cases already trap `mode`
today: that is the hazard baseline). Phase r4b: the planned behaviour, on the LIVE builder's images (never the frozen fixture).
"""
import argparse
import copy
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "ref"))
import m1_r1_check as C1  # noqa: E402
import m1_r1_run as RR  # noqa: E402
import m1_r2_check as C2  # noqa: E402
import m1_r3_check as C3  # noqa: E402
import m1_r3_run as RUN3  # noqa: E402
import m1_r4_check as C4  # noqa: E402
import m1_r4_tagscan as TS  # noqa: E402

CONFIGS = RR.CONFIGS
results = RR.results
negctl = RR.negctl
note = RR.note
FIXTURE = HERE / "results/r4a_pre_r4_fixture_2026-11-04.json"
PINS = HERE / "results/r4a_table_pins_2026-11-04.json"
FROZEN = HERE / "results/r4a_frozen_expectations_2026-11-04.json"
SUPERSEDED_USES = []          # (program, old, new, section) at every use


def tables_hash(dtab, mtab):
    h = hashlib.sha256()
    for i in range(256):
        h.update(dtab[i].to_bytes(4, "little"))
    for i in range(512):
        h.update(mtab[i].to_bytes(4, "little"))
    return h.hexdigest()


def cstore_hash(p):
    h = hashlib.sha256()
    for w in p:
        h.update(int(w).to_bytes(16, "little"))
    return h.hexdigest()


def by_symbol(sym, dtab, mtab):
    """tables with every routine address replaced by the names of the symbols at that address (the R4b words shift later routines)"""
    names = {}
    for n, a in sym.items():
        names.setdefault(int(a), []).append(n)
    cv = lambda v: (v >> 12, tuple(sorted(names.get(v & 0xFFF, [f"@{v & 0xFFF}"]))))
    return {"dtab": {i: cv(v) for i, v in dtab.items()}, "mtab": {i: cv(v) for i, v in mtab.items()}}


def _norm(p, sym, upto):
    """control-store words below `upto` with the JADDR field replaced by the symbol names at that address (0 stays 0)"""
    from microasm import FIELDS
    names = {}
    for n, a in sym.items():
        names.setdefault(int(a), []).append(n)
    lsb, wd = FIELDS["JADDR"]
    mask = ((1 << wd) - 1) << lsb
    out = []
    for w in p[:upto]:
        ja = (w >> lsb) & ((1 << wd) - 1)
        out.append((w & ~mask, tuple(sorted(names.get(ja, [f"@{ja}"]))) if ja else 0))
    return out


def key(st, sm, rn):
    return f"smc_test={int(st)} smc_merged={int(sm)} reset_native={int(rn)}"


def view(x, man, progs, phase):
    xs = copy.copy(x)
    xs.man, xs.progs, xs.phase = man, progs, phase
    return xs


# ------------------------------------------------------------------ V2 stage (generic over a manifest)
def stage_v2(x, label, man, progs, phase, check, sup=None):
    xs = view(x, man, progs, phase)
    jobs = []
    for n, m in man.items():
        if m["v2"] is None or m["mc"] != "prod":
            continue
        if sup and n in sup:
            old, new, sec, rep = sup[n]
            SUPERSEDED_USES.append((n, old, new, rep, sec))
            note(f"{label}: superseded expectation (plan {sec}); the new expectation is checked on {rep}", True, n)
            continue
        for cfg in CONFIGS:
            jobs.append((n, m, cfg, "std"))
            if m.get("v2_ack"):
                jobs.append((n, m, cfg, "ack"))
                jobs.append((n, m, cfg, "delay"))
    # k = 7 without release: the MOVEA completes (all 7 bytes present, only the NEXT instruction is withheld) but does not retire: the plain
    # "ack" run is excluded for it in the b phases (as R3b)
    jobs = [j for j in jobs if not (j[3] == "ack" and j[1]["extra"].get("k") == 7 and phase.endswith("b"))]

    def one(j):
        n, m, cfg, run = j
        t = RUN3.v2_text(xs, n, m, cfg, run)
        (x.logs / f"{label}_{n}.v2_{RUN3.tagof(cfg, run)}").write_text(t)
        f = check(m, t, phase, run)
        if m["where"] == "s0" and RUN3.memstat_bad(t):
            f = f + [f"S0 memory model: access outside ROM / mirror {RUN3.memstat_bad(t)[:1]}"]
        return j, f
    with ThreadPoolExecutor(x.jobs) as ex:
        for (n, m, cfg, run), f in ex.map(one, jobs):
            cat = (f"{label} V2 [{cfg or 'default'}]" + {"std": "", "ack": " withheld fetch, never released", "delay": " withheld fetch, released by cycle count"}[run]
                   + (" S0 harness" if m["where"] == "s0" else ""))
            note(cat, not f, f"{n}: {f[:2]}")


# ------------------------------------------------------------------ structural stage
def stage_struct(x):
    print("== structural: pins, tag scan, tables over the eight builder combinations")
    import m1_m11_microcode as B
    fx = json.loads(FIXTURE.read_text())["configs"]
    pins = json.loads(PINS.read_text())
    ph = x.phase
    x.struct = {}
    for st in (False, True):
        for sm in (False, True):
            for rn in (False, True):
                k = key(st, sm, rn)
                p, sym, dtab, mtab = B.build(st, sm, rn)           # build() runs the builder's own assertions
                f = fx[k]
                fdtab, fmtab, fsym = dict(enumerate(f["dtab"])), dict(enumerate(f["mtab"])), f["sym"]
                s = TS.scan(p, sym, dtab, mtab)
                bad = TS.problems(s, st, "pre" if ph == "r4a" else "r4b")
                note(f"tag scan ({'pre-R4 expectation' if ph == 'r4a' else 'R4b expectation'}): bit-11 entries, condition-23 words, live descriptor consumers", not bad, f"{k}: {bad[:2]}")
                x.struct[k] = {"words": len(p), "fixture_words": len(f["cstore"]), "tagscan": s}
                if ph == "r4a":
                    ok = tables_hash(dtab, mtab) == pins[k]["tables_full"] and cstore_hash(p) == pins[k]["cstore_sha"] and len(p) == pins[k]["cstore_len"]
                    note("r4a: the live builder's tables and control store are identical to the frozen pre-R4 pins", ok, k)
                    continue
                cur, old = by_symbol(sym, dtab, mtab), by_symbol(fsym, fdtab, fmtab)
                dd = [i for i in range(256) if cur["dtab"][i] != old["dtab"][i]]
                md = [i for i in range(512) if cur["mtab"][i] != old["mtab"][i]]
                if not st:
                    note("r4b production: dispatch table identical to the pre-R4 fixture up to routine relocation (names), no new dispatch entry", not dd, f"{k}: {dd[:4]}")
                    note("r4b production: mode table differs from the pre-R4 fixture in exactly mtab[0x0F2] = (0x88C << 12) | mret",
                         md == [0x0F2] and mtab[0x0F2] == (0x88C << 12) | sym["mret"], f"{k}: {md[:4]}")
                    cs = [int(w, 16) for w in f["cstore"]]
                    j0 = int(fsym["jmp"])
                    note("r4b production: the control store below the jmp routine is identical to the pre-R4 fixture except that jump targets are compared by symbol "
                         "(the added words relocate the trap labels and every later routine, so their JADDR values move)",
                         _norm(p, sym, j0) == _norm(cs, fsym, j0) and j0 == int(sym["jmp"]), k)
                    note("r4b production: 4 words added in total (movea branch + movea_pcrel 2 words + jmp guard)", len(p) - len(cs) == 4, f"{k}: {len(p)} vs {len(cs)}")
                    mem = {hex(i): hex(v >> 12) for i, v in mtab.items() if (v & 0xFFF) not in (sym["trap"], sym["trap_mode"]) and not (((v >> 12) & 1) or ((v >> 12) & 2))}
                    note("r4b production: the memory descriptors (neither register nor immediate) are exactly {0xf3: 0x14c, 0xf2: 0x88c}",
                         mem == {"0xf3": "0x14c", "0xf2": "0x88c"}, f"{k}: {mem}")
                    ok = all((mtab[i] & 0xFFF) == sym["trap_mode"] for i in (0x0F0, 0x0F1, 0x0F8, 0x0F9, 0x0FA, 0x1F2, 0x1F3, 0x0FC, 0x0FD, 0x0FE))
                    note("r4b production: mtab[0x0F0], [0x0F1], [0x0F8..0x0FA], [0x0FC..0x0FE], [0x1F2], [0x1F3] stay trap_mode", ok, k)
                    ok = dtab[0x42] == sym["trap"] and dtab[0x44] == sym["trap"] and (dtab[0x40] & 0xFFF) == sym["movea"]
                    note("r4b production: dtab[0x42] / dtab[0x44] stay trap, dtab[0x40] = movea", ok, k)
                else:
                    ident = tables_hash(dtab, mtab) == pins[k]["tables_full"] and cstore_hash(p) == pins[k]["cstore_sha"]
                    note("r4b --smc-test: tables and control store identical to the pre-R4 pins (no PC-relative support, mtab[0x0F2] = trap_mode, dtab[0x40] = trap)", ident, k)
    note("r4b production: the dispatch-table pin: dtab = trap except the listed opcodes (C7i unreachable consumers)",
         True if x.phase == "r4a" else _dtab_pin(), "")


def _dtab_pin():
    import m1_m11_microcode as B
    p, sym, dtab, mtab = B.build()
    listed = {0x00, 0xCD, 0x2D, 0x6A, 0x84, 0xAC, 0xBC, 0xA4, 0xF7, 0x12, 0xD6, 0x40}
    return {i for i, v in dtab.items() if v != sym["trap"]} == listed


# ------------------------------------------------------------------ R3 programs on the live builder
def stage_r3_live(x, sup):
    print("== R3 programs on the LIVE builder (phase r3b expectations; the supersession table is applied and reported at every use)")
    man3 = json.loads((x.out / "r3progs" / "r3_manifest.json").read_text())
    stage_v2(x, "R3 programs", man3, x.out / "r3progs", "r3b", C3.check_v2r3, sup=sup if x.phase == "r4b" else None)
    x3 = view(x, man3, x.out / "r3progs", "r3b")
    x3.sample = False
    print("== R3 SMC matrix on the live smc builds")
    RUN3.stage_smc(x3)


# ------------------------------------------------------------------ coverage rule, supersession replacements
def stage_rules(x):
    man = x.man
    cov = {n: m for n, m in man.items() if m["extra"].get("cov_of")}
    for n, m in cov.items():
        tgt = m["extra"]["cov_of"]
        note("operand-2 coverage rule: every operand-2 negative has a sibling with identical first operand that traps `immdest` at the same start PC "
             "(the destination lookup was reached with a valid first operand)", tgt in man and man[tgt]["v2"]["r4b"]["pc"] == m["v2"]["r4b"]["pc"]
             and m["v2"]["r4b"]["trap"] == "immdest", f"{n} / {tgt}")
    dst = [n for n in man if "_dst_f1" in n and man[n]["cls"] == "neg" and not n.startswith("cov_")]
    for n in dst:
        sib = [c for c, m in cov.items() if m["extra"].get("cov_of") == n]
        op = n.split("_")[1]
        note("operand-2 coverage rule: every Format II operand-2 negative (mov/add/sub/and) has a coverage sibling; cmp has the positive sibling c7e_cov_cmp_q*",
             bool(sib) or op == "cmp", n)
    note("operand-2 coverage rule (cmp): the positive siblings accept #q as operand 2 after a valid first operand",
         all(n in man for n in ("c7e_cov_cmp_q0", "c7e_cov_cmp_q5", "c7e_cov_cmp_q15")), "")


def negctl_run(x):
    import m1_r4 as G
    import m1_m11_microcode as B
    man = x.man
    # (1) the frozen digest: a one-event change breaks it
    frozen = json.loads(FROZEN.read_text()) if FROZEN.exists() else None
    m2 = copy.deepcopy(man)
    k0 = next(n for n in m2 if m2[n]["exp_gevents"] and any(m2[n]["exp_gevents"][i] for i in m2[n]["exp_gevents"]))
    i0 = next(i for i in m2[k0]["exp_gevents"] if m2[k0]["exp_gevents"][i])
    m2[k0]["exp_gevents"][i0][0][1] ^= 1
    negctl.append(("f1", "frozen digest: a one-event change in one program's expectations changes the manifest digest",
                   G.digest(m2) != G.digest(man), f"{G.digest(man)[:16]} vs {G.digest(m2)[:16]}"))
    if frozen:
        negctl.append(("f0", "control: the regenerated manifest digest equals the frozen record", G.digest(man) == frozen["manifest_sha256"],
                       f"{G.digest(man)[:16]} vs {frozen['manifest_sha256'][:16]}"))
    # (2) the other phase rejects today's / the new V2: every PC-relative positive (std, default config) fails against the other phase's expectation
    other = "r4b" if x.phase == "r4a" else "r4a"
    pp = [n for n, m in man.items() if m["pcrel_idx"] and m["cls"] == "pos" and m["where"] == "low" and m["v2"] and not m.get("v2_ack")][:60]
    rej = 0
    for n in pp:
        t = C1.read_log(x.logs / f"R4_{n}.v2_default")
        rej += bool(C4.check_v2r4(man[n], t, other, "std"))
    negctl.append(("p1", f"the {other} expectations REJECT the current V2 on every one of {len(pp)} PC-relative positives (phase {x.phase})", rej == len(pp), f"{rej}/{len(pp)} rejected"))
    # (3) the tag scan fails on a builder that tags a second entry / adds a condition-23 word / adds a DEC_EA word
    p, sym, dtab, mtab = B.build()
    mt = dict(mtab)
    mt[0x0F3] |= 0x800 << 12
    s2 = TS.scan(p, sym, dtab, mt)
    negctl.append(("t1", "tag scan: a second tagged entry (bit 11 on mtab[0x0F3]) is reported", "0xf3" in s2["tagged"] and bool(TS.problems(s2, False, "r4b" if x.phase == "r4b" else "pre")), f"tagged {s2['tagged']}"))
    from microasm import word, cond
    p2 = list(p) + [word(NXT=2, JADDR=0, **cond(23))]
    s3 = TS.scan(p2, sym, dtab, mtab)
    negctl.append(("t2", "tag scan: an added condition-23 branch word is reported", len(s3["c23"]) > len(TS.scan(p, sym, dtab, mtab)["c23"]) and bool(TS.problems(s3, True, "r4b")),
                   f"c23 {len(s3['c23'])}"))
    p3 = list(p) + [word(DEC_EA=1)]
    s4 = TS.scan(p3, sym, dtab, mtab)
    negctl.append(("t3", "tag scan: an added DEC_EA word (the 0x88C base/size/step bits would become live) is reported", bool(s4["live"]) and bool(TS.problems(s4, True, "pre")), f"live {s4['live']}"))
    # (4) R4-NR checker controls on a real passing log of a C7a program (JMP d[PC] traps; no pad may execute)
    nrn = next(n for n in man if n == "c7a_jmp_f2")
    t = C1.read_log(x.logs / f"R4_{nrn}.v2_default")
    nr = man[nrn]["nr"]
    base = C4.check_v2r4(man[nrn], t, x.phase, "std")
    negctl.append(("n0", "control: the real V2 log of c7a_jmp_f2 passes all checks incl. R4-NR", not base, f"{base[:2]}"))
    pad = next(iter(nr["pads"].values()))
    negctl.append(("n1", "R4-NR: a retire record at a landing pad is rejected", bool(C4.check_nr(man[nrn], t + f"L 9 pc={pad:08x} op=2d\n")), ""))
    negctl.append(("n2", "R4-NR: a pad sentinel in a register is rejected", bool(C4.check_nr(man[nrn], t.replace("R21=00000000", "R21=a0a00002"))), ""))
    negctl.append(("n3", "R4-NR: the raw displacement in a register is rejected", bool(C4.check_nr(man[nrn], t.replace("R05=00000000", f"R05={nr['candidates'][0]:08x}"))), ""))
    negctl.append(("n4", "R4-NR: an extra instruction redirect is rejected", bool(C4.check_nr(man[nrn], re.sub(r"(REDIRECTS reset=\d+ instr=)(\d+)", lambda mm: mm.group(1) + str(int(mm.group(2)) + 1), t))), ""))
    negctl.append(("n5", "the wrong trap kind is rejected (TRAP mode -> opcode)", bool(C4.check_v2r4(man[nrn], t.replace("TRAP mode", "TRAP opcode"), x.phase, "std")), ""))
    negctl.append(("n6", "an unretired-instruction GPR write after the drain is rejected (CU gpr=1)", bool(C4.check_v2r4(man[nrn], re.sub(r"(CU \d+ dr=\d+ dw=\d+ priv=\d+ psw=\d+ gpr=)0", r"\g<1>1", t), x.phase, "std")), ""))
    # (5) reference-log corruptions on a real PC-relative positive: a wrong base, a wrong destination, a missing G event
    ref = next(n for n, m in man.items() if m["refs"] and m["pcrel_idx"] and m["where"] == "low")
    te = C1.read_log(x.logs / f"{ref}.ext")
    good = C4.check_ref4(man[ref], te, "ext")
    negctl.append(("r0", f"control: the V60X log of {ref} passes", not good, f"{good[:2]}"))
    i0 = man[ref]["pcrel_idx"][0]
    mm = re.search(rf"^L {i0} pc=([0-9a-f]{{8}}) op=40 R\d+=([0-9a-f]{{8}})", te, re.M)
    start, exp = int(mm.group(1), 16), int(mm.group(2), 16)
    for nm, wrong in (("next-PC base (start + 7)", exp + 7), ("field-address base (start + 3)", exp + 3), ("head base (start + 2)", exp + 2),
                      ("base 0 (the raw displacement)", exp - start)):
        tt = te.replace(f"={exp:08x}", f"={wrong & 0xFFFFFFFF:08x}")
        negctl.append(("r1", f"V60X log with {nm} is rejected", bool(C4.check_ref4(man[ref], tt, "ext")), ""))
    negctl.append(("r2", "V60X log with the G event removed is rejected", bool(C4.check_ref4(man[ref], re.sub(rf"^G {i0} .*\n", "", te, flags=re.M), "ext")), ""))
    # (6) the supersession table: the three R3 programs exist in the R3 manifest with a `mode` expectation and have an R4 replacement
    man3 = json.loads((x.out / "r3progs" / "r3_manifest.json").read_text())
    for (n3, old, new, rep, sec) in G.SUPERSEDED:
        okk = n3 in man3 and rep in man and man3[n3]["v2"]["r3b"].get("trap") == "mode" and man[rep]["v2"]["r4b"]["kind"] == "halt"
        negctl.append(("s1", f"supersession table entry {n3}: present in the R3 manifest with TRAP mode (r3b); its R4b replacement {rep} halts", okk, f"{old} -> {new}"))
    # the R3 programs that carry mode byte F2 after 0x40 and expect `mode` are exactly the three superseded ones
    f2progs = sorted(n for n, m in man3.items() if m["cls"] == "neg" and _has_pcrel_after_movea(m))
    negctl.append(("s2", "the R3 programs whose trapping instruction is 0x40 with mode byte F2 and kind `mode` are exactly the superseded three",
                   set(f2progs) == {s_[0] for s_ in G.SUPERSEDED}, f"{f2progs}"))


def _has_pcrel_after_movea(m):
    b, tp = m.get("bytes"), m.get("trap_pc")
    if not b or tp is None or m["v2"]["r3b"].get("trap") != "mode":
        return False
    g = lambda i: b.get(f"{(tp + i) & 0xFFFFFFFF:08x}")
    return g(0) == 0x40 and g(2) == 0xF2 and g(1) is not None and not (g(1) & 0x40)      # m = 0 only: (m = 1, F2) stays `mode`


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--phase", default="r4a")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--negctl", type=int, default=1)
    ap.add_argument("--root", required=True)
    a = ap.parse_args()
    import m1_r4 as G
    x = RR.Ctx()
    x.out, x.phase, x.jobs, x.root = Path(a.out), a.phase, a.jobs, Path(a.root)
    x.sample = False
    x.sim_stub = False
    x.progs = x.out / "progs"
    x.logs = x.out / "logs"
    x.logs.mkdir(exist_ok=True)
    x.man = json.load(open(x.progs / "r4_manifest.json"))
    classes = {}
    for m in x.man.values():
        k = f"{m['cls']}/{m['where']}"
        classes[k] = classes.get(k, 0) + 1
    print("programs: " + ", ".join(f"{k} {v}" for k, v in sorted(classes.items())) + f" (total {len(x.man)})")
    # the R4 reference logs are named <program>.{ext,up}; the V2 logs R4_<program>.v2_*
    stage_refs_r4(x)
    stage_v2(x, "R4", x.man, x.progs, x.phase, C4.check_v2r4)
    print("== SMC variant matrix (--smc-test builds)")
    RUN3.stage_smc(x)
    stage_struct(x)
    stage_rules(x)
    sup = {s[0]: (s[1], s[2], s[4], s[3]) for s in G.SUPERSEDED}
    stage_r3_live(x, sup)
    if a.negctl:
        negctl_run(x)
        import m1_r4_s0 as S0
        S0.run(x, negctl, note)
    print("\n================ R4 SUMMARY (phase %s) ================" % x.phase)
    bad = 0
    for cat, (p, t, fl) in results.items():
        print(f"{'PASS' if p == t else 'FAIL'}  {cat}: {p}/{t}")
        for f in fl[:5]:
            print(f"      fail: {f}")
        bad += (p != t)
    nbad = 0
    if a.negctl:
        print("\nnegative controls and catchers (each must be REJECTED / caught):")
        for cid, desc, caught, ev in negctl:
            lab = "EQUIV  " if (cid.endswith("E") and caught) else "CAUGHT " if caught else "MISSED "
            print(f"{lab} ({cid}) {desc}\n         {ev}")
            nbad += (not caught)
    print("\nSUPERSESSION TABLE USES (plan 7.1): %d" % len(SUPERSEDED_USES))
    for n, old, new, rep, sec in SUPERSEDED_USES:
        print(f"  {n}: R3b expectation [{old}] -> R4b expectation [{new}] (checked on {rep}); plan section {sec}")
    ok = bad == 0 and nbad == 0
    print(f"\nR4 {'PASS' if ok else 'FAIL'} (phase {x.phase}): {len(results) - bad}/{len(results)} result groups pass"
          + (f", {len(negctl) - nbad}/{len(negctl)} negative controls/catchers caught" if a.negctl else ""))
    json.dump({"phase": x.phase, "groups": {k: [v[0], v[1]] for k, v in results.items()}, "controls": [[c[0], c[1], c[2]] for c in negctl],
               "supersession_uses": SUPERSEDED_USES, "struct": getattr(x, "struct", None)}, open(x.out / "r4_summary.json", "w"), indent=1, default=str)
    return 0 if ok else 1


def stage_refs_r4(x):
    RUN3.stage_refs(x)


if __name__ == "__main__":
    sys.exit(main())
