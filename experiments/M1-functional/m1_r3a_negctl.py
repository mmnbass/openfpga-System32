#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R3a infrastructure-level negative controls (docs/r3-movea-plan.md section 6 infrastructure #1 / #2, R3a exits 3 and 5).
Every control must be REJECTED / caught; the driver exits non-zero otherwise.

  (a) corrected-V60X mutants of MOVEA (in memory): the contents (the v60_ref defect), a byte mask, a sign extension, the wrong
      byte offset / destination, no write, an omitted write whose value is unchanged, a duplicate write, a transient write,
      a flag change, an extra register change, a wrong length, an extra data read, a privileged write
  (b) corrupted real logs (V60X, V2 probe image, V2 trap, the model's r3b log): dropped / duplicated / altered GPR events,
      MOVEA-window counters, the missing GPREV marker, wrong expected traps, the other phase's expectation
  (c) non-vacuity of the new instrumentation: forced-zero copies of the V2 observer's G printer (rebuilt) and of the V60X
      register log
  (d) THE THREE CHECKER CONTROLS on the real V2 (probe image): an omitted unchanged-value write, a duplicate write, a transient
      write restored before a trap; each is invisible to the pre-R3a checks (lock-step records, final state, counters) and is
      caught by the GPR event log
  (e) probe_movea_value enforcement: a fixed copy of the reference, a crash, a timeout, empty output, a missing log, a wrong
      value, an unrelated difference, and the end-to-end driver / run_refs.sh exit codes
  (f) dispatch format bits of the probe (ASSUMED #2), frozen-expectation match
"""
import contextlib
import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "ref"))
import m1_r1_run as RR  # noqa: E402
import m1_r2_check as C2  # noqa: E402
import m1_r3_check as C  # noqa: E402
import m1_r1_check as C1  # noqa: E402

M32 = 0xFFFFFFFF


def ref_inproc(x, name, mutate=None, nolog=False, maxn=20000):
    """The corrected V60X in-process (optionally with a monkeypatched MOVEA), as the CLI text"""
    import m1_ref as MR
    import m1_ref_ext as X
    MR.LOCKSTEP = MR.COUNTERS = MR.PRIVEV = MR.GPREV = True
    orig_step, orig_set = X.V60X.step, MR.LoggedRegs.__setitem__
    try:
        if mutate:
            X.V60X.step = mutate(orig_step)
        if nolog:
            MR.LoggedRegs.__setitem__ = lambda self, k, v: list.__setitem__(self, k, v)
        buf = io.StringIO()
        buf.write("GPREV on\n")
        try:
            with contextlib.redirect_stdout(buf):
                cpu, mem, cnt = MR.run((x.progs / f"{name}.bin").read_bytes(), maxn, start_pc=0, ext=True)
                pr = "".join(f"PR id={i} val={cpu.priv[nm] & M32:08x}\n" for i, nm in X.PRIV.items())
                t = pr + MR.dump(cpu, mem, cnt)
            return buf.getvalue() + t
        except Exception as e:
            return buf.getvalue() + f"\nREFERR {type(e).__name__}: {e}\n"
    finally:
        X.V60X.step, MR.LoggedRegs.__setitem__ = orig_step, orig_set
        MR.PRIVEV = MR.GPREV = False
        MR.COUNTERS = MR.LOCKSTEP = False


def movea_mutants():
    def mk(kind):
        def wrap(orig):
            def step(self):
                if self._b(0) == 0x40 and (self._b(1) & 0xE0) == 0x20 and self._b(2) == 0xF3:
                    a, rd = self._d(3), self._b(1) & 31
                    v = a
                    if kind == "contents":
                        v = self.mem.r(a, 0)                        # the v60_ref defect: the byte AT the address (counts a read)
                    elif kind == "mask8":
                        v = a & 0xFF
                    elif kind == "sext8":
                        v = ((a & 0xFF) - 0x100 if a & 0x80 else a & 0xFF) & M32
                    elif kind == "sext16":
                        v = ((a & 0xFFFF) - 0x10000 if a & 0x8000 else a & 0xFFFF) & M32
                    elif kind == "ofs2":
                        v = self._d(2)
                    elif kind == "wrong_dest":
                        rd = (rd + 1) & 31
                    elif kind == "no_write":
                        self.pc += 7
                        return
                    elif kind == "omit_unchanged":
                        if self.r[rd] != v:
                            self.r[rd] = v
                        self.pc += 7
                        return
                    elif kind == "dup":
                        self.r[rd] = v
                    elif kind == "transient":
                        self.r[rd] = 7
                    elif kind == "len6":
                        self.r[rd] = v
                        self.pc += 6
                        return
                    elif kind == "len8":
                        self.r[rd] = v
                        self.pc += 8
                        return
                    self.r[rd] = v
                    if kind == "flag":
                        self.cy ^= 1
                    if kind == "extra_reg":
                        o = 6 if rd != 6 else 7
                        self.r[o] = (self.r[o] + 1) & M32
                    if kind == "dataread":
                        self.mem.r(a, 2)
                    if kind == "priv":
                        self.priv["SBR"] = v
                    self.pc += 7
                    return
                orig(self)
            return step
        return wrap
    return {"contents": "the contents at the address (the v60_ref defect, counts a data read)", "mask8": "result masked to 8 bits",
            "sext8": "low byte sign-extended", "sext16": "low half sign-extended", "ofs2": "address read from byte offset 2",
            "wrong_dest": "destination register + 1", "no_write": "no register write",
            "omit_unchanged": "the write is omitted when the value is unchanged (lock-step identical)",
            "dup": "the write happens twice (final state identical)", "transient": "a constant written first, then the address (final state identical)",
            "flag": "CY toggled", "extra_reg": "another register changed", "len6": "instruction length 6", "len8": "instruction length 8",
            "dataread": "an extra data read at the target", "priv": "an extra privileged write"}, mk


def negctl_refs(x, negctl):
    print("== negative controls (a): corrected-V60X MOVEA mutants (in memory only)")
    descs, mk = movea_mutants()
    pos = [(n, m) for n, m in x.man.items() if m["cls"] == "pos" and m["where"] == "low" and "ext" in m["refs"]]
    ok = all(not C.check_ref3(m, ref_inproc(x, n), "ext") for n, m in pos)
    negctl.append(("a0", f"control: the UNmutated in-process corrected V60X passes every positive program ({len(pos)})", ok, "baseline for (a)"))
    for kind, desc in descs.items():
        caught, caught_old, first = 0, 0, None
        for n, m in pos:
            t = ref_inproc(x, n, mk(kind))
            f = C.check_ref3(m, t, "ext")
            if f:
                caught += 1
                first = first or f"{n}: {f[0][:100]}"
                if any("GPR write events" not in g and "GPREV" not in g for g in f):
                    caught_old += 1
        gonly = kind in ("omit_unchanged", "dup", "transient")
        ok = caught > 0 and (caught_old == 0 if gonly else True)
        extra = f"; rejected by the pre-R3a checks alone: {caught_old}" if gonly else ""
        negctl.append(("a", f"V60X MOVEA mutant: {desc}", ok, f"{caught}/{len(pos)} programs fail{extra}; first: {first}"))
    # the base v60_ref: the counters see the defect (non-vacuity of dr), every A3 program fails
    base_bad, base_dr = 0, 0
    names = ("a3_ffff", "a3_zero", "a3_same", "a3_near", "a3_pat", "a3_byte5a")
    for n in names:
        r = RR.sh(["python3", str(HERE / "m1_ref.py"), "--lockstep", "--counters", str(x.progs / f"{n}.bin")])
        d = C2.parse2(r.stdout)
        mi = x.man[n]["movea_idx"][0]
        base_dr += d["C"][mi].get("dr", 0) if len(d["C"]) > mi else 0
        base_bad += d["L"] != x.man[n]["exp_L"]
    negctl.append(("a", "base v60_ref (retained defect D1) on the A3 programs: values differ from the generator and the MOVEA windows show data reads",
                   base_bad == len(names) and base_dr >= len(names), f"{base_bad}/{len(names)} programs differ; data reads counted in the MOVEA windows: {base_dr}"))


def negctl_logs(x, negctl):
    print("== negative controls (b): corrupted logs and expectations")
    for n in ("a3_same", "a8_same_val_regs"):
        m = x.man[n]
        t = RR.C.read_log(x.logs / f"{n}.ext")
        base = C.check_ref3(m, t, "ext")
        negctl.append(("b0", f"control: the V60X log of {n} passes", not base, f"{base[:1]}"))
        mi = m["movea_idx"][0]
        lines = t.splitlines()
        gm = [i for i, l in enumerate(lines) if l.startswith(f"G {mi} ")]
        edits = [("a GPR event dropped (the MOVEA's)", lambda L: [l for k, l in enumerate(L) if k != gm[0]]),
                 ("a GPR event duplicated", lambda L: L[:gm[0] + 1] + [L[gm[0]]] + L[gm[0] + 1:]),
                 ("a GPR event value changed", lambda L: [re.sub(r"val=\w{8}", "val=0badf00d", l) if k == gm[0] else l for k, l in enumerate(L)]),
                 ("a GPR event register changed", lambda L: [re.sub(r"id=\d+", "id=30", l) if k == gm[0] else l for k, l in enumerate(L)]),
                 ("a GPR event moved to the next instruction", lambda L: [re.sub(r"^G (\d+)", lambda mm: f"G {int(mm.group(1)) + 1}", l) if k == gm[0] else l for k, l in enumerate(L)]),
                 ("the GPREV marker removed", lambda L: [l for l in L if l != "GPREV on"]),
                 ("an extra GPR event for another register", lambda L: L[:gm[0]] + [f"G {mi} id=29 val=00000001"] + L[gm[0]:]),
                 ("a data read injected into the MOVEA window (C dr=1)", lambda L: [re.sub(r"dr=0", "dr=1", l) if l.startswith(f"C {mi} ") else l for l in L]),
                 ("a privileged write injected into the MOVEA window (C priv=1)", lambda L: [re.sub(r"priv=0", "priv=1", l) if l.startswith(f"C {mi} ") else l for l in L]),
                 ("a data write injected (W)", lambda L: L + ["W addr=00000800 size=4 data=deadbeef"])]
        for what, fn in edits:
            try:
                f = C.check_ref3(m, "\n".join(fn(lines)) + "\n", "ext")
            except Exception as e:
                negctl.append(("b", f"V60X log of {n} corrupted: {what}", False, f"checker error {e!r}"))
                continue
            negctl.append(("b", f"V60X log of {n} corrupted: {what}", bool(f), f"{f[:1]}"))
    n = "a3_pat"
    m = x.man[n]
    t = RR.C.read_log(x.logs / f"{n}.up")
    mi = m["movea_idx"][0]
    negctl.append(("b0", f"control: the upstream log of {n} passes", not C.check_ref3(m, t, "up"), ""))
    f = C.check_ref3(m, re.sub(r"(\nC %d dr=)0" % mi, r"\g<1>1", t), "up")
    negctl.append(("b", "upstream log of a3_pat with a data read injected into the MOVEA window", bool(f), f"{f[:1]}"))
    f = C.check_ref3(m, re.sub(r"(L %d pc=\w+ op=40 R\d\d=)\w{8}" % mi, r"\g<1>0000005a", t), "up")
    negctl.append(("b", "upstream log of a3_pat with the destination value replaced by the byte at the address (0x5A)", bool(f), f"{f[:1]}"))
    import m1_r3_run as RUN0
    t = RR.C.read_log(x.logs / "a9_regions.up")
    negctl.append(("b0", "control: upstream's S0 log of a9_regions shows ROM / mirror reads only", not RUN0.memstat_bad(t), f"{RUN0.memstat_bad(t)[:1]}"))
    for reg in ("io", "wram", "interrupt-ctl", "vram", "protection"):
        negctl.append(("b", f"upstream S0 log of a9_regions with a MEMSTAT {reg} read added (a bus access by the MOVEA target) is flagged",
                       bool(RUN0.memstat_bad(t + f"MEMSTAT read  {reg}  1\n")), ""))
    negctl.append(("b", "upstream S0 log of a9_regions with a MEMSTAT write to work RAM added is flagged", bool(RUN0.memstat_bad(t + "MEMSTAT write wram  1\n")), ""))
    # V2 probe image (a retired MOVEA-shaped instruction) with GPR events
    n = "pr_a3_same"
    m = x.man[n]
    t = RR.C.read_log(x.logs / f"{n}.v2_default")
    base = C.check_v2r3(m, t, x.phase, "std")
    negctl.append(("b0", f"control: V2's probe-image log of {n} passes", not base, f"{base[:1]}"))
    mi = m["movea_idx"][0]
    lines = t.splitlines()
    gm = next(i for i, l in enumerate(lines) if l.startswith(f"G {mi} "))
    edits = [("the MOVEA-shaped instruction's GPR event dropped (unchanged-value write omitted: the lock-step record is identical)", lambda L: [l for k, l in enumerate(L) if k != gm]),
             ("a GPR event duplicated", lambda L: L[:gm + 1] + [L[gm]] + L[gm + 1:]),
             ("byte enables 3 instead of f", lambda L: [l.replace("be=f", "be=3") if k == gm else l for k, l in enumerate(L)]),
             ("an event of an instruction that never retired", lambda L: L[:-3] + [f"G {len(m['exp_L']) + 2} id=5 be=f val=00000001"] + L[-3:]),
             ("C gpr= count wrong", lambda L: [re.sub(r"gpr=1", "gpr=2", l) if l.startswith(f"C {mi} ") else l for l in L]),
             ("a PSW update in the MOVEA window (C psw=1)", lambda L: [re.sub(r"psw=0", "psw=1", l) if l.startswith(f"C {mi} ") else l for l in L]),
             ("a data read in the MOVEA window (C dr=1)", lambda L: [re.sub(r"dr=0", "dr=1", l) if l.startswith(f"C {mi} ") else l for l in L]),
             ("a privileged write in the MOVEA window (C priv=1)", lambda L: [re.sub(r"priv=0", "priv=1", l) if l.startswith(f"C {mi} ") else l for l in L]),
             ("the GPREV marker removed", lambda L: [l for l in L if l != "GPREV on"])]
    for what, fn in edits:
        f = C.check_v2r3(m, "\n".join(fn(lines)) + "\n", x.phase, "std")
        negctl.append(("b", f"V2 probe-image log of {n} corrupted: {what}", bool(f), f"{f[:1]}"))
    # V2 trap exit: the trapped instruction (r3a: the MOVEA of a1_v00 and the d = 0 case; r3b baselines come from the model's log)
    import m1_r3a_catchers as K
    for n in ("a1_v00", "n2_d0_1f"):
        if n == "a1_v00" and x.phase != "r3a":
            continue
        mt = x.man[n]
        ti = mt["movea_idx"][0] if mt["movea_idx"] else mt["n_before"]
        if x.phase == "r3a":
            t = RR.C.read_log(x.logs / f"{n}.v2_default")
        else:
            t = K.run_model((x.progs / f"{n}.bin").read_bytes() + bytes(16), 0, K.Tabs(False), {})
        base = C.check_v2r3(mt, t, x.phase, "std")
        negctl.append(("b0", f"control: V2's trap-exit log of {n} passes the phase-{x.phase} expectation", not base, f"{base[:1]}"))
        for what, add in (("a GPR write by the trapped instruction (G)", f"G {ti} id=5 be=f val=00210000"),
                          ("a GPR write by the trapped instruction (CU gpr=1)", f"CU {ti} dr=0 dw=0 priv=0 psw=0 gpr=1"),
                          ("a PSW update by the trapped instruction (CU psw)", f"CU {ti} dr=0 dw=0 priv=0 psw=1 gpr=0"),
                          ("a privileged write by the trapped instruction (P)", f"P {ti} id=5 be=f val=00210000"),
                          ("a data write", "W addr=00000800 size=4 data=deadbeef")):
            f = C.check_v2r3(mt, t + add + "\n", x.phase, "std")
            negctl.append(("b", f"V2 trap exit of {n}: {what}", bool(f), f"{f[:1]}"))
        ek = mt["v2"][x.phase]["trap"]
        for what, edit_ in (("trap PC + 1", lambda e: e.update(pc=e["pc"] + 1)),
                            (f"trap kind {'mode' if ek != 'mode' else 'opcode'} instead of {ek}", lambda e: e.update(trap="mode" if ek != "mode" else "opcode")),
                            ("retired count + 1", lambda e: e.update(n=e["n"] + 1))):
            m3 = copy.deepcopy(mt)
            edit_(m3["v2"][x.phase])
            f = C.check_v2r3(m3, t, x.phase, "std")
            negctl.append(("b", f"wrong expected V2 trap ({n}): {what}", bool(f), f"{f[:1]}"))
    # the phase parameter: the OTHER phase's expectation rejects the current V2 on every production program whose MOVEA traps
    other = "r3b" if x.phase == "r3a" else "r3a"
    pos = [(nm, mm) for nm, mm in x.man.items() if mm["cls"] == "pos" and mm["where"] == "low" and mm["movea_idx"] and mm["mc"] == "prod"]
    pos = [(nm, mm) for nm, mm in pos if (x.logs / f"{nm}.v2_default").exists()]       # (a stub run executes a sample only)
    rej = sum(1 for nm, mm in pos if C.check_v2r3(mm, RR.C.read_log(x.logs / f"{nm}.v2_default"), other, "std"))
    negctl.append(("g", f"phase {other} expectation rejects the current V2 (phase {x.phase}) on the MOVEA positives", rej == len(pos) and len(pos) > 0, f"{rej}/{len(pos)} rejected"))
    wn = "pr_a7_L16_k6"
    wt = RR.C.read_log(x.logs / f"{wn}.v2_default_ack")
    mw = x.man[wn]
    base = C.check_v2r3(mw, wt, x.phase, "ack")
    f = C.check_v2r3(mw, wt + f"G {mw['movea_idx'][0]} id=2 be=f val=b6a5948a\n", x.phase, "ack")
    negctl.append(("b", "A7 wait exit: control passes; a GPR write of the unretired (withheld) instruction is rejected", not base and bool(f), f"base={base[:1]} leak:{f[:1]}"))


def negctl_benches(x, negctl):
    print("== negative controls (c): forced-zero GPR-event instrumentation (rebuilt copies)")
    mut = x.out / "mut"
    mut.mkdir(exist_ok=True)
    root = x.root
    U = root / "references/s32/rtl/cpu/v60"
    ob = (HERE / "tb/v2_sim_observer.sv").read_text()
    ob2 = ob.replace('if (gprev) $display("G %0d id=%0d be=%x val=%08x"', 'if (1\'b0 && gprev) $display("G %0d id=%0d be=%x val=%08x"')
    assert ob2 != ob
    (mut / "v2_sim_observer_nogev.sv").write_text(ob2)
    Rr = root / "experiments/V2-datapath-floor/rtl"
    files = [str(Rr / l.strip()) for l in (Rr / "files.f").read_text().split()]
    r = RR.sh(["verilator", "--binary", "--timing", "--threads", "1", "-Wno-fatal", "-Wno-lint", "-Wno-style", "-Wno-TIMESCALEMOD",
               "+define+SIMULATION", f"-I{Rr}", "--top-module", "tb_m1_v2", "--Mdir", str(mut / "obj_v2_nogev"), "-o", "sim"] + files +
              [str(U / "s32_v60_bus.sv"), str(HERE / "tb/m1_mem.sv"), str(mut / "v2_sim_observer_nogev.sv"),
               str(HERE / "tb/v2_sim_checks.sv"), str(HERE / "tb/tb_m1_v2.sv")])
    if r.returncode:
        negctl.append(("c2", "no-event V2 observer", False, "mutant build failed: " + r.stderr[-200:]))
    else:
        mc = x.out / "mc_probe3"
        syms = RR.symbols(mc)
        bad = tot = 0
        for n in ("pr_a1_v00", "pr_a2_r05", "pr_a3_same", "pr_a8_same_reg"):
            m = x.man[n]
            cmd = RR.v2_cmd(str(mut / "obj_v2_nogev/sim"), mc, [f"+img={x.progs / (n + '.hex')}", "+maxcyc=400000", "+privev", "+gprev"], syms)
            t = RR.sh(cmd).stdout
            tot += 1
            bad += bool(C.check_v2r3(m, t, x.phase, "std"))
        negctl.append(("c2", "V2 observer without G events (rebuilt copy): every probe-image MOVEA program is rejected", bad == tot, f"{bad}/{tot} rejected"))
    pos = [(n, m) for n, m in x.man.items() if m["cls"] == "pos" and m["where"] == "low" and "ext" in m["refs"]][:20]
    bad = sum(1 for n, m in pos if C.check_ref3(m, ref_inproc(x, n, None, nolog=True), "ext"))
    negctl.append(("c1", "corrected V60X without its register write log: every positive program is rejected", bad == len(pos), f"{bad}/{len(pos)}"))


def negctl_three(x, negctl):
    """THE THREE CHECKER CONTROLS on the real V2 probe image (plan 6 #2)"""
    print("== negative controls (d): the three GPR-event checker controls on the real V2")
    import m1_r3_run as RUN
    specs = [("a", "an omitted GPR write whose value is unchanged (destination already equals the address)", {"nowrite": True},
              ["pr_a3_same", "pr_a3_same_twice"]),
             ("b", "a duplicate GPR write (the same write twice; final state identical)", {"dup": True},
              ["pr_a1_v00", "pr_a1_v07", "pr_a2_r05", "pr_a8_same_reg"])]
    for cid, desc, mut, progs in specs:
        mc, _ = RUN.build_probe3(x, f"mc_probe3_ctl_{cid}", mut)
        old_pass = new_fail = 0
        first = None
        for n in progs:
            m = x.man[n]
            t = RUN.v2_text(x, n, m, "", "std", mc=mc)
            old = C2.check_v2r2(m, t, x.phase, "std")           # lock-step, final state, counters, privileged events: NO GPR events
            new = C.check_v2r3(m, t, x.phase, "std")
            old_pass += not old
            new_fail += bool(new)
            first = first or f"{n}: {new[0][:100]}"
        negctl.append(("d", f"checker control ({cid}) on the real V2: {desc}", new_fail == len(progs) and old_pass == len(progs),
                       f"caught {new_fail}/{len(progs)} by the GPR event log; the pre-R3a checks alone pass {old_pass}/{len(progs)}; first: {first}"))
    mc, _ = RUN.build_probe3(x, "mc_probe3_ctl_c", {"transient": True})
    old_pass = new_fail = tot = 0
    first = None
    for n in ("pr3_mid_r1", "pr3_mid_r31"):
        m = x.man[n]
        for cfg in ("", "+wide"):
            t = RUN.v2_text(x, n, m, cfg, "std", mc=mc)
            old = C2.check_v2r2(m, t, x.phase, "std")
            new = C.check_v2r3(m, t, x.phase, "std")
            tot += 1
            old_pass += not old
            new_fail += bool(new) and any("did not retire" in g for g in new)
            first = first or f"{n}: {new[0][:100]}"
    negctl.append(("d", "checker control (c) on the real V2: a transient GPR write restored before a trap (register value unchanged, the instruction never retires)",
                   new_fail == tot and old_pass == tot, f"caught {new_fail}/{tot} after the drain; the pre-R3a checks alone pass {old_pass}/{tot}; first: {first}"))


def swap_lines(text, p0, p1):
    """swap the (first) lines that start with p0 and p1, keeping their ids: the reordered-retirement reproduction"""
    L = text.splitlines()
    i0 = next(k for k, l in enumerate(L) if l.startswith(p0))
    i1 = next(k for k, l in enumerate(L) if l.startswith(p1))
    L[i0], L[i1] = L[i1], L[i0]
    return "\n".join(L) + "\n"


def negctl_probe_value(x, negctl):
    print("== negative controls (e): probe_movea_value enforcement")
    import m1_probe_movea as PV
    wd = x.out / "pvctl"
    wd.mkdir(exist_ok=True)
    sim = x.out / "obj_up/sim"
    good = PV.default_runners(sim)
    ok, why, _ = PV.run(wd, good)
    negctl.append(("e0", "control: the real runners reproduce D1", ok, why))
    ref_t, up_t = good["ref"](wd / "probe_movea_value.bin", wd / "probe_movea_value.hex"), good["up"](wd / "probe_movea_value.bin", wd / "probe_movea_value.hex")
    cases = {
        "the references AGREE (v60_ref corrected: R1 = 0x20, dr = 0)": ((0, ref_t[1].replace("R01=0000005a", "R01=00000020").replace("C 0 dr=1", "C 0 dr=0")), up_t),
        "the v60_ref run crashed (exit 1)": ((1, ref_t[1]), up_t),
        "the upstream run crashed (exit 1)": (ref_t, (1, up_t[1])),
        "the v60_ref run timed out": ((-1, ""), up_t),
        "empty v60_ref output": ((0, ""), up_t),
        "missing upstream log (empty output)": (ref_t, (0, "")),
        "upstream R1 differs (0x21, not 0x20)": (ref_t, (0, up_t[1].replace("R01=00000020", "R01=00000021"))),
        "v60_ref R1 differs (0x5b)": ((0, ref_t[1].replace("R01=0000005a", "R01=0000005b")), up_t),
        "v60_ref dr = 0 (the read vanished)": ((0, ref_t[1].replace("C 0 dr=1", "C 0 dr=0")), up_t),
        "upstream dr = 1": (ref_t, (0, up_t[1].replace("C 0 dr=0", "C 0 dr=1"))),
        "an unrelated difference (R5 differs)": (ref_t, (0, up_t[1].replace("R05=00000000", "R05=00000001"))),
        "an unrelated difference (a data write in one run only)": (ref_t, (0, up_t[1].replace("END ", "W addr=00000800 size=4 data=00000001\nEND ", 1))),
        "an unrelated difference (PSW)": (ref_t, (0, up_t[1].replace("PSW=10000000", "PSW=10000001"))),
        "no END record": ((0, "\n".join(l for l in ref_t[1].splitlines() if not l.startswith("END"))), up_t),
        # R3a fix 2 (Codex r1 reproductions and further unrelated differences; each was accepted by the pre-fix guard)
        "the upstream MOVEA retires at another PC (L 0 pc=00000001)": (ref_t, (0, up_t[1].replace("L 0 pc=00000000", "L 0 pc=00000001"))),
        "the upstream HALT retires at another PC (L 1 pc=00000008)": (ref_t, (0, up_t[1].replace("L 1 pc=00000007", "L 1 pc=00000008"))),
        "an unrelated R05=00000001 on upstream's MOVEA lock-step record": (ref_t, (0, up_t[1].replace("L 0 pc=00000000 op=40 R01=00000020", "L 0 pc=00000000 op=40 R01=00000020 R05=00000001"))),
        "an unrelated PSW on v60_ref's MOVEA lock-step record": ((0, ref_t[1].replace("L 0 pc=00000000 op=40 R01=0000005a", "L 0 pc=00000000 op=40 R01=0000005a PSW=10000001")), up_t),
        "both runs' unrelated register dumps and PSW records are removed": ((0, "\n".join(l for l in ref_t[1].splitlines() if not re.match(r"^(R(?!01=)\d\d=|PSW=)", l))),
                                                                           (0, "\n".join(l for l in up_t[1].splitlines() if not re.match(r"^(R(?!01=)\d\d=|PSW=)", l)))),
        "both runs miss only the PSW dump": ((0, "\n".join(l for l in ref_t[1].splitlines() if not l.startswith("PSW="))), (0, "\n".join(l for l in up_t[1].splitlines() if not l.startswith("PSW=")))),
        "the same data write appears in both runs": ((0, ref_t[1].replace("END ", "W addr=00000800 size=4 data=00000001\nEND ", 1)), (0, up_t[1].replace("END ", "W addr=00000800 size=4 data=00000001\nEND ", 1))),
        "a data write differs in value between the runs": ((0, ref_t[1].replace("END ", "W addr=00000800 size=4 data=00000001\nEND ", 1)), (0, up_t[1].replace("END ", "W addr=00000800 size=4 data=00000002\nEND ", 1))),
        "an extra retirement on upstream (L 2 / C 2)": (ref_t, (0, up_t[1].replace("R00=", "L 2 pc=00000007 op=00\nC 2 dr=0 dw=0 priv=0\nR00=", 1))),
        "a missing retirement on upstream (no L 1 / C 1, END unchanged)": (ref_t, (0, "\n".join(l for l in up_t[1].splitlines() if not l.startswith(("L 1 ", "C 1 "))))),
        "a missing retirement on v60_ref (no L 1, END unchanged)": ((0, "\n".join(l for l in ref_t[1].splitlines() if not l.startswith("L 1 "))), up_t),
        "an extra retirement counted in both END records (instrs=3)": ((0, ref_t[1].replace("instrs=2", "instrs=3")), (0, up_t[1].replace("instrs=2", "instrs=3"))),
        "an unrelated counter differs on the HALT (C 1 dw=1 on upstream)": (ref_t, (0, up_t[1].replace("C 1 dr=0 dw=0 priv=0", "C 1 dr=0 dw=1 priv=0"))),
        "the same unrelated counter in both runs (C 1 priv=1)": ((0, ref_t[1].replace("C 1 dr=0 dw=0 priv=0", "C 1 dr=0 dw=0 priv=1")), (0, up_t[1].replace("C 1 dr=0 dw=0 priv=0", "C 1 dr=0 dw=0 priv=1"))),
        "an unrecognised line in the upstream output": (ref_t, (0, up_t[1].replace("END ", "X surprise\nEND ", 1))),
        "a duplicated L 0 record on upstream": (ref_t, (0, up_t[1].replace("L 1 ", "L 0 pc=00000000 op=40 R01=00000020\nL 1 ", 1))),
        # R3a fixes 2 (Codex r2 should-fix 2): duplicate fields and retirement order. The first reproduction is Codex's, verbatim.
        "upstream's counter record is `C 0 dr=1 dr=0 dw=0 priv=0` (duplicate field; the later dr=0 used to overwrite dr=1)": (ref_t, (0, up_t[1].replace("C 0 dr=0 dw=0 priv=0", "C 0 dr=1 dr=0 dw=0 priv=0"))),
        "v60_ref's counter record is `C 0 dr=0 dr=1 dw=0 priv=0` (duplicate field, the other order)": ((0, ref_t[1].replace("C 0 dr=1 dw=0 priv=0", "C 0 dr=0 dr=1 dw=0 priv=0")), up_t),
        "the upstream L 0 and L 1 lines are swapped, ids retained (Codex r2)": (ref_t, (0, swap_lines(up_t[1], "L 0 ", "L 1 "))),
        "the v60_ref L 0 and L 1 lines are swapped, ids retained": ((0, swap_lines(ref_t[1], "L 0 ", "L 1 ")), up_t),
        "the L lines are swapped in BOTH runs, so the runs still agree with each other": ((0, swap_lines(ref_t[1], "L 0 ", "L 1 ")), (0, swap_lines(up_t[1], "L 0 ", "L 1 "))),
        "the upstream C 0 and C 1 lines are swapped, ids retained": (ref_t, (0, swap_lines(up_t[1], "C 0 ", "C 1 "))),
        "a duplicate field in upstream's END record (halted=1 halted=0)": (ref_t, (0, up_t[1].replace("END halted=1", "END halted=1 halted=0"))),
        "a duplicate register field on upstream's MOVEA record (R01 twice)": (ref_t, (0, up_t[1].replace("R01=00000020", "R01=00000020 R01=00000020", 1))),
    }
    def nl_(r):         # R3a Codex-r5 fixes: the D1 parser now demands the final newline; cases built with "\n".join(...) get theirs, so each still fails for its own reason
        return (r[0], r[1] + "\n") if r[1] and not r[1].endswith("\n") else r
    for what, (r_, u_) in cases.items():
        res, why = PV.evaluate(nl_(r_), nl_(u_))
        negctl.append(("e", f"probe_movea_value must FAIL when {what}", not res, why[:110]))
    # R3a fixes 3 (Codex r3 should-fix 2, audit of this parser): every recognised record is checked to its end; each case names the
    # message the rejection must contain
    gram = {
        "upstream's END record carries an unknown field (`END halted=1 instrs=2 cycles=9 junk=1`)": (ref_t, (0, up_t[1].replace("END halted=1 instrs=2", "END halted=1 instrs=2 junk=1"))),
        "upstream's END record has its fields reordered": (ref_t, (0, re.sub(r"END halted=1 instrs=2( cycles=\d+)?( ce=\d+)?", r"END instrs=2 halted=1\1\2", up_t[1]))),
        "v60_ref's C 0 record carries a trailing token": ((0, ref_t[1].replace("C 0 dr=1 dw=0 priv=0", "C 0 dr=1 dw=0 priv=0 extra=1")), up_t),
        "upstream's C 0 record has an unknown key (gpr=0)": (ref_t, (0, up_t[1].replace("C 0 dr=0 dw=0 priv=0", "C 0 dr=0 dw=0 priv=0 gpr=0"))),
        "upstream's C 0 record has its fields reordered": (ref_t, (0, up_t[1].replace("C 0 dr=0 dw=0 priv=0", "C 0 dw=0 dr=0 priv=0"))),
        "upstream's L 0 record has a short pc (`pc=0`)": (ref_t, (0, up_t[1].replace("L 0 pc=00000000", "L 0 pc=0"))),
    }
    needles = ["malformed END record", "malformed END record", "malformed counter record", "malformed counter record", "malformed counter record", "unrecognised line"]
    for (what, (r_, u_)), need in zip(gram.items(), needles):
        res, why = PV.evaluate(r_, u_)
        negctl.append(("e", f"probe_movea_value must FAIL when {what} [reason must contain: {need}]", (not res) and need in why, why[:110]))
    # end to end: a deliberately FIXED copy of the reference (m1_ref.py + ref/v60_ref.py with MOVEA decoding operand 1 as an address)
    fx = x.out / "fixed_ref"
    (fx / "ref").mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "m1_ref.py", fx / "m1_ref.py")
    shutil.copy(HERE / "m1_ref_ext.py", fx / "m1_ref_ext.py")
    src = (HERE / "ref/v60_ref.py").read_text()
    old = "                o1 = self.decode_am(2, d1, False); v1 = self.load(o1)\n                ln = o1[2] + 2"
    assert old in src
    new = ("                _mv = op in (0x40, 0x42, 0x44)\n"
           "                o1 = self.decode_am(2, d1, _mv); v1 = o1[1] if _mv else self.load(o1)\n                ln = o1[2] + 2")
    (fx / "ref/v60_ref.py").write_text(src.replace(old, new, 1))
    r = RR.sh(["python3", str(HERE / "m1_probe_movea.py"), "--sim", str(sim), "--ref-script", str(fx / "m1_ref.py"), "--workdir", str(wd / "fixed")])
    negctl.append(("e", "end to end: m1_probe_movea.py with a deliberately FIXED copy of the reference exits non-zero",
                   r.returncode != 0 and "AGREE" in r.stdout, f"rc={r.returncode}: {r.stdout.strip()[:140]}"))
    r = RR.sh(["python3", str(HERE / "m1_probe_movea.py"), "--sim", str(x.out / "no_such_sim"), "--workdir", str(wd / "nosim")])
    negctl.append(("e", "end to end: m1_probe_movea.py with a missing upstream simulator exits non-zero", r.returncode != 0, f"rc={r.returncode}: {r.stdout.strip()[:120]}"))
    r = RR.sh(["python3", str(HERE / "m1_probe_movea.py"), "--sim", str(sim), "--ref-script", str(wd / "no_such_ref.py"), "--workdir", str(wd / "noref")])
    negctl.append(("e", "end to end: m1_probe_movea.py with a missing reference script exits non-zero", r.returncode != 0, f"rc={r.returncode}: {r.stdout.strip()[:120]}"))
    env = dict(os.environ, PROBE_REF_SCRIPT=str(fx / "m1_ref.py"))
    d = x.out / "refs_fixed"
    r = subprocess.run(["bash", str(HERE / "run_refs.sh"), "2", str(d)], capture_output=True, text=True, env=env, timeout=900)
    negctl.append(("e", "end to end: run_refs.sh with the FIXED reference exits non-zero",
                   r.returncode != 0 and "probe_movea_value" in r.stdout, f"rc={r.returncode}; " + " | ".join(l for l in r.stdout.splitlines() if "movea" in l)[:200]))
    d = x.out / "refs_good"
    r = subprocess.run(["bash", str(HERE / "run_refs.sh"), "2", str(d)], capture_output=True, text=True, timeout=900)
    negctl.append(("e0", "control: run_refs.sh with the real reference exits 0 and reports probe_movea_value as reproducing",
                   r.returncode == 0 and "D1 reproduces" in r.stdout, f"rc={r.returncode}; " + " | ".join(l for l in r.stdout.splitlines() if "movea" in l)[:200]))


def negctl_parsers(x, negctl):
    """R3a fixes 2 (Codex r2 should-fix 2 and 3): duplicate fields and retirement order in the shared run-log parser (m1_r1_check.parse,
    used by the R1a / R2a / R3a checkers), and the identity run's completion test (m1_r3a_identity.completion_problem)"""
    print("== negative controls (g): duplicate fields, retirement order, identity completion")
    import m1_r1_check as C1
    import m1_r3_run as RUN
    import m1_r3a_identity as ID
    n = "pr_a1_v00"
    m = x.man[n]
    t = RUN.v2_text(x, n, m, "", "std")
    base = C.check_v2r3(m, t, "r3a", "std")
    negctl.append(("g0", "control: the real V2 log of a probe-image program passes the R3a checker before it is corrupted", not base, f"{n}: {base[:1]}"))
    lines = t.splitlines()
    cl = [l for l in lines if l.startswith("C ")]
    assert len(cl) >= 2 and any(l.startswith("L 1 ") for l in lines), "probe program has too few records"
    corr = {
        "a duplicate field in a counter record (C 0 dr=1 dr=0 ...)": lambda L: [(l.replace("dr=0", "dr=1 dr=0", 1) if l.startswith("C 0 ") else l) for l in L],
        "a duplicate field in an END record": lambda L: [(l.replace("halted=", "halted=0 halted=", 1) if l.startswith("END ") else l) for l in L],
        "the L 0 and L 1 lines swapped, ids retained": lambda L: [L[k] if not L[k].startswith(("L 0 ", "L 1 ")) else (L[[j for j, q in enumerate(L) if q.startswith("L 1 ")][0]] if L[k].startswith("L 0 ") else L[[j for j, q in enumerate(L) if q.startswith("L 0 ")][0]]) for k in range(len(L))],
        "the C 0 and C 1 lines swapped, ids retained": lambda L: [L[k] if not L[k].startswith(("C 0 ", "C 1 ")) else (L[[j for j, q in enumerate(L) if q.startswith("C 1 ")][0]] if L[k].startswith("C 0 ") else L[[j for j, q in enumerate(L) if q.startswith("C 0 ")][0]]) for k in range(len(L))],
        "a duplicated final register line": lambda L: L + ["R05=00000000"],
        "a duplicated final PSW line": lambda L: L + ["PSW=10000000"],
    }
    for what, fn in corr.items():
        t2 = "\n".join(fn(list(lines))) + "\n"
        d = C1.parse(t2)
        fails = C.check_v2r3(m, t2, "r3a", "std")
        negctl.append(("g", f"the shared run-log parser / R3a checker must FAIL on a real V2 log with {what}",
                       bool(d["FATAL"]) and bool(fails), f"parse: {d['FATAL'][:1]}; checker: {len(fails)} failure(s)"))
    # identity completion: the exact Codex r2 reproductions and the other missing / malformed / duplicate cases
    ok_txt = "R00=00000000\nEND halted=1 instrs=2 cycles=88\nCOUNTERS pre=1 exec=2 mem=0 fetch=0 iter=0\nSIMCHECK errors=0 ipc_errors=0\nREDIRECTS reset=0 instr=0 instr_outstanding=0 (x)"
    cases = {
        "an exit-zero run with `SIMCHECK errors=7 ipc_errors=2` (Codex r2)": (0, ok_txt.replace("errors=0 ipc_errors=0", "errors=7 ipc_errors=2")),
        "an exit-zero run with only ipc_errors=2": (0, ok_txt.replace("ipc_errors=0", "ipc_errors=2")),
        "an exit-zero run with only errors=1": (0, ok_txt.replace("errors=0", "errors=1")),
        "a bare `END ` followed by `SIMCHECK errors=` (Codex r2)": (0, "END \nSIMCHECK errors="),
        "a bare `END ` with a valid SIMCHECK": (0, ok_txt.replace("END halted=1 instrs=2 cycles=88", "END ")),
        "an END record missing cycles": (0, ok_txt.replace(" cycles=88", "")),
        "an END record with a duplicate field": (0, ok_txt.replace("halted=1", "halted=1 halted=0")),
        "an END record with a non-numeric field": (0, ok_txt.replace("instrs=2", "instrs=two")),
        "a SIMCHECK record missing ipc_errors": (0, ok_txt.replace(" ipc_errors=0", "")),
        "a SIMCHECK record with a duplicate field": (0, ok_txt.replace("ipc_errors=0", "ipc_errors=0 errors=0")),
        "no END record": (0, "\n".join(l for l in ok_txt.splitlines() if not l.startswith("END"))),
        "no SIMCHECK record": (0, "\n".join(l for l in ok_txt.splitlines() if not l.startswith("SIMCHECK"))),
        "a duplicate END record": (0, ok_txt + "\nEND halted=1 instrs=2 cycles=88"),
        "a duplicate SIMCHECK record": (0, ok_txt + "\nSIMCHECK errors=0 ipc_errors=0"),
        "simulation-check violation lines without a summary": (0, "END halted=1 instrs=2 cycles=88\nSIMCHECK 123 something broke (upc=1)"),
        "a non-zero exit with a clean log": (3, ok_txt),
        "an empty log": (0, ""),
    }
    for what, (rc_, txt) in cases.items():
        pr = ID.completion_problem(rc_, txt)
        negctl.append(("g", f"identity completion test must FAIL: {what}", pr is not None, pr or "accepted"))
    for what, txt in (("a bench log with END / SIMCHECK and counters (the clean control)", ok_txt),
                      ("an S0 bench log, END without ce and with TRAP / MEMSTAT lines", ok_txt + "\nTRAP mode ipc=0010051c\nMEMSTAT read  rom            13"),
                      ("an upstream-style END with ce=", ok_txt.replace("cycles=88", "cycles=88 ce=30"))):
        pr = ID.completion_problem(0, txt)
        negctl.append(("g0", f"control: identity completion test accepts {what}", pr is None, pr or "accepted"))
    # a real bench log, end to end through run() on the new bench
    sim = x.out / "obj_v2/sim"
    mc = x.out / "mc"
    if sim.exists():
        text, prob = ID.run(sim, mc, [f"+img={x.progs / (n + '.hex')}", "+maxcyc=400000", "+lockstep"])
        negctl.append(("g0", "control: a real V2 bench run is accepted by the identity run() / completion test", prob is None and "END halted" in text, prob or f"{len(text)} bytes"))


def negctl_parsers3(x, negctl):
    """R3a fixes 3 (Codex r3 should-fix 1, and the G-order note): the shared run-log parser (m1_r1_check.parse, used by the R1a / R2a /
    R3a checkers) rejects contradictory, malformed and duplicate SIMCHECK and TRAP records and every other malformed recognised
    record, and G / P events must be printed in program order. Each rejection names the message it must contain (the checker's failure
    list is searched), so it is shown to come from the INTENDED check; the controls show the real logs still pass."""
    print("== negative controls (h): complete record parsing, G / P order (Codex r3)")
    import m1_r3_run as RUN
    # the real passing log of the Codex r3 reproductions (a3_same: V2 traps `opcode` at the MOVEA in phase r3a). With the table-only R3b stub
    # (phase r3b) a3_same STALLs, so n1_42_abs (opcode trap at 7 after one retirement in both phases, production microcode) stands in.
    n = "a3_same" if x.phase == "r3a" else "n1_42_abs"
    m = x.man[n]
    t = RUN.v2_text(x, n, m, "", "std")
    base = C.check_v2r3(m, t, x.phase, "std")
    negctl.append(("h0", f"control: the real V2 log of {n} (default fetch) passes the R3a checker before it is corrupted", not base, f"{n}: {base[:1]}"))
    L = t.splitlines()
    assert any(l == "SIMCHECK errors=0 ipc_errors=0" for l in L) and any(l.startswith("TRAP opcode ipc=") for l in L), f"{n} V2 log lacks SIMCHECK / TRAP"
    trap_line = next(l for l in L if l.startswith("TRAP "))

    def sub1(prefix, fn):
        """replace the first line starting with prefix by fn(line) (a string or a list of lines)"""
        def go(Lx):
            i = next(k for k, l in enumerate(Lx) if l.startswith(prefix))
            r = fn(Lx[i])
            return Lx[:i] + (r if isinstance(r, list) else [r]) + Lx[i + 1:]
        return go

    def ins(prefix, *new, after=False):
        def go(Lx):
            i = next(k for k, l in enumerate(Lx) if l.startswith(prefix))
            return Lx[:i + (1 if after else 0)] + list(new) + Lx[i + (1 if after else 0):]
        return go
    SC = "SIMCHECK errors=0 ipc_errors=0"
    v2cases = [
        # (description, transform, message the failure list must contain)
        ("`SIMCHECK errors=7 ipc_errors=2` followed by `SIMCHECK errors=0 ipc_errors=0` (Codex r3)", sub1("SIMCHECK", lambda l: ["SIMCHECK errors=7 ipc_errors=2", SC]), "duplicate SIMCHECK summary"),
        ("`SIMCHECK errors=0 ipc_errors=0` followed by `SIMCHECK errors=7 ipc_errors=2`", sub1("SIMCHECK", lambda l: [SC, "SIMCHECK errors=7 ipc_errors=2"]), "duplicate SIMCHECK summary"),
        ("`SIMCHECK errors=0 ipc_errors=0 errors=7 ipc_errors=2` on one line (Codex r3)", sub1("SIMCHECK", lambda l: "SIMCHECK errors=0 ipc_errors=0 errors=7 ipc_errors=2"), "malformed SIMCHECK record"),
        ("a malformed additional SIMCHECK line (`SIMCHECK nonsense`) (Codex r3)", ins("SIMCHECK", "SIMCHECK nonsense", after=True), "malformed SIMCHECK record"),
        ("a malformed additional SIMCHECK line before the summary", ins("SIMCHECK", "SIMCHECK errors="), "malformed SIMCHECK record"),
        ("a SIMCHECK summary with a trailing token", sub1("SIMCHECK", lambda l: l + " extra"), "malformed SIMCHECK record"),
        ("a violation record next to an errors=0 summary", ins("SIMCHECK", "SIMCHECK 1234 branch target (upc=7 head=00000007 avail=2)"), "simulation-check violation record"),
        ("an incorrect TRAP followed by the expected TRAP (Codex r3)", ins("TRAP", "TRAP mode ipc=00000099"), "duplicate TRAP record"),
        ("the expected TRAP followed by an incorrect TRAP", ins("TRAP", "TRAP mode ipc=00000099", after=True), "duplicate TRAP record"),
        ("the expected TRAP printed twice", ins("TRAP", trap_line, after=True), "duplicate TRAP record"),
        ("a TRAP record with a trailing token", sub1("TRAP", lambda l: l + " extra"), "malformed TRAP record"),
        ("a TRAP record with a short pc", sub1("TRAP", lambda l: re.sub(r"ipc=0*", "ipc=", l)), "malformed TRAP record"),
        ("a bare TRAP record", sub1("TRAP", lambda l: "TRAP"), "malformed TRAP record"),
        ("an END record with an unknown field", sub1("END ", lambda l: l + " junk=1"), "malformed END record"),
        ("an END record with a trailing token", sub1("END ", lambda l: l + " extra"), "malformed END record"),
        ("an END record with halted=2", sub1("END ", lambda l: l.replace("halted=0", "halted=2").replace("halted=1", "halted=2")), "malformed END record"),
        ("a second END record", ins("END ", "END halted=0 instrs=1 cycles=9", after=True), "duplicate END record"),
        ("an L record with a trailing token", sub1("L 0 ", lambda l: l + " extra"), "malformed lock-step record"),
        ("an L record with a short pc", sub1("L 0 ", lambda l: l.replace("pc=00000000", "pc=0")), "malformed lock-step record"),
        ("a C record with a trailing token", sub1("C 0 ", lambda l: l + " extra"), "malformed C record"),
        ("a C record with an unknown key", sub1("C 0 ", lambda l: l.replace("priv=", "prib=")), "malformed C record"),
        ("a C record with its fields reordered", sub1("C 0 ", lambda l: l.replace("dr=0 dw=0", "dw=0 dr=0")), "malformed C record"),
        ("a C record with a non-numeric field", sub1("C 0 ", lambda l: l.replace("dw=0", "dw=x")), "non-numeric field"),
        ("a final register line with trailing junk", sub1("R05=", lambda l: l + " junk"), "malformed final dump line"),
        ("a final register line with a non-hex value", sub1("R05=", lambda l: "R05=0000000g"), "malformed final dump line"),
        ("a final register line R40", sub1("R05=", lambda l: "R40=00000000"), "malformed final dump line"),
        ("a final PSW line with trailing junk", sub1("PSW=", lambda l: l + " junk"), "malformed final PSW line"),
        ("a final PSW line that is not hex", sub1("PSW=", lambda l: "PSW=zzzzzzzz"), "malformed final PSW line"),
        ("a malformed data write record", ins("END ", "W addr=00000800 size=3 data=00000001"), "malformed data write record"),
        ("a malformed STALL record", ins("END ", "STALL ipc=00000007"), "malformed STALL record"),
        ("a STALL record with a trailing token", ins("END ", "STALL ipc=00000007 upc=3 retired=1 extra"), "malformed STALL record"),
        ("a second GPREV line", ins("GPREV", "GPREV on", after=True), "2 GPREV records"),
        ("a GPREV variant", sub1("GPREV", lambda l: "GPREV on "), "malformed GPREV record"),
        ("a G record with a trailing token", sub1("G 0 ", lambda l: l + " extra"), "unparsable G line"),
        ("a G record with its byte enables doubled", sub1("G 0 ", lambda l: l.replace("be=f", "be=ff")), "unparsable G line"),
        ("a PR record with a repeated field", sub1("PR id=5 ", lambda l: l + " val=deadbeef"), "malformed final privileged dump line"),
        ("a duplicated PR record", ins("PR id=5 ", "PR id=5 val=00000000", after=True), "duplicate final privileged dump line"),
        ("a PR record with id=40", sub1("PR id=5 ", lambda l: "PR id=40 val=00000000"), "malformed final privileged dump line"),
        ("a P record with a repeated field", ins("L 0 ", "P 0 id=5 be=f val=00000001 val=00000002"), "unparsable P line"),
    ]
    for what, fn, need in v2cases:
        try:
            t2 = "\n".join(fn(list(L))) + "\n"
        except StopIteration:
            negctl.append(("h", f"R3a checker must FAIL on the real {n} V2 log with {what}", False, "the record to edit is not in this log"))
            continue
        fails = C.check_v2r3(m, t2, x.phase, "std")
        negctl.append(("h", f"R3a checker must FAIL on the real {n} V2 log with {what} [reason must contain: {need}]",
                       any(need in f_ for f_ in fails), (fails[0] if fails else "ACCEPTED")[:120]))
    # the same through the shared R1a / R2a checkers (every user of the idiom): R2a on an R2 program, R1a on an R1 program
    # ---- G / P event order (Codex r3 note) ----
    n8 = "a8_same_val_regs"
    m8 = x.man[n8]
    e8 = RUN.ref_text(x, n8, m8, "ext")
    base8 = C.check_ref3(m8, e8, "ext")
    negctl.append(("h0", "control: the corrected-V60X log of a8_same_val_regs passes check_ref3 before it is corrupted", not base8, base8[:1] and base8[0][:100] or "pass"))
    L8 = e8.splitlines()
    gi = [k for k, l in enumerate(L8) if l.startswith("G ")]
    assert len(gi) >= 3, "a8_same_val_regs needs G events of several instructions"

    def reverse_g(Lx):
        out, rev = list(Lx), [Lx[k] for k in gi][::-1]
        for k, line in zip(gi, rev):
            out[k] = line
        return out

    def swap_g(Lx, a, b):
        out = list(Lx)
        ia, ib = next(k for k, l in enumerate(out) if l.startswith(f"G {a} ")), next(k for k, l in enumerate(out) if l.startswith(f"G {b} "))
        out[ia], out[ib] = out[ib], out[ia]
        return out

    def g_after_its_l(Lx):
        out = list(Lx)
        ig = next(k for k, l in enumerate(out) if l.startswith("G 1 "))
        il = next(k for k, l in enumerate(out) if l.startswith("L 1 "))
        g = out.pop(ig)
        out.insert(il, g)               # after L 1 (the list shrank by one, so index il is the slot after it)
        return out
    gcases = [
        ("the G records REVERSED across instructions (Codex r3: this passed)", reverse_g, "events out of order"),
        ("the G 1 and G 2 records swapped (ids retained)", lambda Lx: swap_g(Lx, 1, 2), "events out of order"),
        ("the G 1 record printed after its own L 1 record", g_after_its_l, "misplaced"),
        ("a G 3 record printed before L 0", lambda Lx: [next(l for l in Lx if l.startswith("G 3 "))] + [l for l in Lx if not l.startswith("G 3 ")], "misplaced"),
    ]
    for what, fn, need in gcases:
        t2 = "\n".join(fn(list(L8))) + "\n"
        fails = C.check_ref3(m8, t2, "ext")
        negctl.append(("h", f"check_ref3 must FAIL on the corrected-V60X log of a8_same_val_regs with {what} [reason must contain: {need}]",
                       any(need in f_ for f_ in fails), (fails[0] if fails else "ACCEPTED")[:120]))
    # the same on a real V2 log (G events printed by the observer) and on the upstream-form P events of an LDPR program
    n9 = "pr_a1_v00"      # probe image: independent of the production table (and of the R3b stub)
    m9 = x.man[n9]
    t9 = RUN.v2_text(x, n9, m9, "", "std")
    base9 = C.check_v2r3(m9, t9, "r3a", "std")
    negctl.append(("h0", "control: the real V2 log of pr_a1_v00 passes the R3a checker before it is corrupted", not base9, base9[:1] and base9[0][:100] or "pass"))
    L9 = t9.splitlines()
    gi9 = [k for k, l in enumerate(L9) if l.startswith("G ")]
    out9 = list(L9)
    for k, line in zip(gi9, [L9[k] for k in gi9][::-1]):
        out9[k] = line
    fails = C.check_v2r3(m9, "\n".join(out9) + "\n", "r3a", "std")
    negctl.append(("h", "check_v2r3 must FAIL on a real V2 log of pr_a1_v00 with the G records REVERSED across instructions [reason must contain: events out of order]",
                   any("events out of order" in f_ for f_ in fails), (fails[0] if fails else "ACCEPTED")[:120]))
    nP = "a8_ldpr_between"
    mP = x.man[nP]
    for kind in ("ext", "up"):
        eP = RUN.ref_text(x, nP, mP, kind)
        baseP = C.check_ref3(mP, eP, kind)
        negctl.append(("h0", f"control: the {kind} log of a8_ldpr_between passes check_ref3 before it is corrupted", not baseP, baseP[:1] and baseP[0][:100] or "pass"))
        LP = eP.splitlines()
        pi = [k for k, l in enumerate(LP) if l.startswith("P ")]
        assert len({LP[k].split()[1] for k in pi}) >= 2, "needs P events of two instructions"
        outP = list(LP)
        for k, line in zip(pi, [LP[k] for k in pi][::-1]):
            outP[k] = line
        fails = C.check_ref3(mP, "\n".join(outP) + "\n", kind)
        negctl.append(("h", f"check_ref3 ({kind}) must FAIL on a8_ldpr_between with the P records REVERSED across instructions [reason must contain: events out of order]",
                       any("events out of order" in f_ for f_ in fails), (fails[0] if fails else "ACCEPTED")[:120]))
        fails = C.check_ref3(mP, "\n".join(LP + ["PR id=5 val=deadbeef"]) + "\n", kind)
        negctl.append(("h", f"check_ref3 ({kind}) must FAIL on a8_ldpr_between with a duplicated PR id=5 record [reason must contain: duplicate final privileged dump line]",
                       any("duplicate final privileged dump line" in f_ for f_ in fails), (fails[0] if fails else "ACCEPTED")[:120]))


def negctl_parsers4(x, negctl):
    """R3a fixes 4 (Codex r4 should-fix 1): MALFORMED RECORD BOUNDARIES in the shared run-log parser (m1_r1_check.parse, used by the R1a /
    R2a / R3a checkers). Codex's three lines, appended to a real passing log, used to vanish; they and the adversarial variants (leading
    whitespace, tab, glue, case, CR / CRLF, NUL, form feed, vertical tab, U+2028, non-ASCII, trailing space, duplicate fields) must now
    fail through the checker, each for the intended reason; the real log, a banner with a non-ASCII path and prose that merely starts like a
    keyword must still pass."""
    print("== negative controls (i): malformed record boundaries (Codex r4)")
    import m1_r3_run as RUN
    n = "a3_same" if x.phase == "r3a" else "n1_42_abs"
    m = x.man[n]
    t = RUN.v2_text(x, n, m, "", "std")
    base = C.check_v2r3(m, t, x.phase, "std")
    negctl.append(("i0", f"control: the real V2 log of {n} passes the R3a checker before the boundary corruptions", not base, f"{n}: {base[:1]}"))
    BND = "malformed record boundary"
    CTL = "control character"
    cases = [
        # (description, text appended to the passing log, message the failure list must contain)
        ("`TRAP\\tmode ipc=00000099` appended (Codex r4, tab)", "TRAP\tmode ipc=00000099\n", BND),
        ("`END\\thalted=0 instrs=0` appended (Codex r4, tab)", "END\thalted=0 instrs=0\n", BND),
        ("` SIMCHECK errors=7 ipc_errors=2` appended (Codex r4, leading space)", " SIMCHECK errors=7 ipc_errors=2\n", BND),
        ("a tab in a TRAP record", "TRAP\tmode ipc=00000099\n", CTL),
        ("a leading space on END", " END halted=0 instrs=0\n", BND),
        ("a leading space on TRAP", " TRAP mode ipc=00000099\n", BND),
        ("a leading space on STALL", " STALL ipc=00000007 upc=3 retired=1\n", BND),
        ("a leading space on an L record", " L 99 pc=00000000 op=00\n", BND),
        ("a leading space on a C record", " C 99 dr=0 dw=0 priv=0\n", BND),
        ("a leading space on a W record", " W addr=00000800 size=4 data=00000001\n", BND),
        ("a leading space on a G record", " G 0 id=1 be=f val=00000001\n", BND),
        ("a leading space on a P record", " P 0 id=5 be=f val=00000001\n", BND),
        ("a leading space on a PR record", " PR id=5 val=00000000\n", BND),
        ("a leading space on a final register line", " R05=00000000\n", BND),
        ("a leading space on the final PSW line", " PSW=10000000\n", BND),
        ("a leading space on GPREV", " GPREV on\n", BND),
        ("a tab-prefixed record", "\tTRAP mode ipc=00000099\n", CTL),
        ("`ENDhalted=0 instrs=0` appended (glue)", "ENDhalted=0 instrs=0\n", BND),
        ("`SIMCHECKerrors=7 ipc_errors=2` appended (glue)", "SIMCHECKerrors=7 ipc_errors=2\n", BND),
        ("`TRAPmode ipc=00000099` appended (glue)", "TRAPmode ipc=00000099\n", BND),
        ("`L1 pc=00000000 op=00` appended (glue)", "L1 pc=00000000 op=00\n", BND),
        ("`Waddr=00000800 size=4 data=00000001` appended (glue)", "Waddr=00000800 size=4 data=00000001\n", BND),
        ("`trap mode ipc=00000099` appended (lower case)", "trap mode ipc=00000099\n", BND),
        ("`Trap mode ipc=00000099` appended (title case)", "Trap mode ipc=00000099\n", BND),
        ("`end halted=0 instrs=0` appended (lower case)", "end halted=0 instrs=0\n", BND),
        ("`Simcheck errors=7 ipc_errors=2` appended (title case)", "Simcheck errors=7 ipc_errors=2\n", BND),
        ("`psw=10000000` appended (lower case)", "psw=10000000\n", BND),
        ("`r05=00000000` appended (lower case)", "r05=00000000\n", BND),
        ("`l 99 pc=00000000 op=00` appended (lower case)", "l 99 pc=00000000 op=00\n", BND),
        ("`R05 =00000000` appended (space before the equals sign)", "R05 =00000000\n", BND),
        ("a TRAP record with a trailing space", "TRAP mode ipc=00000099 \n", "malformed TRAP record"),
        ("a TRAP record with a duplicate field", "TRAP mode ipc=00000099 ipc=00000007\n", "malformed TRAP record"),
        ("an END record with a duplicate field", "END halted=0 halted=0 instrs=0\n", "duplicate field"),
        ("a CR before the newline of an appended TRAP record (CRLF)", "TRAP mode ipc=00000099\r\n", CTL),
        ("a lone CR line end", "TRAP mode ipc=00000099\r", CTL),
        ("a NUL byte inside a record", "TRAP mode\x00 ipc=00000099\n", CTL),
        ("a NUL-only line", "\x00\n", CTL),
        ("a form feed gluing two records (str.splitlines would split them)", "SIMCHECK errors=7 ipc_errors=2\x0cEND halted=0 instrs=0\n", CTL),
        ("a vertical tab inside a record", "TRAP\x0bmode ipc=00000099\n", CTL),
        ("U+2028 inside a record", "TRAP mode\u2028ipc=00000099\n", CTL),
        ("U+0085 inside a record", "TRAP mode\u0085ipc=00000099\n", CTL),
        ("a no-break space as the separator", "TRAP\u00a0mode ipc=00000099\n", "non-ASCII character"),
        ("a non-ASCII letter in a record", "TRAP mode ipc=0000009\u00e9\n", "non-ASCII character"),
        ("a Cyrillic look-alike keyword", "\u0422RAP mode ipc=00000099\n", "non-ASCII character"),
    ]
    for what, add, need in cases:
        fails = C.check_v2r3(m, t + add, x.phase, "std")
        negctl.append(("i", f"R3a checker must FAIL on the real {n} V2 log with {what} [reason must contain: {need}]",
                       any(need in f_ for f_ in fails), (fails[0] if fails else "ACCEPTED")[:120]))
    m8_ = x.man["a8_same_val_regs"]
    e8_, u8_ = RUN.ref_text(x, "a8_same_val_regs", m8_, "ext"), RUN.ref_text(x, "a8_same_val_regs", m8_, "up")
    negctl.append(("i0", "control: the upstream log of a8_same_val_regs passes check_ref3 (up) before the closed-grammar corruptions", not C.check_ref3(m8_, u8_, "up"), "up"))
    # R3a fixes 5: the CLOSED grammar of the shared evaluator. The four survivors of the per-line mutation fuzz, then unknown text
    cl = "unrecognised line"
    mal = "malformed "
    base_lines = t.splitlines()
    def swap_line(prefix, fn):
        out = [fn(l) if l.startswith(prefix) else l for l in base_lines]
        return "\n".join(out) + "\n"
    closed_cases = [
        ("the COUNTERS record with a trailing space (fuzz survivor)", swap_line("COUNTERS ", lambda l: l + " "), mal + "COUNTERS record"),
        ("the COUNTERS record with a double space (fuzz survivor)", swap_line("COUNTERS ", lambda l: l.replace(" ", "  ", 1)), mal + "COUNTERS record"),
        ("the REDIRECTS record with a trailing space (fuzz survivor)", swap_line("REDIRECTS ", lambda l: l + " "), mal + "REDIRECTS record"),
        ("the REDIRECTS record with a double space (fuzz survivor)", swap_line("REDIRECTS ", lambda l: l.replace(" ", "  ", 1)), mal + "REDIRECTS record"),
        ("a COUNTERS record with a field missing", swap_line("COUNTERS ", lambda l: l.replace(" iter=0", "")), mal + "COUNTERS record"),
        ("a COUNTERS record with a leading zero", swap_line("COUNTERS ", lambda l: l.replace("mem=0", "mem=00")), mal + "COUNTERS record"),
        ("a REDIRECTS record with an unknown suffix", swap_line("REDIRECTS ", lambda l: l.replace("(data-bus prefetch)", "(something)").replace("(        wide port)", "(something)")), mal + "REDIRECTS record"),
        ("`FATAL: test failure` appended", t + "FATAL: test failure\n", "FATAL: test failure"),
        ("an unknown line `MR io addr=c0000a data=0000 be=11` appended", t + "MR io addr=c0000a data=0000 be=11\n", cl),
        ("an unknown line `hello` appended", t + "hello\n", cl),
        ("a mutated keyword `TRAPP mode ipc=00000099` appended", t + "TRAPP mode ipc=00000099\n", cl),
        ("a mutated keyword `SIMCHEK errors=0 ipc_errors=0` appended", t + "SIMCHEK errors=0 ipc_errors=0\n", cl),
        ("a mutated keyword `ENDS halted=0 instrs=0` appended", t + "ENDS halted=0 instrs=0\n", cl),
        ("prose that merely begins like a keyword (`Endpoint reached`) appended", t + "Endpoint reached\n", cl),
        ("an empty line appended", t + "\n", "blank line"),
        ("a whitespace-only line appended", t + "   \n", "blank line"),
        ("a MEMSTAT record with an unknown region", t + "MEMSTAT read  bogus          3\n", mal + "MEMSTAT record"),
        ("a MEMSTAT record with a trailing space", t + "MEMSTAT read  rom            3 \n", mal + "MEMSTAT record"),
        ("a MEMSTAT record with a non-numeric count", t + "MEMSTAT read  rom            x\n", mal + "MEMSTAT record"),
        ("a MEMSTAT record with wrong padding", t + "MEMSTAT read  rom               3\n", mal + "MEMSTAT record"),
        ("a `%Warning` line appended", t + "%Warning-WIDTH: x.sv:1: bad\n", cl),
        ("an upstream-style `M R io addr=c0000a data=0000 be=11` line appended", t + "M R io addr=c0000a data=0000 be=11\n", cl),
        ("a second malformed banner `- Verilator: whatever` appended", t + "- Verilator: whatever\n", cl),
        ("a G record with a trailing space", t + "G 0 id=1 be=f val=00000001 \n", mal + "G record"),
        ("a PR record with id=40", t + "PR id=40 val=00000000\n", mal + "PR record"),
    ]
    for what, txt, need in closed_cases:
        fails = C.check_v2r3(m, txt, x.phase, "std")
        negctl.append(("i", f"R3a checker must FAIL on the real {n} V2 log with {what} [reason must contain: {need}]",
                       any(need in f_ for f_ in fails), (fails[0] if fails else "ACCEPTED")[:120]))
    for what, add in (("`FATAL: test failure`", "FATAL: test failure\n"), ("an unknown line", "hello\n"), ("a mutated keyword `TRAPP`", "TRAPP mode ipc=00000099\n")):
        for kind_, log_ in (("ext", e8_), ("up", u8_)):
            fails = C.check_ref3(m8_, log_ + add, kind_)
            negctl.append(("i", f"check_ref3 ({kind_}) must FAIL on a8_same_val_regs with {what}", bool(fails), (fails[0] if fails else "ACCEPTED")[:120]))
    # the whole text with CRLF line ends (a Windows-style copy of the log)
    fails = C.check_v2r3(m, t.replace("\n", "\r\n"), x.phase, "std")
    negctl.append(("i", f"R3a checker must FAIL on the real {n} V2 log converted to CRLF [reason must contain: {CTL}]", any(CTL in f_ for f_ in fails), (fails[0] if fails else "ACCEPTED")[:120]))
    # the same through the other users of the idiom: a reference / upstream log (check_ref3), the R1a and R2a checkers
    n8 = "a8_same_val_regs"
    m8 = x.man[n8]
    e8 = RUN.ref_text(x, n8, m8, "ext")
    base8 = C.check_ref3(m8, e8, "ext")
    negctl.append(("i0", "control: the corrected-V60X log of a8_same_val_regs passes check_ref3 before the boundary corruptions", not base8, base8[:1] and base8[0][:100] or "pass"))
    for what, add, need in cases[:3] + [("a leading space on a G record", " G 0 id=1 val=00000001\n", BND), ("a leading space on a P record", " P 0 id=5 val=00000001\n", BND)]:
        fails = C.check_ref3(m8, e8 + add, "ext")
        negctl.append(("i", f"check_ref3 (ext) must FAIL on a8_same_val_regs with {what} [reason must contain: {need}]", any(need in f_ for f_ in fails), (fails[0] if fails else "ACCEPTED")[:120]))
    import m1_r1_check as C1
    import m1_r2_check as C2
    for lab, parse_ in (("m1_r1_check.parse", C1.parse), ("m1_r2_check.parse2", C2.parse2)):
        for what, add, need in cases[:3]:
            d_ = parse_(t + add)
            negctl.append(("i", f"{lab} must record a FATAL error for {what}", any(need in f_ for f_ in d_["FATAL"]), (d_["FATAL"][0] if d_["FATAL"] else "ACCEPTED")[:120]))
    # controls: what must NOT be flagged
    ok_cases = [
        ("a Verilator banner line with a non-ASCII host path", "- /Users/Jos\u00e9/ga2/tb/tb_m1_v2.sv:255: Verilog $finish\n"),
        ("a banner line", "- Verilator: cpu 0.003 s on 1 threads; allocated 3 MB\n"),
    ]
    for what, add in ok_cases:
        fails = C.check_v2r3(m, t + add, x.phase, "std")
        negctl.append(("i0", f"control: the R3a checker still passes the real {n} V2 log with {what}", not fails, (fails[0] if fails else "pass")[:120]))


def negctl_parsers5(x, negctl):
    """R3a Codex-r5 fixes (Codex r5, four should-fixes; strict mode). The exact reproductions of docs/reviews/R3a-codex-review-r5.md, each as a
    permanent negative control with the message the rejection must contain:
      1. contradictory COUNTERS / REDIRECTS / MEMSTAT records appended to the real passing a3_same V2 log;
      2. a banner line with non-ASCII digits, and an undecodable byte in the banner path (the S0 side is in test_s0_compare.py);
      3. CRLF: through the REAL shared helper (RR.sh, which used to run subprocess.run(text=True)), through a log file read back by the
         runners (read_log), and through the D1 runner (default_runners);
      4. D1: a tab-only line, `- FATAL: test failure`, `- TRAP mode ipc=00000099`, a blank line, CRLF (the documented exception is gone)."""
    print("== negative controls (j): Codex r5 strict-mode fixes")
    import m1_r3_run as RUN
    import m1_probe_movea as PV
    n = "a3_same" if x.phase == "r3a" else "n1_42_abs"
    m = x.man[n]
    t = RUN.v2_text(x, n, m, "", "std")
    base = C.check_v2r3(m, t, x.phase, "std")
    negctl.append(("j0", f"control: the real V2 log of {n} passes the R3a checker before the Codex r5 corruptions", not base, f"{n}: {base[:1]}"))
    # --- 1. contradictory singleton summaries (Codex r5 should-fix 1, the two lines verbatim, then the MEMSTAT pair)
    c5 = "COUNTERS pre=999 exec=999 mem=999 fetch=999 iter=999\n"
    r5 = "REDIRECTS reset=999 instr=999 instr_outstanding=999 (data-bus prefetch)\n"
    r5w = "REDIRECTS reset=999 instr=999 instr_outstanding=999 (        wide port)\n"
    def ms_(kind, region, cnt):          # the printed form: "MEMSTAT read  " / "MEMSTAT write " then %-14s then the count
        return f"MEMSTAT {kind}{' ' if kind == 'write' else '  '}{region:<14} {cnt}\n"
    cases = [
        ("Codex r5 should-fix 1: `COUNTERS pre=999 ...` appended (contradicts the real one)", t + c5, "duplicate COUNTERS record"),
        ("Codex r5 should-fix 1: `REDIRECTS reset=999 ...` appended (contradicts the real one)", t + r5, "duplicate REDIRECTS record"),
        ("a REDIRECTS record in the other printed form appended", t + r5w, "duplicate REDIRECTS record"),
        ("Codex r5 should-fix 1: two `MEMSTAT read rom` records with different counts", t + ms_("read", "rom", 3) + ms_("read", "rom", 4), "duplicate MEMSTAT read rom"),
        ("two `MEMSTAT write wram` records, one padded and one with single spaces", t + "MEMSTAT write wram 1\n" + ms_("write", "wram", 2), "duplicate MEMSTAT write wram"),
        ("two `MEMSTAT rom_writes_ignored` records", t + "MEMSTAT rom_writes_ignored 0\nMEMSTAT rom_writes_ignored 1\n", "duplicate MEMSTAT rom_writes_ignored"),
        ("COUNTERS twice, identical (a bench prints it once)", t + c5 + c5, "duplicate COUNTERS record"),
        # --- 2. banner characters (should-fix 2)
        ("Codex r5 should-fix 2: `- Verilator: cpu ٠.١ s on ١ threads; allocated ٥ MB` appended (non-ASCII digits)",
         t + "- Verilator: cpu ٠.١ s on ١ threads; allocated ٥ MB\n", "unrecognised line"),
        ("non-ASCII digits in the $finish line number of the banner", t + "- /Users/x/tb/tb_m1_v2.sv:٢٢٤: Verilog $finish\n", "unrecognised line"),
        ("an undecodable byte (U+FFFD) inside the banner's host path", t + "- /Users/x�/ga2/tb/tb_m1_v2.sv:255: Verilog $finish\n", "unrecognised line"),
    ]
    for what, txt, need in cases:
        fails = C.check_v2r3(m, txt, x.phase, "std")
        negctl.append(("j", f"R3a checker must FAIL on the real {n} V2 log: {what} [reason must contain: {need}]",
                       any(need in f_ for f_ in fails), (fails[0] if fails else "ACCEPTED")[:120]))
    # the same through the raw bytes: an invalid UTF-8 byte in the banner path, through decode_log (what every runner and CLI uses)
    bad = (t + "- /Users/xé/ga2/tb/tb_m1_v2.sv:255: Verilog $finish\n").encode().replace("é".encode(), b"\xff")
    fails = C.check_v2r3(m, C1.decode_log(bad), x.phase, "std")
    negctl.append(("j", "R3a checker must FAIL on the real V2 log with the invalid UTF-8 byte 0xff in the banner path (bytes, via decode_log)", any("unrecognised line" in f_ for f_ in fails), (fails[0] if fails else "ACCEPTED")[:120]))
    # --- 3. line endings must reach the evaluator (should-fix 3)
    d = x.out / "negctl_j"
    d.mkdir(exist_ok=True)
    (d / "crlf.log").write_bytes(t.encode().replace(b"\n", b"\r\n"))
    helper = RR.sh(["cat", str(d / "crlf.log")])
    fails = C.check_v2r3(m, helper.stdout, x.phase, "std")
    negctl.append(("j", "CRLF bytes through the REAL shared helper RR.sh must still carry their CR (Codex r5 should-fix 3: text=True normalised it away) and the checker must FAIL [control character]",
                   "\r" in helper.stdout and any("control character" in f_ for f_ in fails), (fails[0] if fails else "ACCEPTED")[:120]))
    (d / "crlf.v2_default").write_bytes(t.encode().replace(b"\n", b"\r\n"))
    fails = C.check_v2r3(m, C1.read_log(d / "crlf.v2_default"), x.phase, "std")
    negctl.append(("j", "a CRLF log file read back by the runners (read_log, was Path.read_text) must FAIL [control character]", any("control character" in f_ for f_ in fails), (fails[0] if fails else "ACCEPTED")[:120]))
    (d / "bad.stderr.sh").write_text("#!/bin/sh\nprintf 'TRAP mode ipc=00000099\\r\\n' >&2\n")
    r2 = RR.sh(["sh", str(d / "bad.stderr.sh")])
    negctl.append(("j", "stderr through RR.sh keeps its CR as well", r2.stderr == "TRAP mode ipc=00000099\r\n", repr(r2.stderr)))
    # --- 4. D1 (should-fix 4) and its runner
    wd = x.out / "pvctl"
    wd.mkdir(exist_ok=True)
    good = PV.default_runners(x.out / "obj_up/sim")
    binp, hexp = wd / "probe_movea_value.bin", wd / "probe_movea_value.hex"
    PV.write(PV.image(), str(wd / "probe_movea_value"))
    ref_t, up_t = good["ref"](binp, hexp), good["up"](binp, hexp)
    ok, why = PV.evaluate(ref_t, up_t)
    negctl.append(("j0", "control: the real D1 runs (with the Verilator banner on upstream's side) still reproduce D1 under the strict parser", ok, why[:110]))
    def both(f):
        return (ref_t[0], f(ref_t[1])), (up_t[0], f(up_t[1]))
    def ins(txt, line):          # a line before END
        return txt.replace("END ", line + "\nEND ", 1)
    d1 = {
        "a tab-only line (Codex r5 should-fix 4)": (ref_t, (0, ins(up_t[1], "\t")), "control or non-ASCII character"),
        "a tab-only line on v60_ref's side": ((0, ins(ref_t[1], "\t")), up_t, "control or non-ASCII character"),
        "`- FATAL: test failure` (Codex r5 should-fix 4)": (ref_t, (0, ins(up_t[1], "- FATAL: test failure")), "unrecognised line"),
        "`- TRAP mode ipc=00000099` (Codex r5 should-fix 4)": (ref_t, (0, ins(up_t[1], "- TRAP mode ipc=00000099")), "unrecognised line"),
        "`- FATAL: test failure` on v60_ref's side": ((0, ins(ref_t[1], "- FATAL: test failure")), up_t, "unrecognised line"),
        "an empty line": (ref_t, (0, ins(up_t[1], "")), "blank line"),
        "a whitespace-only line": (ref_t, (0, ins(up_t[1], "   ")), "blank line"),
        "a second copy of the $finish banner line": (ref_t, (0, up_t[1] + up_t[1].splitlines()[-4] + "\n"), "duplicate Verilator banner"),
        "a banner with non-ASCII digits": (ref_t, (0, up_t[1] + "- Verilator: cpu ٠.١ s on ١ threads; allocated ٥ MB\n"), "non-ASCII character"),
        "CRLF line ends on upstream's side (Codex r5 should-fix 4)": (ref_t, (0, up_t[1].replace("\n", "\r\n")), "control or non-ASCII character"),
        "CRLF line ends on v60_ref's side": ((0, ref_t[1].replace("\n", "\r\n")), up_t, "control or non-ASCII character"),
        "a lone CR": (ref_t, (0, up_t[1].replace("END ", "\rEND ", 1)), "control or non-ASCII character"),
        "a form feed gluing two records (str.splitlines would have split them)": (ref_t, (0, up_t[1].replace("\nEND ", "\x0cEND ", 1)), "control or non-ASCII character"),
        "an invalid UTF-8 byte (U+FFFD) in the output": (ref_t, (0, ins(up_t[1], "X�")), "non-ASCII character"),
        "the final newline missing": (ref_t, (0, up_t[1][:-1]), "does not end with a newline"),
    }
    for what, (r_, u_, need) in d1.items():
        res, why = PV.evaluate(r_, u_)
        negctl.append(("j", f"probe_movea_value must FAIL when {what} [reason must contain: {need}]", (not res) and need in why, why[:110]))
    # the D1 runner: CRLF produced by the simulator itself must reach the parser (text=True used to erase it)
    fk = wd / "fake_sim_crlf.sh"
    (wd / "up_crlf.txt").write_bytes(up_t[1].encode().replace(b"\n", b"\r\n"))
    fk.write_text(f"#!/bin/sh\ncat '{wd / 'up_crlf.txt'}'\n")
    fk.chmod(0o755)
    runners = PV.default_runners(fk)
    rc_, out_ = runners["up"](binp, hexp)
    res, why = PV.evaluate(ref_t, (rc_, out_))
    negctl.append(("j", "probe_movea_value must FAIL when the simulator itself emits CRLF (through default_runners, bytes) [reason must contain: control or non-ASCII character]",
                   "\r" in out_ and (not res) and "control or non-ASCII character" in why, why[:110]))


def negctl_dflags(x, negctl, note):
    """ASSUMED #2: which dispatch-table format bits the planned execute step consumes (real V2, probe image)"""
    print("== dispatch format bits of the probe (ASSUMED #2)")
    import m1_r3_run as RUN
    fmt_movw = 2 | (2 << 2) | (0 << 4) | (1 << 8) | (1 << 10)
    progs = [n for n in x.man if n.startswith(("pr_a1_v0", "pr_a2_r0", "pr_a2_r3", "pr_a6_s0", "pr_a8_same"))]
    variants = {"fmt = 0 (the clean probe)": 0, "fmt_movw (sizes W/W, layout 0, two operands, op2 written)": fmt_movw,
                "sizes H/H, layout 1": (1 | (1 << 2) | (1 << 4)),
                "all non-layout dflags set except privileged (bits 8-11)": (0xF << 8)}
    for what, fmt in variants.items():
        mc, _ = RUN.build_probe3(x, "mc_probe3_fmt", {"dflags": fmt})
        bad = sum(1 for n in progs if C.check_v2r3(x.man[n], RUN.v2_text(x, n, x.man[n], "", "std", mc=mc), "r3a", "std"))
        note("probe execute step is independent of the dispatch size / flag bits and works with layout 0 or 1 (fld1 = byte 1 [4:0])", bad == 0, f"{what}: {bad}/{len(progs)} fail")
    for lay in (2, 3):
        mc, _ = RUN.build_probe3(x, "mc_probe3_fmt", {"dflags": (lay << 4)})
        bad = sum(1 for n in progs if C.check_v2r3(x.man[n], RUN.v2_text(x, n, x.man[n], "", "std", mc=mc), "r3a", "std"))
        negctl.append(("f", f"dispatch layout {lay} (fld1 = a different byte) breaks the execute step, as expected: R3b must use layout 0 or 1", bad > 0, f"{bad}/{len(progs)} programs fail"))


def run(x, negctl, note):
    negctl_refs(x, negctl)
    negctl_logs(x, negctl)
    negctl_benches(x, negctl)
    negctl_three(x, negctl)
    negctl_probe_value(x, negctl)
    negctl_parsers(x, negctl)
    negctl_parsers3(x, negctl)
    negctl_parsers4(x, negctl)
    negctl_parsers5(x, negctl)
    negctl_dflags(x, negctl, note)
    fr = HERE / "results/r3a_frozen_expectations_2026-10-29.json"
    if fr.exists():
        import m1_r3 as G
        rec = json.loads(fr.read_text())
        dg = G.digest(x.man)
        negctl.append(("z0", "control: the regenerated manifest equals the frozen-expectation record", rec["manifest_sha256"] == dg, f"{dg[:16]}"))
        m2 = copy.deepcopy(x.man)
        m2["a1_v00"]["exp_gevents"]["2"] = [[5, 1]]
        negctl.append(("z", "a one-event change of the manifest breaks the frozen digest", G.digest(m2) != rec["manifest_sha256"], "digest differs"))
