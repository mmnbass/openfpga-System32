#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R4: the executable S0 criterion s0_compare.py --r4b-check against the real GA2 prefix (docs/r4-movea-pcrel-plan.md section 6.4 / 4.2).

Runs only where the local, git-ignored GA2 inputs exist (build/S0/rom/ga2u_prog.hex, build/S0/boot/boot_head.trace); otherwise the stage
is reported as skipped (never as a pass). Only counts, PCs, one-byte opcodes and register values of the boot prefix (already in the plan)
are printed; no ROM bytes. All outputs go to <run-dir>/s0 (a NEW directory).

  1. upstream S0 runs: a PREFIX run (+maxinstr=7: records 0..7), +maxinstr=200 and +maxinstr=20000, with +counters +privev +memlog; the
     reference state at the reach point is the REPLAY of the first 7 records, never a final dump
  2. the real V2 (this run's microcode) on the real ROM with +privev +gprev +drainrec (+counters from v2_cmd)
       phase r4b: --r4b-check PASSES with each upstream log (reach 7, TRAP opcode ipc=0010052d, ...); --r3b-check FAILS (reach 7 != 4)
       phase r4a: --r3b-check PASSES (the R3b state), --r4b-check FAILS for exactly the expected reasons (reach 4 != 7, not TRAP opcode)
  3. a V2-format log SYNTHESISED from the real upstream prefix with the planned end passes with each upstream log; every corruption fails
     (the unit-test corruptions of test_s0_compare.py re-run against REAL upstream text), including upstream effects at index 6 (rejected)
     versus at index 7 (accepted)
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m1_r1_run as RR  # noqa: E402

CMP = HERE.parent / "S0-boot" / "s0_compare.py"
SYNTH_MEMREC = ["DRAIN data_busy=0 d_active=0 owner=2 d_starts=0 d_acks=0 d_wr_acks=0 pf_acks=17 at_trap_data_busy=0 at_trap_d_starts=0 at_trap_d_acks=0 settle=312",
                "MEMSTAT read  rom            24", "MEMSTAT read  rom-mirror     6", "MEMSTAT rom_writes_ignored 0",
                "MEMDRAIN reqs=30 acks=29 rd_reqs=30 wr_reqs=0 wr_acks=0 outstanding=1 outstanding_wr=0 nonrom_reqs=0 m_req=1 busy=1"]


def compare(up, v2, mame, *flags):
    r = subprocess.run([sys.executable, str(CMP), str(up), str(v2), str(mame), *flags], capture_output=True, text=True)
    return r.returncode, r.stderr.strip(), r.stdout


def synth_v2(up_text, n=7):
    """a V2-format log for the planned R4b end, built from the REAL upstream prefix: the first 7 lock-step records, their privileged events,
    GPR events, per-instruction counters, a trap `opcode` at the mov.b with its (empty) in-flight CU record, the final GPR / PSW dump, V2's
    privileged dump (zero + the prefix's privileged writes), END, SIMCHECK and the memory-bench constants"""
    out = ["GPREV on"]
    regs, psw = [0] * 32, 0x10000000
    pv = {i: 0 for i in range(32)}
    nP, nG = {}, {}
    for line in up_text.splitlines():
        m = re.match(r"^P (\d+) id=(\d+) val=([0-9a-f]+)", line)
        if m and int(m.group(1)) < n:
            out.append(f"P {m.group(1)} id={m.group(2)} be=f val={m.group(3)}")
            pv[int(m.group(2))] = int(m.group(3), 16)
            nP[int(m.group(1))] = nP.get(int(m.group(1)), 0) + 1
        m = re.match(r"^L (\d+) pc=(\w+) op=(\w+)(.*)$", line)
        if m and int(m.group(1)) < n:
            k_ = int(m.group(1))
            for k, v in re.findall(r"(R\d\d|PSW)=([0-9a-f]+)", m.group(4)):
                if k == "PSW":
                    psw = int(v, 16)
                else:
                    regs[int(k[1:])] = int(v, 16)
                    out.append(f"G {k_} id={int(k[1:])} be=f val={v}")
                    nG[k_] = nG.get(k_, 0) + 1
            out.append(line)
            out.append(f"C {k_} dr=0 dw=0 priv={nP.get(k_, 0)} psw=0 gpr={nG.get(k_, 0)}")
    out += [f"R{i:02d}={regs[i]:08x}" for i in range(32)]
    out.append(f"PSW={psw:08x}")
    out += [f"PR id={i} val={pv[i]:08x}" for i in range(32)]
    out += ["CU 7 dr=0 dw=0 priv=0 psw=0 gpr=0"] + [SYNTH_MEMREC[0]] + [
            "TRAP opcode ipc=0010052d", "END halted=0 instrs=7 cycles=0", "COUNTERS pre=0 exec=0 mem=0 fetch=0 iter=0", "SIMCHECK errors=0 ipc_errors=0"]
    out += SYNTH_MEMREC[1:]
    return "\n".join(out) + "\n"


def run(x, negctl, note):
    root = x.root
    rom = root / "build/S0/rom/ga2u_prog.hex"
    trace = root / "build/S0/boot/boot_head.trace"
    print("== S0 --r4b-check against the real GA2 prefix")
    if not (rom.exists() and trace.exists()):
        print("   SKIPPED: build/S0/rom/ga2u_prog.hex or build/S0/boot/boot_head.trace is absent (local, git-ignored inputs)")
        x.s0_skipped = True
        return
    d = x.out / "s0"
    d.mkdir(exist_ok=False)
    mame16 = d / "mame_head.trace"
    mame16.write_text("".join(list(open(trace, errors="replace"))[:16]))                 # PCs and mnemonics only
    sup = str(x.out / "obj_s0up/sim")
    for nm, n in (("prefix", 7), ("mid", 200), ("long", 20000)):
        (d / f"up_{nm}.log").write_text(RR.sh([sup, f"+rom={rom}", f"+maxinstr={n}", "+counters", "+privev", "+memlog"]).stdout)
    mc = x.out / "mc.s0"
    syms = RR.symbols(mc)
    v2cmd = RR.v2_cmd(str(x.out / "obj_s0v2/sim"), mc, [f"+rom={rom}", "+maxinstr=1000", "+maxcyc=2000000", "+stallcyc=20000", "+privev", "+gprev", "+drainrec"], syms)
    (d / "v2.log").write_text(RR.sh(v2cmd).stdout)
    v2_t = (d / "v2.log").read_text()
    kind = re.search(r"^(TRAP \S+ ipc=\w+|STALL .*)$", v2_t, re.M)
    x.s0_end = kind.group(1) if kind else None
    ups = {nm: d / f"up_{nm}.log" for nm in ("prefix", "mid", "long")}
    up_t = (d / "up_prefix.log").read_text()
    recs = len(re.findall(r"^L \d+ ", up_t, re.M))
    long_t = (d / "up_long.log").read_text()
    seg67 = long_t.split("L 6 ")[1].split("L 7 ")[0] if "L 6 " in long_t and "L 7 " in long_t else ""
    negctl.append(("s0a", f"real upstream logs: the prefix run (+maxinstr=7) has {recs} lock-step records (0..7) and no W / M line at all; in the long run records 0-6 carry "
                          "no W / M line and the byte store of index 7 (W addr=00e00000 size=1) is printed between L 6 and L 7",
                   recs == 8 and not any(l.startswith(("W ", "M ")) for l in up_t.splitlines())
                   and not any(l.startswith(("W ", "M ")) for l in long_t.split("L 6 ")[0].splitlines())
                   and "W addr=00e00000 size=1" in seg67, f"{recs} records"))
    json_out = d / "s0_r4b_result.json"
    json_long = d / "s0_r4b_full_result.json"
    res = {}
    for nm, upf in ups.items():
        for tag, mame in (("16-line MAME head", mame16), ("full 199,999-line MAME head", trace)):
            if nm != "long" and mame is trace:
                continue
            rc4, err4, out4 = compare(upf, d / "v2.log", mame, "--r4b-check", "--json", str(json_out if nm == "prefix" else d / f"s0_r4b_{nm}_{'full' if mame is trace else 'head'}.json"))
            rc3, err3, _ = compare(upf, d / "v2.log", mame, "--r3b-check")
            res[(nm, tag)] = (rc4, err4, rc3, err3)
            if x.phase == "r4b":
                negctl.append(("s0b", f"R4b: the REAL V2 passes --r4b-check on the real GA2 prefix (upstream {nm} run, {tag}): reach 7, TRAP opcode ipc=0010052d, "
                                      "R31/R25/R10/R11/R1/SBR/PSW, counters, G events, memory bench, drained state",
                               rc4 == 0 and "PASS" in err4 and x.s0_end == "TRAP opcode ipc=0010052d", f"{err4[:120]}; V2 ended {x.s0_end}"))
                negctl.append(("s0b3", f"R4b: --r3b-check FAILS the real R4b V2 log, by design (reach 7 != 4; upstream {nm} run, {tag})",
                               rc3 == 1 and "reach 7 != 4" in err3, err3[:100]))
            else:
                negctl.append(("s0b", f"R4a: today's V2 FAILS --r4b-check on the real GA2 prefix for exactly the expected reasons (upstream {nm} run, {tag}): reach 4 != 7, "
                                      "not TRAP opcode at 0010052d, END instrs, no counters / G events past instruction 3",
                               rc4 == 1 and "reach 4 != 7" in err4 and "V2 end is not TRAP opcode at 0010052d" in err4 and "TRAP mode ipc=0010051c" in (x.s0_end or "")
                               or (rc4 == 1 and "reach 4 != 7" in err4 and "V2 end is not TRAP opcode at 0010052d" in err4), err4[:200]))
                negctl.append(("s0b3", f"R4a: today's V2 still PASSES --r3b-check on the real GA2 prefix (the R3b state is intact; upstream {nm} run, {tag})",
                               rc3 == 0 and "PASS" in err3, err3[:100]))
    # synthesised planned end from the real upstream prefix: passes with every upstream log
    synth = synth_v2(up_t)
    (d / "v2_r4b_synth.log").write_text(synth)
    for nm, upf in ups.items():
        rc, err, out = compare(upf, d / "v2_r4b_synth.log", mame16, "--r4b-check")
        jr = json.loads(out)["r4b_check"] if out.strip().startswith("{") else {}
        negctl.append(("s0c0", f"control: the planned R4b end (V2 synthesised from the real upstream prefix) PASSES --r4b-check with upstream's {nm} run",
                       rc == 0 and "PASS" in err, f"{err[:60]}; state source: {jr.get('state_source')}; long-run upstream log: {jr.get('upstream_log_is_long_run')}"))
    # corruptions: each must FAIL; run on the synthesised log (both phases) and, in r4b, on the real log too
    bases = [("synth", synth)] + ([("real", v2_t)] if x.phase == "r4b" else [])
    for src, text in bases:
        lines = text.splitlines()

        def edit(fn, lines=lines):
            return "\n".join(fn(list(lines))) + "\n"

        def sub(pat, rep, once_on=None):
            def fn(L):
                return [re.sub(pat, rep, l) if (once_on is None or l.startswith(once_on)) else l for l in L]
            return fn

        def ins_before(L, prefix, *new_lines):
            i = next(k for k, l in enumerate(L) if l.startswith(prefix))
            return L[:i] + list(new_lines) + L[i:]
        corr = {
            "TRAP kind mode at 0010052d": sub(r"TRAP opcode", "TRAP mode", "TRAP"),
            "TRAP opcode at 0010052a (the mov.w #8,R1)": sub(r"ipc=0010052d", "ipc=0010052a", "TRAP"),
            "TRAP mode at 0010051c (the R3b end)": lambda L: [l if not l.startswith("TRAP") else "TRAP mode ipc=0010051c" for l in L],
            "no trap, the run just ends": lambda L: [l for l in L if not l.startswith("TRAP")],
            "R10 = 0x00100593 (wrong base: next PC)": sub(r"R10=0010058c", "R10=00100593", None),
            "R10 = 0x0010058f (wrong base: field address)": sub(r"R10=0010058c", "R10=0010058f", None),
            "R10 = 0x0010058e (wrong base: head)": sub(r"R10=0010058c", "R10=0010058e", None),
            "R10 = 0 (contents / base 0)": sub(r"R10=0010058c", "R10=00000000", None),
            "R10 = 0x00000070 (the raw displacement)": sub(r"R10=0010058c", "R10=00000070", None),
            "R11 wrong in record 5": sub(r"R11=00e00000", "R11=00e00001", "L 5 "),
            "R1 wrong in record 6": sub(r"R01=00000008", "R01=00000009", "L 6 "),
            "G event of instruction 4 removed": lambda L: [l for l in L if not l.startswith("G 4 ")],
            "G event of instruction 4 names R11": sub(r"G 4 id=10", "G 4 id=11", "G 4 "),
            "G event value of instruction 5 wrong": sub(r"val=00e00000", "val=00e00001", "G 5 "),
            "a stray GPR event of the trapping instruction": lambda L: ins_before(L, "TRAP", "G 7 id=10 be=f val=0010058d"),
            "a data write before the trap": lambda L: ins_before(L, "TRAP", "W addr=00200000 size=4 data=deadbeef"),
            "the PC-relative MOVEA counting a data read (C 4 dr=1)": sub(r"dr=0", "dr=1", "C 4 "),
            "the PC-relative MOVEA counting a PSW update (C 4 psw=2)": sub(r"psw=0", "psw=2", "C 4 "),
            "the trapping mov.b counting a data write (CU 7 dw=1)": sub(r"dw=0", "dw=1", "CU 7 "),
            "PSW changed by record 4": sub(r"(R10=0010058c)", r"\1 PSW=10000001", "L 4 "),
            "an extra privileged write by record 4": lambda L: ins_before(L, "L 4 ", "P 4 id=7 be=f val=00000070"),
            "END instrs=6": sub(r"instrs=7", "instrs=6", "END"),
            "final dump R10 wrong": sub(r"R10=0010058c", "R10=0010058d", "R10="),
            "final dump R11 wrong": sub(r"R11=00e00000", "R11=00e00001", "R11="),
            "final dump R01 wrong": sub(r"R01=00000008", "R01=00000000", "R01="),
            "an unrelated register in the final dump (R05)": sub(r"R05=00000000", "R05=00000001", "R05="),
            "the SBR write missing": lambda L: [l for l in L if not l.startswith("P 1 ")],
            "GPR instrumentation removed": lambda L: [l for l in L if not l.startswith(("GPREV", "G "))],
            "simulation-check errors set": sub(r"errors=0 ipc_errors=0", "errors=1 ipc_errors=1", "SIMCHECK"),
            "a data-port transaction started (DRAIN d_starts=1)": sub(r"d_starts=0", "d_starts=1", "DRAIN "),
            "counters removed": lambda L: [l for l in L if not l.startswith(("C ", "CU "))],
        }
        for ci, (nm, fn) in enumerate(corr.items()):
            try:
                t = edit(fn)
            except StopIteration:
                continue
            if t == "\n".join(lines) + "\n":
                continue
            cf = d / f"corrupt_{src}_{ci:02d}.log"
            cf.write_text(t)
            rc, err, _ = compare(ups["long"], cf, mame16, "--r4b-check")
            negctl.append(("s0c", f"--r4b-check must FAIL on the {src} V2 log with: {nm}", rc == 1, err[:110]))
    # upstream effects: at index 7 accepted (the real log), the same effect at index 6 rejected
    up_l = (d / "up_long.log").read_text().splitlines()
    i7 = next(k for k, l in enumerate(up_l) if l.startswith("L 7 "))
    i6 = next(k for k, l in enumerate(up_l) if l.startswith("L 6 "))
    moved = [l for l in up_l[i6 + 1:i7] if l.startswith(("M ", "W "))]
    bad = up_l[:i6] + moved + up_l[i6:i6 + 1] + [l for l in up_l[i6 + 1:i7] if l not in moved] + up_l[i7:]
    (d / "up_effects_at_6.log").write_text("\n".join(bad) + "\n")
    base_v2 = d / "v2.log" if x.phase == "r4b" else d / "v2_r4b_synth.log"
    rc, err, _ = compare(d / "up_effects_at_6.log", base_v2, mame16, "--r4b-check")
    negctl.append(("s0u6", "upstream's index-7 store and M record attributed to index 6 (real upstream text) is REJECTED", rc == 1 and bool(moved), f"{len(moved)} lines moved; {err[:100]}"))
    rc, err, _ = compare(ups["long"], base_v2, mame16, "--r4b-check")
    negctl.append(("s0u7", "control: the same effects at index 7 (the real upstream log) are ACCEPTED", rc == 0, err[:100]))
