#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R1b microcode / table mutants (plan section 5, section 10 #3, section 11 #1/#2).

Each mutant is an in-memory edit of m1_m11_microcode.py's SOURCE (the file itself is never
modified): the edited source is written under <out>/mut/<name>/ and run to produce a microcode
directory, which the V2 bench then runs against the whole R1 suite in phase r1b (every positive
program, N1, N2, G1, immediate destinations, J12/BR8 withheld-fetch progress at the 4 fetch
configurations, S0 harness). A mutant is CAUGHT when some program fails the independent
expectations (m1_r1_check.py). Expected-EQUIVALENT mutants must NOT be caught; they are reported
with the plan's reason and, where the plan says so, checked structurally (a builder
self-assertion, or an assertion over the built tables).

    m1_r1b_mutants.py --out <run-dir> [--jobs N]     (run_r1.sh calls it in phase r1b)
"""
import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m1_r1_check as C  # noqa: E402
import m1_r1_run as RR  # noqa: E402

SRC = (HERE / "m1_m11_microcode.py").read_text()
JMP_BLK = SRC[SRC.index('        at("jmp")'):SRC.index('        jmp("disp")', SRC.index('        at("jmp")')) + len('        jmp("disp")')]
GUARD = 'br(C_IMM, "trap_mode", pol=1)'


def blk(fn):
    return lambda s: s.replace(JMP_BLK, fn(JMP_BLK))


def sub_once(old, new):
    def f(s):
        assert s.count(old) == 1, (old, s.count(old))
        return s.replace(old, new)
    return f


def sub_nth(old, new, k):
    """replace the k-th (0-based) occurrence"""
    def f(s):
        i = -1
        for _ in range(k + 1):
            i = s.index(old, i + 1)
        return s[:i] + new + s[i + len(old):]
    return f


def op_only(old, op):
    """in a loop body over ALU ops: skip the statement for operation <op> (n == op)"""
    def f(s):
        assert s.count(old) >= 1, old
        return s.replace(old, f'if n != "{op}": {old}')
    return f


MUT = {}      # name -> (what, [edit fns], expected: "caught" | ("equiv", reason))


def add(name, what, edits, expect="caught"):
    MUT[name] = (what, edits, expect)


# ---- plan section 5: JMP routine
add("imm_ofs0", "JMP reads the target from window offset 0 (the mode byte)", [blk(lambda b: b.replace("IMM_OFS=1", "IMM_OFS=0"))])
add("asel_pc", "target = PC + imm (PC-relative) instead of absolute", [blk(lambda b: b.replace('ASEL=A["IMM"], ALU_OP=ALU["PASSA"]', 'ASEL=A["PC"], BSEL=A["IMM"], ALU_OP=ALU["ADD"]'))])
add("consume4", "JMP imm word CONSUME 4", [blk(lambda b: b.replace("CONSUME=5", "CONSUME=4"))])
add("consume6", "JMP imm word CONSUME 6", [blk(lambda b: b.replace("CONSUME=5", "CONSUME=6"))])
add("no_redirect", "missing REDIRECT", [blk(lambda b: b.replace(", REDIRECT=1", ""))])
add("flag_upd", "FLAG_UPD on the JMP word", [blk(lambda b: b.replace("REDIRECT=1)", "REDIRECT=1, FLAG_UPD=15)"))])
add("two_idle", "two idle words after REDIRECT instead of three", [blk(lambda b: b.replace("[word()] * 3", "[word()] * 2"))],
    ("equiv", "the following jmp disp word covers E3 (plan section 10 #3), so the effect of the redirect is the same"))
add("no_regdir_guard", "JMP does not trap register direct", [blk(lambda b: b.replace('        br(C_REGDIR, "trap_mode")\n', ""))],
    ("equiv", "register direct exists only in modm 1 and D7 is not dispatched, so the guard is unreachable (plan section 10 #2); "
              "checked structurally: no supported modm-0 entry is register direct, dtab[0xD7] is trap"))
add("no_imm_guard_jmp", "JMP does not trap immediates (#q / #imm32 as a jump address)", [blk(lambda b: b.replace('        br(C_IMM, "trap_mode")\n', ""))])
# ---- tables
add("mtab_imm", "mtab[0x0F3] marked immediate ((1<<1)|(3<<2)): /abs seen as #imm32",
    [sub_once('mtab[0x0F3] = (0x14C << 12) | sym["mret"]', 'mtab[0x0F3] = (((1 << 1) | (3 << 2)) << 12) | sym["mret"]')])
add("mtab_size16", "mtab[0x0F3] descriptor offset size 16 bits (0x148)",
    [sub_once('mtab[0x0F3] = (0x14C << 12) | sym["mret"]', 'mtab[0x0F3] = (0x148 << 12) | sym["mret"]')],
    ("equiv", "JMP does not read the descriptor's size bits (plan section 10 #3); the behavioural mutant is deferred to R3 (first AGU "
             "user); checked structurally: the builder asserts mtab[0x0F3] == (0x14C << 12) | mret"))
add("dtab_d7", "dtab[0xD7] = jmp", [sub_once('        dtab[0xD6] = sym["jmp"]', '        dtab[0xD6] = sym["jmp"]\n        dtab[0xD7] = sym["jmp"]')])
# ---- guards: one mutant per guarded caller
for op in ("movw",):
    pass
add("guard_movw_d1", "no value guard on MOV.W <src>,Rd (F2 D=1)", [sub_nth(GUARD, "pass", 0)])
add("guard_movw_f1", "no value guard on MOV.W F1 source", [sub_nth(GUARD, "pass", 1)])
for op in ("addw", "subw", "cmpw", "andw"):
    nm = op[:-1]
    add(f"guard_{nm}_d1", f"no value guard on {nm.upper()}.W F2 D=1 source",
        [sub_once(f'        br(C_IMM, "trap_mode", pol=1)                   # R1b guard (value source)\n        exe(1,',
                  f'        if n != "{op}": br(C_IMM, "trap_mode", pol=1)   # (mutant)\n        exe(1,')])
    add(f"guard_{nm}_f1", f"no value guard on {nm.upper()}.W F1 source",
        [sub_once('        br(C_IMM, "trap_mode", pol=1)                   # R1b guard (value source)\n        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A["IMM"], ALU_OP=ALU["PASSA"],\n          RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)\n        jmp(f"{n}_f1_dst")',
                  f'        if n != "{op}": br(C_IMM, "trap_mode", pol=1)   # (mutant)\n        w(IMM_LD=1, IMM_OFS=1, IMM_SZ=2, CONSUME=5, ASEL=A["IMM"], ALU_OP=ALU["PASSA"],\n          RES_SEL=RES["ALU"], WR_DST=WR["TEMP"], TADDR=T0)\n        jmp(f"{{n}}_f1_dst")')])
add("guard_cmp_op2", "no value guard on CMP operand 2 (#imm32)",
    [sub_once('            br(C_IMM, "trap_mode", pol=1)               # R1b guard (value operand 2)', '            pass')])
# ---- destination classifiers: two mutants each (section 11 #1)
add("dst_movw_noimm", "MOV.W destination classifier without its C_IMM branch (imm dest -> mode instead of immdest)",
    [sub_once('    br(C_IMM, "trap_immdest")                           # R1b: an immediate destination ...', '    pass')])
add("dst_movw_notrapmode", "MOV.W destination classifier whose trailing trap is trap_immdest instead of trap_mode",
    [sub_once('    jmp("trap_mode")                                    # ... else (/abs, memory modes) trap_mode', '    jmp("trap_immdest")')])
for op in ("addw", "subw", "andw"):
    nm = op[:-1]
    add(f"dst_{nm}_noimm", f"{nm.upper()}.W destination classifier without its C_IMM branch",
        [sub_once('            br(C_IMM, "trap_immdest")                   # immediate destination: the references disagree',
                  f'            if n != "{op}": br(C_IMM, "trap_immdest")')])
    add(f"dst_{nm}_notrapmode", f"{nm.upper()}.W destination classifier whose trailing trap is trap_immdest",
        [sub_once('            jmp("trap_mode")                            # R1b: anything else (/abs) is a mode trap',
                  f'            jmp("trap_immdest" if n == "{op}" else "trap_mode")')])
add("dst_getpsw_noimm", "GETPSW classifier without its C_IMM branch",
    [sub_once('    br(C_IMM, "trap_immdest")                           # an immediate destination', '    pass')],
    ("equiv", "GETPSW is modm 1 (opcode bit 0); modm 1 has no immediate entry and F7 F3 traps at mtab[0x1F3] first (plan section 11 #1); "
              "checked structurally: every supported modm-1 entry is register direct (builder assertion)"))
add("dst_getpsw_notrapmode", "GETPSW classifier whose trailing trap is trap_immdest",
    [sub_once('    jmp("trap_mode")                                    # R1b: anything else is a mode trap', '    jmp("trap_immdest")')],
    ("equiv", "unreachable: every non-register modm-1 entry already traps in the mode lookup (plan section 11 #1); same structural check"))


def make_mc(out, name, edits):
    """write the mutated builder source, run it (asserts on, then off), return (mcdir, mcdir_s0, assertion message)"""
    d = out / "mut" / name
    d.mkdir(parents=True, exist_ok=True)
    s = SRC
    for e in edits:
        s = e(s)
    s = s.replace("HERE = Path(__file__).resolve().parent", f"HERE = Path({str(HERE)!r})")
    (d / "mcgen.py").write_text(s)
    msg = None
    r = subprocess.run([sys.executable, str(d / "mcgen.py"), str(d / "mc")], capture_output=True, text=True)
    if r.returncode:
        msg = (r.stderr.strip().splitlines() or ["?"])[-1][:140]
        (d / "mcgen_noassert.py").write_text(s.replace("    self_check()\n", "").replace("    check_tables(sym, dtab, mtab, smc_test)\n", ""))
        gen = d / "mcgen_noassert.py"
    else:
        gen = d / "mcgen.py"
    for sub, flags in (("mc", []), ("mc_s0", ["--reset-native"])):
        r = subprocess.run([sys.executable, str(gen), str(d / sub)] + flags, capture_output=True, text=True)
        if r.returncode:
            return None, None, "builder failed even without assertions: " + r.stderr[-200:]
    return d / "mc", d / "mc_s0", msg


def run_suite(x, mc, mc_s0):
    """all V2 programs of the suite on the mutated microcode; returns list of (program, config, failures)"""
    jobs = []
    for n, m in x.man.items():
        if m["v2"] is None or m.get("v2mc") == "smc":
            continue
        if m["where"] == "s0":
            jobs.append((n, m, "", None))
        else:
            jobs.append((n, m, "", None))
            if m.get("v2_ack"):
                for cfg in RR.CONFIGS:
                    jobs.append((n, m, cfg, m["extra"]["limit"]))
            if m["cls"] in ("pos",) and n.startswith(("j1_", "j4_", "j11_s5")):
                jobs.append((n, m, "+wide", None))

    def one(j):
        n, m, cfg, ack = j
        t = RR.v2_text(x, n, m, cfg, ack, mc=mc, s0mc=mc_s0)
        return n, cfg, ack is not None, C.check_v2(m, t, x.phase, ack is not None)
    out = []
    with ThreadPoolExecutor(x.jobs) as ex:
        for r in ex.map(one, jobs):
            if r[3]:
                out.append(r)
    return len(jobs), out


def structural(x):
    """tables-level facts used by the equivalence arguments"""
    sys.path.insert(0, str(HERE))
    import m1_m11_microcode as B
    res = []
    p, sym, dtab, mtab = B.build()
    trap, tm = sym["trap"], sym["trap_mode"]
    sup0 = [i for i, v in mtab.items() if i < 0x100 and (v & 0xFFF) not in (trap, tm)]
    res.append(("no supported modm-0 entry is register direct", all(not ((mtab[i] >> 12) & 1) for i in sup0)))
    res.append(("dtab[0xD7] is trap, dtab[0xD6] is jmp", dtab[0xD7] == trap and dtab[0xD6] == sym["jmp"]))
    sup1 = [i for i, v in mtab.items() if i >= 0x100 and (v & 0xFFF) not in (trap, tm)]
    res.append(("every supported modm-1 entry is register direct", all(((mtab[i] >> 12) & 1) for i in sup1)))
    res.append(("mtab[0x0F3] == (0x14C<<12)|mret", mtab[0x0F3] == (0x14C << 12) | sym["mret"]))
    odd = [i for i, v in mtab.items() if (v & 0xFFF) not in (trap, tm) and not (((v >> 12) & 1) or ((v >> 12) & 2))]
    if (mtab[0x0F2] & 0xFFF) == trap or (mtab[0x0F2] & 0xFFF) == tm:
        res.append(("0x0F3 is the only supported non-register non-immediate entry", odd == [0x0F3]))
    else:      # R4b (docs/r4-movea-pcrel-plan.md 5.5 item 5): the classification invariant replaces `odd == [0x0F3]`
        res.append(("the supported non-register non-immediate entries are exactly 0x0F3 (/abs, 0x14C) and 0x0F2 (disp32[PC], 0x88C; R4b supersession)",
                    odd == [0x0F2, 0x0F3] and mtab[0x0F2] >> 12 == 0x88C))
    for st in (False, True):
        for sm in (False, True):
            for rn in (False, True):
                p2, s2, d2, m2 = B.build(st, sm, rn)       # build() runs check_tables
                res.append((f"isolation/asserts, smc_test={st} smc_merged={sm} reset_native={rn}",
                            (d2[0xD6] == s2["trap"] and m2[0x0F3] == s2["trap_mode"]) if st else (d2[0xD6] == s2["jmp"])))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    x = RR.Ctx()
    x.out, x.phase, x.jobs = Path(a.out), "r1b", a.jobs
    x.progs = x.out / "progs"
    x.logs = x.out / "logs"
    x.man = json.load(open(x.progs / "r1_manifest.json"))
    # control: the unmutated builder passes the whole suite
    n_runs, fails = run_suite(x, x.out / "mc", x.out / "mc.s0")
    print(f"unmutated builder: {n_runs} runs, {len(fails)} failing")
    bad = 0 if not fails else 1
    rows = []
    for name, (what, edits, expect) in MUT.items():
        mc, mc_s0, msg = make_mc(x.out, name, edits)
        if mc is None:
            rows.append((name, what, "ERROR", msg, expect))
            bad += 1
            continue
        n, f = run_suite(x, mc, mc_s0)
        caught = bool(f)
        first = f[0] if f else None
        sample = f"{first[0]}{' ['+first[1]+']' if first[1] else ''}: {first[3][0][:80]}" if first else ""
        names = sorted({r[0] for r in f})
        ok = caught if expect == "caught" else (not caught)
        if expect != "caught" and "assert" in (msg or "").lower() or (msg and "Assertion" in msg):
            pass
        rows.append((name, what, "CAUGHT" if caught else "NOT CAUGHT", f"{len(f)}/{n} runs fail; first: {sample}; programs: {names[:6]}"
                     + (f"; builder self-assertion fires: {msg}" if msg else ""), expect, ok))
        bad += (not ok)
    print("\n================ R1b MUTANTS ================")
    for r in rows:
        name, what, res, ev, expect = r[:5]
        ok = r[5] if len(r) > 5 else False
        if expect == "caught":
            lab = "CAUGHT    " if ok else "MISSED    "
        else:
            lab = "EQUIVALENT" if ok else "UNEXPECTED"
        print(f"{lab} {name}: {what}\n           {ev}" + ("" if expect == "caught" else f"\n           reason: {expect[1]}"))
    print("\nstructural checks:")
    for what, ok in structural(x):
        print(f"{'ok  ' if ok else 'FAIL'} {what}")
        bad += (not ok)
    ncaught = sum(1 for r in rows if len(r) > 5 and r[4] == "caught" and r[5])
    neq = sum(1 for r in rows if len(r) > 5 and r[4] != "caught" and r[5])
    print(f"\nR1b MUTANTS {'PASS' if not bad else 'FAIL'}: {len(rows)} mutants: {ncaught} caught, {neq} equivalent (as expected), "
          f"{len(rows) - ncaught - neq} unexpected")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
