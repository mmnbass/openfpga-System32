#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R1a driver (called by run_r1.sh after the benches are built): runs the
references, upstream and V2 on the m1_r1.py programs, checks every log against
the INDEPENDENT expectations (m1_r1_check.py), runs the negative controls, and
prints a PASS/FAIL summary. Exit status 0 only if everything passes and every
negative control is rejected.
"""
import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "ref"))
import m1_r1_check as C  # noqa: E402

CONFIGS = ["", "+memwait=2", "+memwait=5 +memjit=4", "+wide"]
M32 = 0xFFFFFFFF
results = {}          # category -> [pass, total, [failures]]
negctl = []           # (id, description, caught(bool), evidence)


def note(cat, ok, what):
    r = results.setdefault(cat, [0, 0, []])
    r[1] += 1
    if ok:
        r[0] += 1
    else:
        r[2].append(what)


def sh(cmd, timeout=600):
    """R3a Codex-r5 fixes (should-fix 3): the output is read as BYTES and decoded without newline translation (C.decode_log);
    subprocess.run(text=True) turned CRLF into LF before any evaluator could see the CR."""
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    r.stdout, r.stderr = C.decode_log(r.stdout), C.decode_log(r.stderr)
    return r


class Ctx:
    pass


def symbols(mcdir):
    d = {}
    for line in open(Path(mcdir) / "m1_symbols.txt"):
        k, v = line.strip().split("=")
        d[k] = v
    return d


def v2_cmd(sim, mc, extra, syms=None):
    s = syms or symbols(mc)
    return [sim, f"+mc={mc}", f"+haltpc={s['halt']}", f"+trappc={s['trap']}", f"+trapmodepc={s['trap_mode']}",
            f"+trapimmpc={s['trap_immdest']}", "+lockstep", "+counters", "+drain=300"] + extra


# ------------------------------------------------------------------ runs
def ref_text_cli(x, name, m, ext):
    cmd = ["python3", str(HERE / "m1_ref.py"), "--lockstep", "--counters"] + (["--ext"] if ext else [])
    if m["where"] == "s0":
        cmd += ["--pc", "0xFFFFFFF0", "--rom-mirror", "--segs", str(x.progs / f"{name}.segs"), "-"]
    else:
        cmd += [str(x.progs / f"{name}.bin")]
    r = sh(cmd)
    return r.stdout + (("\nREFERR " + r.stderr[-300:]) if r.returncode else "")


def up_text(x, name, m):
    if m["where"] == "s0":
        cmd = [str(x.out / "obj_s0up/sim"), f"+rom={x.progs / (name + '.rom.hex')}", "+maxinstr=1000", "+counters"]
    else:
        cmd = [str(x.out / "obj_up/sim"), f"+img={x.progs / (name + '.hex')}", "+maxcyc=200000", "+lockstep", "+counters"]
    r = sh(cmd)
    return r.stdout + r.stderr


def v2_text(x, name, m, cfg, ack=None, mc=None, s0mc=None, extra_args=()):
    mc = mc or (x.out / ("mc.smc" if m.get("v2mc") == "smc" else "mc"))
    if m["where"] == "s0":
        extra = [f"+rom={x.progs / (name + '.rom.hex')}", "+maxinstr=1000", "+maxcyc=400000"] + cfg.split()
        cmd = v2_cmd(str(x.out / "obj_s0v2/sim"), s0mc or x.out / "mc.s0", extra)
    else:
        extra = [f"+img={x.progs / (name + '.hex')}"] + cfg.split()
        if ack is not None:
            extra += ["+maxcyc=20000", f"+ackmax={ack}"]
            if m["extra"].get("ackrel") is not None:
                extra += [f"+ackrel={m['extra']['ackrel']}"]
        else:
            extra += ["+maxcyc=400000"]
        cmd = v2_cmd(str(x.out / "obj_v2/sim"), mc, extra + list(extra_args))
    r = sh(cmd)
    return r.stdout + r.stderr


def tagof(cfg):
    return (cfg or "default").replace("+", "").replace(" ", "_").replace("=", "-")


def save(x, name, tag, text):
    (x.logs / f"{name}.{tag}").write_text(text)


# ------------------------------------------------------------------ stages
def stage_refs(x):
    print("== references and upstream on every positive program")
    pos = [(n, m) for n, m in x.man.items() if m["cls"] in ("pos", "nonvac", "br8x") and m["where"] in ("low", "s0")]
    jobs = []
    for n, m in pos:
        for kind in m.get("refs", ["ref", "ext", "up"]):
            jobs.append((n, m, kind))

    def one(job):
        n, m, kind = job
        if kind == "up":
            t = up_text(x, n, m)
        else:
            t = ref_text_cli(x, n, m, kind == "ext")
        save(x, n, kind, t)
        f = C.check_ref(m, t, ref=(kind != "up"))
        return n, m, kind, f
    with ThreadPoolExecutor(x.jobs) as ex:
        for n, m, kind, f in ex.map(one, jobs):
            cat = f"{kind} ({'S0 harness' if m['where'] == 's0' else 'M1 bench'})" if kind == "up" else \
                f"{'ref_ext' if kind == 'ext' else 'ref'} ({'S0 adapter' if m['where'] == 's0' else 'plain'})"
            note(cat, not f, f"{n}: {f[:2]}")
    # all three agree with each other on lock-step records and counters (not only with the expectation)
    agree = tot = 0
    for n, m in pos:
        kinds = m.get("refs", ["ref", "ext", "up"])
        if len(kinds) < 3:
            continue
        ds = {k: C.parse(C.read_log(x.logs / f"{n}.{k}")) for k in kinds}
        tot += 1
        same = ds["ref"]["L"] == ds["ext"]["L"] == ds["up"]["L"] and \
            [(c["dr"], c["dw"], c["priv"]) for c in ds["ref"]["C"]] == [(c["dr"], c["dw"], c["priv"]) for c in ds["ext"]["C"]]
        note("ref == ext == upstream (lock-step records)", same, n)
        agree += same
    # upstream's counters equal the references' except the documented v60_ref destination over-read
    return


def stage_v2(x):
    print(f"== V2, phase {x.phase}")
    jobs = []
    for n, m in x.man.items():
        if m["v2"] is None:
            continue
        for cfg in CONFIGS:
            jobs.append((n, m, cfg, None))
            if m.get("v2_ack"):
                jobs.append((n, m, cfg, m["extra"]["limit"]))

    def one(job):
        n, m, cfg, ack = job
        t = v2_text(x, n, m, cfg, ack)
        save(x, n, "v2_" + tagof(cfg) + ("_ack" if ack is not None else ""), t)
        return job, C.check_v2(m, t, x.phase, ack is not None)
    with ThreadPoolExecutor(x.jobs) as ex:
        for (n, m, cfg, ack), f in ex.map(one, jobs):
            cat = f"V2 [{cfg or 'default'}]" + (" + withheld fetch (J12/BR8, released at the redirect)" if ack is not None else "") \
                + (" S0 harness" if m["where"] == "s0" else "")
            note(cat, not f, f"{n}: {f[:2]}")


# ------------------------------------------------------------------ negative controls
def ref_inproc(x, name, m, mutate=None, nocount=False):
    """The Python reference in-process (optionally with a monkeypatched JMP), as text."""
    import m1_ref as MR
    import v60_ref as R
    MR.LOCKSTEP, MR.COUNTERS = True, True
    orig_step, orig_w, orig_r = R.V60.step, MR.CountingMem.w, MR.CountingMem.r
    try:
        if mutate:
            R.V60.step = mutate(orig_step)
        if nocount:                    # forced-zero counter mutant: the memory model stops counting
            MR.CountingMem.w = lambda self, a, d, v: R.Mem.w(self, a, d, v)
            MR.CountingMem.r = lambda self, a, d: R.Mem.r(self, a, d)
        if m["where"] == "s0":
            segs = MR.parse_segs(str(x.progs / f"{name}.segs"))
            img, kw = b"", dict(start_pc=0xFFFFFFF0, segs=segs, mirror24=True, rom_mirror=True)
        else:
            img, kw = (x.progs / f"{name}.bin").read_bytes(), dict(start_pc=0)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                cpu, mem, n = MR.run(img, 20000, **kw)
        except Exception as e:                      # a mutant can run off into code the reference rejects
            return buf.getvalue() + f"\nREFERR {type(e).__name__}: {e}\n"
        return buf.getvalue() + MR.dump(cpu, mem, n)
    finally:
        R.V60.step, MR.CountingMem.w, MR.CountingMem.r = orig_step, orig_w, orig_r


def jmp_mutants():
    import v60_ref as R

    def mk(kind):
        def wrap(orig):
            def step(self):
                if self._b(0) == 0xD6 and self._b(1) == 0xF3:
                    tgt = self._d(2)
                    if kind == "pcrel":
                        self.pc = (self.pc + tgt) & M32             # PC-relative instead of absolute
                    elif kind == "offset":
                        self.pc = self._d(1)                        # target read from the wrong byte offset (1, the mode byte)
                    elif kind == "offset3":
                        self.pc = self._d(3)                        # ... or offset 3
                    elif kind == "flag":
                        self.pc = tgt
                        self.cy ^= 1                                # a flag changed
                    elif kind == "reg":
                        self.pc = tgt
                        self.r[7] = (self.r[7] + 1) & M32           # a register changed
                    elif kind == "dataread":
                        self.mem.r(tgt, 2)                          # an extra data read
                        self.pc = tgt
                    return
                orig(self)
            return step
        return wrap
    return {"PC-relative target": mk("pcrel"), "target read from byte offset 1 (the mode byte) instead of 2": mk("offset"),
            "target read from byte offset 3 instead of 2": mk("offset3"),
            "a flag changed (CY toggled)": mk("flag"), "a register changed (R7 += 1)": mk("reg"),
            "an extra data read (mem.r at the target)": mk("dataread")}


def negctl_refs(x):
    print("== negative controls (a): reference mutants (in memory only)")
    pos = [(n, m) for n, m in x.man.items() if m["cls"] == "pos"]
    for desc, mut in jmp_mutants().items():
        caught, first = 0, None
        for n, m in pos:
            t = ref_inproc(x, n, m, mut)
            f = C.check_ref(m, t, ref=True)
            if f:
                caught += 1
                first = first or f"{n}: {f[0][:110]}"
        negctl.append(("a", f"reference mutant: {desc}", caught > 0, f"{caught}/{len(pos)} programs fail; first: {first}"))
    # sanity: the unmutated in-process reference passes all of them
    ok = all(not C.check_ref(m, ref_inproc(x, n, m), ref=True) for n, m in pos)
    negctl.append(("a0", "control: the UNmutated in-process reference passes every positive program", ok, "baseline for (a)"))


def write_mc(x, name, B, p, sym, dtab, mtab):
    mc = x.out / name
    mc.mkdir(exist_ok=True)
    B.write_hex(mc / "m1_cstore.hex", p, (B.CW_W + 3) // 4)
    B.write_hex(mc / "m1_dispatch.hex", [dtab[i] for i in range(256)], 6)
    B.write_hex(mc / "m1_mode.hex", [mtab[i] for i in range(512)], 6)
    (mc / "m1_symbols.txt").write_text("".join(f"{k}={v}\n" for k, v in sym.items()))
    return mc


def g1_on(x, mc, g):
    rows = []

    def one(job):
        n, m = job
        t = v2_text(x, n, m, "", None, mc=mc)
        return n, m, t, C.check_v2(m, t, x.phase)
    with ThreadPoolExecutor(x.jobs) as ex:
        for n, m, t, f in ex.map(one, g):
            d = C.parse(t)
            did = f"TRAP {d['TRAP'][0]}" if d["TRAP"] else f"halted={d['END'] and d['END'].get('halted')} (executed)"
            rows.append((m["extra"]["site"], bool(f), did))
    return rows


def negctl_table(x):
    print("== negative controls (b): table mutants, /abs given an immediate descriptor (in memory, builder untouched)")
    import m1_m11_microcode as B
    p, sym, dtab, mtab = B.build()          # whatever the production tables are in this phase
    g = [(n, m) for n, m in x.man.items() if m["cls"] == "guard"]
    jp = [(n, x.man[n]) for n in ("j1_fwd_t0", "j3_back_chain", "j9_first_instr", "j12_k6")]
    imm = (((1 << 1) | (3 << 2)) << 12) | sym["mret"]                # /abs silently seen as #imm32

    def run(mc):
        rows = g1_on(x, mc, g)
        rows += [("JMP:" + n, bool(C.check_v2(m, v2_text(x, n, m, "", None, mc=mc), x.phase)), "") for n, m in jp]
        return rows
    base = run(write_mc(x, "mc_prod_copy", B, p, sym, dtab, dict(mtab)))
    base_ok = not any(r[1] for r in base)
    negctl.append(("b0", f"control: unmutated production tables pass all 21 G1 cases and the JMP cases (phase {x.phase})", base_ok,
                   "baseline for (b); failing: " + str([r[0] for r in base if r[1]])))
    # b: mode entry 0x0F3 (modm 0, byte F3)
    mt = dict(mtab)
    mt[0x0F3] = imm
    rows = run(write_mc(x, "mc_mut_tab", B, p, sym, dtab, mt))
    if x.phase == "r1a":
        equiv = {"getpsw"}      # GETPSW is bank 1: F7 F3 reaches mtab[0x1F3] (plan section 11 row 1)
        miss = [r[0] for r in rows if not r[1] and r[0] not in equiv and not r[0].startswith("JMP:")]
        ok = not miss and all(not r[1] for r in rows if r[0] in equiv)
        what = ("G1 fails for %d/21 call sites (getpsw unaffected: bank 1, equivalent under R1 tables; JMP cases unaffected: "
                "D6 is not dispatched in r1a); V2 did: " % sum(r[1] for r in rows if not r[0].startswith("JMP:")) +
                "; ".join(f"{s_}->{d}" for s_, c, d in rows if c) + (f"; NOT caught: {miss}" if miss else ""))
    else:
        failed = [r[0] for r in rows if r[1]]
        ok = base_ok and bool(failed)
        what = f"phase r1b: failing cases (any = caught; the R1b guards are expected to turn /abs-as-immediate into trap_mode): {failed}"
    negctl.append(("b", "table mutant: mtab[0x0F3] = ((1<<1)|(3<<2)) descriptor (/abs seen as #imm32)", ok, what))
    # b2: the bank-1 entry 0x1F3, which only GETPSW reaches
    mt = dict(mtab)
    mt[0x1F3] = imm
    rows2 = run(write_mc(x, "mc_mut_tab1", B, p, sym, dtab, mt))
    c2 = [r[0] for r in rows2 if r[1]]
    negctl.append(("b2", "table mutant: mtab[0x1F3] = immediate descriptor (the bank-1 /abs entry GETPSW reaches)",
                   base_ok and ("getpsw" in c2) and all(c == "getpsw" for c in c2) if x.phase == "r1a" else base_ok and "getpsw" in c2,
                   f"fails: {c2}"))


def negctl_counters(x):
    print("== negative controls (c): counter non-vacuity and forced-zero mutants")
    # non-vacuity first: the real counters are nonzero where data/privileged accesses happen
    nv = {}
    for nm, kinds in (("nv_store", ["v2_default", "up", "ref", "ext"]), ("nv_load", ["up", "ref", "ext"]),
                      ("nv_ldpr", ["up", "ext"])):
        for k in kinds:
            if k == "v2_default" and x.man[nm]["v2"] is None:
                continue
            d = C.parse(C.read_log(x.logs / f"{nm}.{k}"))
            nv[(nm, k)] = [(c["dr"], c["dw"], c["priv"]) for c in d["C"]]
    ev = []
    ok = True
    for (nm, k), v in nv.items():
        idx = {"nv_store": 2, "nv_load": 1, "nv_ldpr": 1}[nm]
        ev.append(f"{nm}/{k}: (dr,dw,priv)@{idx}={v[idx]}")
        want = {"nv_store": 1, "nv_load": 1, "nv_ldpr": 1}[nm]
        pos_i = {"nv_store": 1, "nv_load": 0, "nv_ldpr": 2}[nm]
        if v[idx][pos_i] < want:
            ok = False
    negctl.append(("c0", "non-vacuity: counters are nonzero on a data-writing / data-reading / LDPR instruction",
                   ok, "; ".join(ev)))

    # (c1) Python reference: memory model with forced-zero counters
    caught = []
    for nm in ("nv_store", "nv_load"):
        m = x.man[nm]
        t = ref_inproc(x, nm, m, None, nocount=True)
        f = C.check_ref(m, t, ref=True)
        caught.append((nm, f[0][:90] if f else None))
    negctl.append(("c1", "forced-zero counter mutant: Python reference", all(c[1] for c in caught), f"{caught}"))

    # (c2) upstream bench and V2 observer: temporary copies with forced-zero counters, rebuilt
    mut = x.out / "mut"
    mut.mkdir(exist_ok=True)
    root = x.root
    # upstream
    tb = (HERE / "tb/tb_m1_up.sv").read_text()
    tb2 = tb.replace("if (cpu.dack) begin if (c_we) n_dw = n_dw + 1; else n_dr = n_dr + 1; end",
                     "if (cpu.dack) begin if (c_we) n_dw = n_dw + 0; else n_dr = n_dr + 0; end")
    assert tb2 != tb
    (mut / "tb_m1_up_zero.sv").write_text(tb2)
    flags = ["--binary", "--timing", "--threads", "1", "-Wno-fatal", "-Wno-WIDTHTRUNC", "-Wno-WIDTHEXPAND", "-Wno-UNOPTFLAT",
             "-Wno-CASEINCOMPLETE", "-Wno-BLKANDNBLK", "-Wno-MULTIDRIVEN", "-Wno-INITIALDLY", "-Wno-DECLFILENAME",
             "-Wno-PINMISSING", "-Wno-UNSIGNED", "-Wno-WIDTH", "-Wno-TIMESCALEMOD", "+define+SIMULATION", "+define+S32_V60_NO_FP=1"]
    U = root / "references/s32/rtl/cpu/v60"
    r = sh(["verilator"] + flags + ["--top-module", "tb_m1_up", "--Mdir", str(mut / "obj_up_zero"), "-o", "sim",
                                   str(U / "s32_v60.sv"), str(U / "s32_v60_bus.sv"), str(HERE / "tb/m1_mem.sv"),
                                   str(mut / "tb_m1_up_zero.sv")])
    res = []
    if r.returncode:
        negctl.append(("c2", "forced-zero counter mutant: upstream bench", False, "mutant build failed: " + r.stderr[-200:]))
    else:
        for nm in ("nv_store", "nv_load"):
            m = x.man[nm]
            t = sh([str(mut / "obj_up_zero/sim"), f"+img={x.progs / (nm + '.hex')}", "+maxcyc=200000", "+lockstep", "+counters"]).stdout
            f = C.check_ref(m, t)
            res.append((nm, f[0][:90] if f else None))
        negctl.append(("c2", "forced-zero counter mutant: upstream bench (rebuilt copy)", all(c[1] for c in res), f"{res}"))
    # V2 observer
    ob = (HERE / "tb/v2_sim_observer.sv").read_text()
    ob2 = ob.replace("c_dr[tag3 % 8] + 1;", "c_dr[tag3 % 8] + 0;").replace("c_dw[tag3 % 8] + 1;", "c_dw[tag3 % 8] + 0;") \
            .replace("c_pw[tag3 % 8] + 1;", "c_pw[tag3 % 8] + 0;")
    assert ob2.count("+ 0;") == 3
    (mut / "v2_sim_observer_zero.sv").write_text(ob2)
    R = root / "experiments/V2-datapath-floor/rtl"
    files = [str(R / l.strip()) for l in (R / "files.f").read_text().split()]
    r = sh(["verilator", "--binary", "--timing", "--threads", "1", "-Wno-fatal", "-Wno-lint", "-Wno-style", "-Wno-TIMESCALEMOD",
            "+define+SIMULATION", f"-I{R}", "--top-module", "tb_m1_v2", "--Mdir", str(mut / "obj_v2_zero"), "-o", "sim"] + files +
           [str(U / "s32_v60_bus.sv"), str(HERE / "tb/m1_mem.sv"), str(mut / "v2_sim_observer_zero.sv"),
            str(HERE / "tb/v2_sim_checks.sv"), str(HERE / "tb/tb_m1_v2.sv")])
    if r.returncode:
        negctl.append(("c3", "forced-zero counter mutant: V2 observer", False, "mutant build failed: " + r.stderr[-200:]))
    else:
        m = x.man["nv_store"]
        t = sh(v2_cmd(str(mut / "obj_v2_zero/sim"), x.out / "mc.smc", [f"+img={x.progs / 'nv_store.hex'}", "+maxcyc=400000"])).stdout
        f = C.check_v2(m, t, x.phase)
        negctl.append(("c3", "forced-zero counter mutant: V2 observer (rebuilt copy), nv_store under --smc-test",
                       bool(f), f"{f[:1]}"))
    x.mut_flags = (flags, U, R, files)


def negctl_v2_microcode(x):
    """In-memory microcode mutants of the NOP routine (never written to the builder file): a data read
    and a privileged-register write, so that V2's dr/priv counters are shown to fire and be caught."""
    print("== negative controls (e): V2 microcode mutants of NOP (in memory): data read / privileged write")
    import m1_m11_microcode as B
    from microasm import word
    nops = [(n, m) for n, m in x.man.items() if m["cls"] == "pos" and m["where"] == "low"
            and any(r.split()[2] == "op=cd" for r in m["exp_L"])]
    for tag, extra, what in (("dr", dict(BUS_OP=1, BUS_SIZE=3), "a data-bus READ"), ("priv", dict(WR_DST=3), "a privileged-register write")):
        p, sym, dtab, mtab = B.build()
        p = list(p)
        p[sym["nop"]] |= word(**extra)
        mc = write_mc(x, f"mc_mut_nop_{tag}", B, p, sym, dtab, mtab)
        caught, first, seen = 0, None, set()
        for n, m in nops[:12]:
            t = v2_text(x, n, m, "", None, mc=mc)
            f = C.check_v2(m, t, x.phase)
            d = C.parse(t)
            seen |= {(c["dr"], c["priv"]) for c in d["C"]}
            if any("counters" in g for g in f):
                caught += 1
                first = first or f"{n}: {[g for g in f if 'counters' in g][:1]}"
        negctl.append(("e", f"V2 microcode mutant: NOP does {what}", caught == len(nops[:12]) and caught > 0,
                       f"{caught}/{len(nops[:12])} NOP programs rejected; counters seen (dr,priv) {sorted(seen)}; first: {first}"))


def negctl_trappc(x):
    print("== negative controls (d, f, g, h): wrong expected trap, corrupted logs, side effects on trap/wait, phase flip")
    import copy
    import re
    # (d) wrong expected trap: on a program that traps in BOTH phases (N2: D7 is never dispatched)
    n = "n2_d7_abs"
    m = x.man[n]
    t = C.read_log(x.logs / f"{n}.v2_default")
    base = C.check_v2(m, t, x.phase)
    negctl.append(("d0", f"control: V2's log of {n} passes the phase-{x.phase} expectation", not base, f"{base}"))
    for what, edit in (("trap PC + 1", lambda e: e.update(pc=e["pc"] + 1)),
                       ("trap kind mode instead of opcode", lambda e: e.update(trap="mode")),
                       ("retired count + 1", lambda e: e.update(n=e["n"] + 1))):
        m2 = copy.deepcopy(m)
        edit(m2["v2"][x.phase])
        f = C.check_v2(m2, t, x.phase)
        negctl.append(("d", f"wrong expected trap: {what}", bool(f), f"{f[:1]}"))
    # (g) the phase parameter: the OTHER phase's expectation must reject the current V2 on the JMP programs
    other = "r1b" if x.phase == "r1a" else "r1a"
    pos = [(nm, mm) for nm, mm in x.man.items() if mm["cls"] == "pos" and mm["v2"] and mm["where"] == "low"]
    rej = sum(1 for nm, mm in pos if C.check_v2(mm, C.read_log(x.logs / f"{nm}.v2_default"), other))
    negctl.append(("g", f"phase {other} expectation rejects the current V2 (phase {x.phase}) on the JMP programs", rej == len(pos),
                   f"{rej}/{len(pos)} rejected"))
    # (f) corrupted logs must be rejected by the reference check
    nj = "j1_fwd_t0"
    ref = C.read_log(x.logs / f"{nj}.up")

    def corrupt(how):
        lines = ref.splitlines()
        i = next(k for k, l in enumerate(lines) if l.startswith("L ") and " R" in l)
        j = next(k for k, l in enumerate(lines) if l.startswith("C "))
        if how == "pc":
            lines[i] = re.sub(r"pc=\w{8}", "pc=0000beef", lines[i])
        if how == "reg":
            lines[i] = re.sub(r"(R\d\d=)\w{8}", r"\g<1>0badf00d", lines[i], count=1)
        if how == "drop":
            del lines[i]
        if how == "psw":
            lines[i] += " PSW=0badf00d"
        if how == "counter":
            lines[j] = "C 0 dr=1 dw=0 priv=0"
        return "\n".join(lines) + "\n"
    for how in ("pc", "reg", "drop", "psw", "counter"):
        f = C.check_ref(x.man[nj], corrupt(how))
        negctl.append(("f", f"corrupted reference/upstream log ({how})", bool(f), f"{f[:1]}"))
    # (g2) the r1b expectation is satisfiable: it accepts the halting upstream log of every positive program
    ok = tot = 0
    for nm, mm in x.man.items():
        if mm["cls"] == "pos" and mm["v2"]:
            tot += 1
            txt = C.read_log(x.logs / f"{nm}.up") + "SIMCHECK errors=0 ipc_errors=0\n"
            ok += not C.check_v2(mm, txt, "r1b")
    negctl.append(("g2", "dry run: the r1b expectation accepts the halting upstream log of every positive program", ok == tot, f"{ok}/{tot}"))
    # (h) side effects on every V2 exit kind: trap and wait logs
    tr = C.read_log(x.logs / f"{n}.v2_default")
    for what, add in (("a data write before the expected trap (extra W record)", "W addr=00000800 size=4 data=deadbeef"),
                      ("an extra nonzero counter record (C)", "C 99 dr=1 dw=0 priv=0 psw=0"),
                      ("a nonzero counter on the trapped instruction (CU dw=1)", "CU 2 dr=0 dw=1 priv=0 psw=0"),
                      ("a privileged write by the trapped instruction (CU priv=1)", "CU 2 dr=0 dw=0 priv=1 psw=0")):
        f = C.check_v2(m, tr + add + "\n", x.phase)
        negctl.append(("h", f"trap exit: {what}", bool(f), f"{f[:1]}"))
    f = C.check_v2(m, "\n".join(l for l in tr.splitlines() if not l.startswith("CU ")), x.phase)
    negctl.append(("h", "trap exit: the CU record of the unretired instruction missing", bool(f), f"{f[:1]}"))
    wn = "j12_k1"
    if x.man[wn].get("v2_ack"):
        wt = C.read_log(x.logs / f"{wn}.v2_default_ack")
        base = C.check_v2(x.man[wn], wt, x.phase, True)
        f = C.check_v2(x.man[wn], wt + "W addr=00000800 size=4 data=deadbeef\n", x.phase, True)
        f2 = C.check_v2(x.man[wn], wt + "CU 9 dr=1 dw=0 priv=0 psw=0\n", x.phase, True)
        negctl.append(("h", "wait exit (J12 k1): control passes; an extra W record / a nonzero CU record are rejected",
                       not base and bool(f) and bool(f2), f"base={base} W:{f[:1]} CU:{f2[:1]}"))
    # (h3) a real V2 microcode mutant: the TRAP routine itself performs a data write (in memory only)
    import m1_m11_microcode as B
    from microasm import word
    p, sym, dtab, mtab = B.build()
    p = list(p)
    p[sym["trap"]] |= word(BUS_OP=2, BUS_SIZE=3)
    mc = write_mc(x, "mc_mut_trapwrite", B, p, sym, dtab, mtab)
    t2 = v2_text(x, n, m, "", None, mc=mc)
    f = C.check_v2(m, t2, x.phase)
    d2 = C.parse(t2)
    negctl.append(("h", "V2 microcode mutant: the trap routine of the trapped instruction writes data (in memory)",
                   any(("data writes" in g or "unretired" in g) for g in f),
                   f"W records {len(d2['W'])}, CU {d2['CU']}; rejected by: {f[:2]}"))


def stage_smc(x):
    """Variant isolation (plan section 10 #1): the TEST-ONLY --smc-test builds, separate and merged, M1 bench
    and reset-native (S0) bench: D6 F3 ... must trap `opcode`, /abs through every operand() site must trap
    `mode`, immediate destinations `immdest` - in BOTH phases (JMP and /abs exist only in production tables)."""
    print("== SMC isolation vectors (--smc-test builds)")
    pick_pos = ["j1_fwd_t0", "j4_start_a2", "j8_flags_z", "j11_s3_t5", "j12_k6"]
    pick_s0 = ["j10_100506", "j13_wrap_fc", "j13_upper_ff"]
    for variant in ("smc", "smcm"):
        for bench in ("m1", "s0"):
            mc = x.out / (f"mc.{variant}" if bench == "m1" else f"mc.{variant}_s0")
            names = [n for n, m in x.man.items() if m.get("v2_smc") and
                     ((bench == "m1" and m["where"] == "low" and (m["cls"] in ("guard", "immdest") or n in pick_pos)) or
                      (bench == "s0" and n in pick_s0))]
            for cfg in CONFIGS:
                def one(n):
                    m = dict(x.man[n])
                    m["v2"] = m["v2_smc"]
                    t = v2_text(x, n, m, cfg, None, mc=mc if bench == "m1" else None, s0mc=mc)
                    return n, C.check_v2(m, t, x.phase)
                with ThreadPoolExecutor(x.jobs) as ex:
                    for n, f in ex.map(one, names):
                        note(f"SMC isolation [{variant}, {'M1 bench' if bench == 'm1' else 'S0 reset-native'}] [{cfg or 'default'}]",
                             not f, f"{n}: {f[:2]}")


def negctl_br8(x):
    print("== negative controls (j): J12 mechanism proven with BR8 (supported redirect), microcode mutants in memory")
    import m1_m11_microcode as B
    from microasm import word, A, ALU, RES
    p, sym, dtab, mtab = B.build()
    p = list(p)
    base_w = dict(SIZE_SEL=2, BE_MODE=1, IMM_LD=1, IMM_OFS=1, IMM_SZ=0, IMM_SX=1, ASEL=A["PC"], BSEL=A["IMM"],
                  ALU_OP=ALU["ADD"], RES_SEL=RES["ALU"], REDIRECT=1, CONSUME=2)
    # MC-compact (GA2_MC_PACK): a packed image may sequence the BR8 word differently (fall-through -> jump / call); compare and keep the
    # sequencing fields (NXT, JADDR) of the builder's word. Unpacked: they are 0, so this is the original check and mutants.
    from microasm import FIELDS as _F
    _seq = sum(((1 << _F[k][1]) - 1) << _F[k][0] for k in ("NXT", "JADDR")) & p[sym["br8"]]
    assert word(**base_w) == p[sym["br8"]] & ~_seq, "rebuilt BR8 word differs from the builder's"
    progs = [(n, m) for n, m in x.man.items() if m["cls"] == "br8x"]
    # j0: withholding WITHOUT release deadlocks even a correct redirect (the defect this mechanism fixes)
    n0, m0 = progs[0]
    t = v2_text(x, n0, m0, "", 16, extra_args=())  # +ackrel is added from the manifest; strip it for this control
    t = sh([str(x.out / "obj_v2/sim")] + [a for a in v2_cmd(str(x.out / "obj_v2/sim"), x.out / "mc", [])[1:]] +
           [f"+img={x.progs / (n0 + '.hex')}", "+maxcyc=20000", "+ackmax=16"]).stdout
    d = C.parse(t)
    negctl.append(("j0", "no +ackrel: a correct BR8 whose sequential fetch is withheld forever never reaches HALT (the original J12 defect)",
                   d["END"] and d["END"]["halted"] == "0", f"halted={d['END'] and d['END']['halted']}, retired {d['END'] and d['END']['instrs']}"))
    for tag, upd, what in (("consume3", dict(CONSUME=3), "BR8 consumes one byte too many (needs the withheld byte)"),
                           ("consume1", dict(CONSUME=1), "BR8 consumes one byte too few"),
                           ("immsz1", dict(IMM_SZ=1), "BR8 latches a 2-byte displacement (reads the withheld byte)"),
                           ("immofs2", dict(IMM_OFS=2), "BR8 latches its displacement from byte 2 (the withheld byte)")):
        q = list(p)
        q[sym["br8"]] = word(**dict(base_w, **upd)) | _seq
        mc = write_mc(x, f"mc_mut_br8_{tag}", B, q, sym, dtab, mtab)
        res = []
        for cfg in CONFIGS:
            for n, m in progs:
                t = v2_text(x, n, m, cfg, m["extra"]["limit"], mc=mc)
                f = C.check_v2(m, t, x.phase, True)
                dd = C.parse(t)
                res.append((n, cfg or "default", bool(f), dd["END"]["halted"] if dd["END"] else "?", dd["SIMCHECK"]))
        caught = [r for r in res if r[2]]
        ev = f"{len(caught)}/{len(res)} runs rejected; sample: {caught[:1] or res[:1]}"
        if tag == "consume3":
            ok = len(caught) == len(res) and all(r[3] == "0" for r in res)       # all time out (never halt)
        elif tag == "immofs2":
            ok = len(caught) == len(res)       # waits for the missing byte (timeout) - or the checker
        elif tag == "immsz1":
            ok = len(caught) == len(res) and all(r[4] and r[4][0] > 0 for r in caught)   # the availability checker fires
        else:
            ok = len(caught) == 0 and all(r[3] == "1" and r[4] == (0, 0) for r in res)   # equivalent: halts, trace identical
        negctl.append(("jE" if tag == "consume1" else "j", f"BR8 microcode mutant, {what}" +
                       (" - classified EQUIVALENT: the redirect replaces the queue head, so leftover bytes are discarded; "
                        "passes by design, trace and checkers identical" if tag == "consume1" else ""), ok, ev))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--phase", default="r1a")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--negctl", type=int, default=1)
    ap.add_argument("--root", required=True)
    a = ap.parse_args()
    x = Ctx()
    x.out, x.phase, x.jobs, x.root = Path(a.out), a.phase, a.jobs, Path(a.root)
    x.progs = x.out / "progs"
    x.logs = x.out / "logs"
    x.logs.mkdir(exist_ok=True)
    x.man = json.load(open(x.progs / "r1_manifest.json"))
    classes = {}
    for m in x.man.values():
        k = f"{m['cls']}/{m['where']}"
        classes[k] = classes.get(k, 0) + 1
    print("programs: " + ", ".join(f"{k} {v}" for k, v in sorted(classes.items())) + f" (total {len(x.man)})")
    stage_refs(x)
    stage_v2(x)
    stage_smc(x)
    if a.negctl:
        negctl_refs(x)
        negctl_table(x)
        negctl_counters(x)
        negctl_v2_microcode(x)
        negctl_trappc(x)
        negctl_br8(x)
    print("\n================ R1a SUMMARY (phase %s) ================" % x.phase)
    bad = 0
    for cat, (p, t, fl) in results.items():
        print(f"{'PASS' if p == t else 'FAIL'}  {cat}: {p}/{t}")
        for f in fl[:5]:
            print(f"      fail: {f}")
        bad += (p != t)
    nbad = 0
    if a.negctl:
        print("\nnegative controls (each must be REJECTED / caught):")
        for cid, desc, caught, ev in negctl:
            lab = "EQUIV  " if (cid.endswith("E") and caught) else "CAUGHT " if caught else "MISSED "
            print(f"{lab} ({cid}) {desc}\n         {ev}")
            nbad += (not caught)
    ok = bad == 0 and nbad == 0
    print(f"\nR1a {'PASS' if ok else 'FAIL'}: {len(results) - bad}/{len(results)} result groups pass"
          + (f", {len(negctl) - nbad}/{len(negctl)} negative controls caught" if a.negctl else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
