#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R3a: the executable S0 criterion s0_compare.py --r3b-check against the real GA2 prefix (docs/r3-movea-plan.md section 6 #4 / 7).

Runs only where the local, git-ignored GA2 inputs exist (build/S0/rom/ga2u_prog.hex, build/S0/boot/boot_head.trace); otherwise
the stage is reported as skipped (never as a pass). Only counts, PCs, one-byte opcodes and register values of the boot prefix
(already in the plan) are printed; no ROM bytes.

  1. upstream S0 PREFIX runs (+maxinstr=5, and a longer one, +maxinstr=200) with +privev +memlog: the reference state at the
     reach point is the REPLAY of the first 4 records, not a final dump (the longer run's final dump differs)
  2. the CURRENT V2 (R2b microcode, this phase): --r2b-check still PASSES (reach 2, TRAP opcode 0010050e) and --r3b-check
     FAILS with exactly the expected reasons (reach 2, not TRAP mode 0010051c): the criterion is not vacuous
  3. a V2-format log synthesised from the real upstream prefix with the planned R3b end (TRAP mode ipc=0010051c) PASSES, and
     each of the corruptions below FAILS
  4. with R3_SIM_R3B=1 (the table-only stub): the real V2 bench on the stub does not retire the MOVEA (STALL): --r3b-check FAILS
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


def compare(up, v2, mame, *flags):
    r = subprocess.run([sys.executable, str(CMP), str(up), str(v2), str(mame), *flags], capture_output=True, text=True)
    return r.returncode, r.stderr.strip(), r.stdout


# The memory-bench records of the synthesised planned-R3b log are CONSTANTS modelled on a fetch-only prefix run of the real bench
# (+drainrec): reads of ROM and its mirror only, one instruction prefetch still in flight (MEMDRAIN outstanding=1, owner=2), no
# data-port transaction, the trap sampled with an idle data port, +drain=300 settle. They are not taken from whichever V2 happens to
# be installed (the table-only stub STALLs and never samples a trap).
SYNTH_MEMREC = ["DRAIN data_busy=0 d_active=0 owner=2 d_starts=0 d_acks=0 d_wr_acks=0 pf_acks=17 at_trap_data_busy=0 at_trap_d_starts=0 at_trap_d_acks=0 settle=312",
                "MEMSTAT read  rom            24", "MEMSTAT read  rom-mirror     6", "MEMSTAT rom_writes_ignored 0",
                "MEMDRAIN reqs=30 acks=29 rd_reqs=30 wr_reqs=0 wr_acks=0 outstanding=1 outstanding_wr=0 nonrom_reqs=0 m_req=1 busy=1"]


def synth_v2(up_text):
    """a V2-format log for the planned R3b end, built from the REAL upstream prefix: the first 4 lock-step records, their
    privileged events (V2 adds be=f), the GPR events (+gprev) and the per-instruction counters (+counters) derived from those
    events, a trap `mode` at the third GA2 MOVEA with its (empty) in-flight CU record, the final GPR / PSW dump, V2's privileged
    dump (zero + the prefix's privileged writes: V2's privileged file has no reset values), END and SIMCHECK, and (Codex r2) the memory
    bench's own records MEMSTAT / MEMDRAIN / DRAIN (SYNTH_MEMREC above)"""
    out = ["GPREV on"]
    regs, psw = [0] * 32, 0x10000000
    pv = {i: 0 for i in range(32)}
    nP, nG = {}, {}
    for line in up_text.splitlines():
        m = re.match(r"^P (\d+) id=(\d+) val=([0-9a-f]+)", line)
        if m and int(m.group(1)) < 4:
            out.append(f"P {m.group(1)} id={m.group(2)} be=f val={m.group(3)}")
            pv[int(m.group(2))] = int(m.group(3), 16)
            nP[int(m.group(1))] = nP.get(int(m.group(1)), 0) + 1
        m = re.match(r"^L (\d+) pc=(\w+) op=(\w+)(.*)$", line)
        if m and int(m.group(1)) < 4:
            n = int(m.group(1))
            for k, v in re.findall(r"(R\d\d|PSW)=([0-9a-f]+)", m.group(4)):
                if k == "PSW":
                    psw = int(v, 16)
                else:
                    regs[int(k[1:])] = int(v, 16)
                    out.append(f"G {n} id={int(k[1:])} be=f val={v}")
                    nG[n] = nG.get(n, 0) + 1
            out.append(line)
            out.append(f"C {n} dr=0 dw=0 priv={nP.get(n, 0)} psw=0 gpr={nG.get(n, 0)}")
    # the G lines are printed before their L line, as the observer prints them (the checkers verify this order since R3a fixes 3)
    out += [f"R{i:02d}={regs[i]:08x}" for i in range(32)]
    out.append(f"PSW={psw:08x}")
    out += [f"PR id={i} val={pv[i]:08x}" for i in range(32)]
    out += ["CU 4 dr=0 dw=0 priv=0 psw=0 gpr=0"] + [SYNTH_MEMREC[0]] + [
            "TRAP mode ipc=0010051c", "END halted=0 instrs=4 cycles=0", "COUNTERS pre=0 exec=0 mem=0 fetch=0 iter=0", "SIMCHECK errors=0 ipc_errors=0"]
    out += SYNTH_MEMREC[1:]
    return "\n".join(out) + "\n"


def run(x, negctl, note):
    root = x.root
    rom = root / "build/S0/rom/ga2u_prog.hex"
    trace = root / "build/S0/boot/boot_head.trace"
    print("== S0 --r3b-check against the real GA2 prefix")
    if not (rom.exists() and trace.exists()):
        print("   SKIPPED: build/S0/rom/ga2u_prog.hex or build/S0/boot/boot_head.trace is absent (local, git-ignored inputs)")
        x.s0_skipped = True
        return
    d = x.out / "s0"
    d.mkdir(exist_ok=True)
    mame = d / "mame_head.trace"
    mame.write_text("".join(list(open(trace, errors="replace"))[:12]))          # the first 12 trace lines: PCs and mnemonics only
    sup = str(x.out / "obj_s0up/sim")
    (d / "up_prefix.log").write_text(RR.sh([sup, f"+rom={rom}", "+maxinstr=5", "+counters", "+privev", "+memlog"]).stdout)
    (d / "up_mid.log").write_text(RR.sh([sup, f"+rom={rom}", "+maxinstr=200", "+counters", "+privev", "+memlog"]).stdout)
    mc = x.out / "mc.s0"
    syms = RR.symbols(mc)
    v2cmd = RR.v2_cmd(str(x.out / "obj_s0v2/sim"), mc, [f"+rom={rom}", "+maxinstr=1000", "+maxcyc=2000000", "+stallcyc=20000", "+privev", "+gprev", "+drainrec"], syms)
    (d / "v2.log").write_text(RR.sh(v2cmd).stdout)
    up_t = (d / "up_prefix.log").read_text()
    mid_t = (d / "up_mid.log").read_text()
    recs = len(re.findall(r"^L \d+ ", mid_t, re.M))
    # 1. the upstream reference state is the replay, and the longer run's final dump is different (so a dump-based check would be wrong)
    m_ = re.search(r"^R31=([0-9a-f]{8})$", mid_t, re.M)
    dump31 = int(m_.group(1), 16) if m_ else None
    m10 = re.search(r"^R10=([0-9a-f]{8})$", up_t, re.M)
    past = bool(m10) and int(m10.group(1), 16) != 0                      # the +maxinstr=5 run already executed index 4 (R10 <- 0010058c)
    negctl.append(("s0a", "upstream reference state: replay of the first 4 records (R31 = 00210000, R25 = 00208000, PSW 10000000, SBR 00100000). No final dump is used: the "
                          "+maxinstr=5 run's dump is already PAST the trap point (R10 set) and the +maxinstr=200 run's final R31 differs",
                   past and dump31 not in (None, 0x00210000),
                   f"{recs} records in the +maxinstr=200 run; its final R31 = {dump31:08x}; prefix run final R10 set: {past}" if dump31 is not None else "no dump"))
    # 2. the current V2
    v2_t = (d / "v2.log").read_text()
    kind = re.search(r"^(TRAP \S+ ipc=\w+|STALL .*)$", v2_t, re.M)
    rc2, err2, _ = compare(d / "up_prefix.log", d / "v2.log", mame, "--r2b-check")
    if x.sim_stub:
        negctl.append(("s0b", "stub (table-only, inert self-loop routine) on the real S0 bench: --r3b-check FAILS", compare(d / "up_prefix.log", d / "v2.log", mame, "--r3b-check")[0] == 1,
                       f"V2 ended: {kind.group(1) if kind else 'no TRAP/STALL'}"))
    elif x.phase == "r3b":
        # R3b (real builder, MOVEA microcode installed): the three pre-R3b controls flip, as the plan's S0 criterion requires
        negctl.append(("s0b0", "R3b: the V2 with the MOVEA routine FAILS --r2b-check on the real S0 prefix, because it reached 4, no longer 2 (the R2b criterion is superseded)",
                       rc2 == 1 and "reach 4 != 2" in err2, f"{err2[:90]}; V2 ended {kind.group(1) if kind else '?'}"))
        rc3, err3, out3 = compare(d / "up_prefix.log", d / "v2.log", mame, "--r3b-check")
        negctl.append(("s0b", "R3b: the REAL V2 with the MOVEA routine PASSES --r3b-check on the real S0 prefix (reach 4, TRAP mode ipc=0010051c, R31/R25/SBR/PSW, "
                              "counters, G events, memory bench, final dumps)", rc3 == 0 and "PASS" in err3 and kind is not None and kind.group(1) == "TRAP mode ipc=0010051c",
                       f"{err3[:120]}; V2 ended {kind.group(1) if kind else '?'}"))
        negctl.append(("s0b1", "R3b: the REAL V2's memory-bench records (MEMSTAT reads of ROM and its mirror only, MEMDRAIN with one instruction prefetch in flight, DRAIN "
                               "with no data-port transaction) raise NO memory or drain failure in --r3b-check",
                       rc3 == 0 and "MEMDRAIN" in v2_t and "DRAIN data_busy=0" in v2_t,
                       f"real records: {[l for l in v2_t.splitlines() if l.startswith(('MEMSTAT', 'MEMDRAIN', 'DRAIN '))]}"))
    else:
        negctl.append(("s0b0", "control: the CURRENT V2 on the real S0 prefix passes --r2b-check (reach 2, TRAP opcode at 0010050e)", rc2 == 0 and "PASS" in err2, f"{err2[:90]}; V2 ended {kind.group(1) if kind else '?'}"))
        rc3, err3, _ = compare(d / "up_prefix.log", d / "v2.log", mame, "--r3b-check")
        negctl.append(("s0b", "the CURRENT V2 (pre-R3b) FAILS --r3b-check, for exactly the expected reasons (reach 2 != 4; not TRAP mode at 0010051c)",
                       rc3 == 1 and "reach 2 != 4" in err3 and "TRAP mode at 0010051c" in err3, err3[:200]))
        negctl.append(("s0b1", "the CURRENT V2's REAL memory-bench records (MEMSTAT reads of ROM and its mirror only, MEMDRAIN with one instruction prefetch in flight, DRAIN "
                               "with no data-port transaction) raise NO memory or drain failure in --r3b-check: legitimate instruction fetches are accepted",
                       rc3 == 1 and not any(k in err3 for k in ("MEMSTAT", "MEMDRAIN", "DRAIN", "memory-access")) and "MEMDRAIN" in v2_t and "DRAIN data_busy=0" in v2_t,
                       f"real records: {[l for l in v2_t.splitlines() if l.startswith(('MEMSTAT', 'MEMDRAIN', 'DRAIN '))]}"))
    # 3. the synthesised planned-R3b log passes, with the short and with the longer upstream run; corruptions fail
    synth = synth_v2(up_t)
    (d / "v2_r3b.log").write_text(synth)
    for tag, upf in (("prefix run", d / "up_prefix.log"), ("longer run (+maxinstr=200)", d / "up_mid.log")):
        rc, err, out = compare(upf, d / "v2_r3b.log", mame, "--r3b-check")
        res = json.loads(out)["r3b_check"] if out.strip().startswith("{") else {}
        negctl.append(("s0c0", f"control: the planned R3b end (V2 synthesised from the real upstream prefix) PASSES --r3b-check with upstream's {tag}",
                       rc == 0 and "PASS" in err, f"{err[:60]}; state source: {res.get('state_source')}; upstream long-run log: {res.get('upstream_log_is_long_run')}"))
    lines = synth.splitlines()

    def edit(fn):
        return "\n".join(fn(list(lines))) + "\n"

    def ins_before(L, prefix, *new_lines):
        i = next(k for k, l in enumerate(L) if l.startswith(prefix))
        return L[:i] + list(new_lines) + L[i:]

    def sub(pat, rep, once_on=None):
        def fn(L):
            return [re.sub(pat, rep, l) if (once_on is None or l.startswith(once_on)) else l for l in L]
        return fn
    def swap_l01(L):
        i0 = next(k for k, l in enumerate(L) if l.startswith("L 0 "))
        i1 = next(k for k, l in enumerate(L) if l.startswith("L 1 "))
        L[i0], L[i1] = L[i1], L[i0]
        return L
    def swap_g23(L):
        i2 = next(k for k, l in enumerate(L) if l.startswith("G 2 "))
        i3 = next(k for k, l in enumerate(L) if l.startswith("G 3 "))
        L[i2], L[i3] = L[i3], L[i2]
        return L
    corr = {
        "TRAP kind opcode (the current V2, mis-predicted)": sub(r"TRAP mode", "TRAP opcode", "TRAP"),
        "TRAP at 0010050e (the first MOVEA, the pre-R3b end)": sub(r"ipc=0010051c", "ipc=0010050e", "TRAP"),
        "no trap, the run just ends (limit)": lambda L: [l for l in L if not l.startswith("TRAP")],
        "record 3 missing (reach 3)": lambda L: [l for l in L if not l.startswith("L 3 ") and not l.startswith("G 3 ")],
        "R31 wrong in record 2": sub(r"R31=00210000", "R31=00210001", "L 2 "),
        "R25 wrong in record 3": sub(r"R25=00208000", "R25=00208001", "L 3 "),
        "another register written by record 3 (R10)": sub(r"(R25=00208000)", r"\1 R10=0010058c", "L 3 "),
        "PSW changed by record 2": sub(r"(R31=00210000)", r"\1 PSW=10000001", "L 2 "),
        "an extra privileged write by record 2": lambda L: ins_before(L, "L 2 ", "P 2 id=7 be=f val=00000070"),
        "the SBR write missing": lambda L: [l for l in L if not l.startswith("P 1 ")],
        "SBR wrong in the privileged dump": sub(r"PR id=5 val=00100000", "PR id=5 val=00100001", "PR id=5"),
        "GPR event value wrong (+gprev)": sub(r"val=00210000", "val=00210001", "G 2 "),
        "a stray GPR event of the trapping instruction": lambda L: ins_before(L, "TRAP", "G 4 id=10 be=f val=0010058c"),
        "final dump R25 wrong": sub(r"R25=00208000", "R25=00000000", "R25="),
        "final dump PSW wrong": sub(r"PSW=10000000", "PSW=10000001", "PSW="),
        "V2 STALL instead of the trap (the stub's behaviour)": lambda L: [("STALL ipc=0010050e upc=300 retired=2" if l.startswith("TRAP") else l) for l in L],
        # Codex r1 blocker 1 (R3a fix): each reproduction inserted before the trap on the passing log, plus the other named cases
        "a memory write before the trap (W addr=00200000 size=4 data=deadbeef)": lambda L: ins_before(L, "TRAP", "W addr=00200000 size=4 data=deadbeef"),
        "CU 4 dr=1 dw=0 priv=0 psw=0 gpr=0 before the trap": lambda L: ins_before(L, "TRAP", "CU 4 dr=1 dw=0 priv=0 psw=0 gpr=0"),
        "CU 4 dr=0 dw=0 priv=0 psw=2 gpr=0 before the trap": lambda L: ins_before(L, "TRAP", "CU 4 dr=0 dw=0 priv=0 psw=2 gpr=0"),
        "corrupted final privileged dump PR id=7": sub(r"PR id=7 val=00000000", "PR id=7 val=00000070", "PR id=7 "),
        "GPR instrumentation removed (no GPREV marker, no G events)": lambda L: [l for l in L if not l.startswith(("GPREV", "G "))],
        "changed GPR byte enables (G 2 be=3)": sub(r"be=f", "be=3", "G 2 "),
        "simulation-check errors set (SIMCHECK errors=1 ipc_errors=1)": sub(r"errors=0 ipc_errors=0", "errors=1 ipc_errors=1", "SIMCHECK"),
        "counters removed (no C / CU records)": lambda L: [l for l in L if not l.startswith(("C ", "CU "))],
        # Codex r2 reproductions, exactly as written (blocker: memory-bench evidence; should-fix: duplicate fields, retirement order)
        "M R io addr=c0000a data=0000 be=11 before the trap (Codex r2)": lambda L: ins_before(L, "TRAP", "M R io addr=c0000a data=0000 be=11"),
        "MEMSTAT write wram 1 before the trap (Codex r2)": lambda L: ins_before(L, "TRAP", "MEMSTAT write wram 1"),
        "CU 4 dr=1 dr=0 dw=0 priv=0 psw=0 gpr=0 (duplicate field, Codex r2)": lambda L: [("CU 4 dr=1 dr=0 dw=0 priv=0 psw=0 gpr=0" if l.startswith("CU 4 ") else l) for l in L],
        "CU 4 dr=0 dw=0 priv=0 psw=2 psw=0 gpr=0 (duplicate field, Codex r2)": lambda L: [("CU 4 dr=0 dw=0 priv=0 psw=2 psw=0 gpr=0" if l.startswith("CU 4 ") else l) for l in L],
        "the L 0 and L 1 lines swapped, ids retained (Codex r2)": lambda L: swap_l01(L),
        # Codex r3 reproductions (R3a fixes 3), on the passing log: the review's exact lines
        "val=deadbeef appended to the P 1 record (Codex r3)": sub(r"$", " val=deadbeef", "P 1 "),
        "val=deadbeef appended to the PR id=5 record (Codex r3)": sub(r"$", " val=deadbeef", "PR id=5 "),
        "M R io addr=c0000a data=ZZZZ be=11 appended (Codex r3)": lambda L: L + ["M R io addr=c0000a data=ZZZZ be=11"],
        "C 4 dr=1 dr=0 dw=0 priv=0 psw=0 gpr=0 extra appended (Codex r3)": lambda L: L + ["C 4 dr=1 dr=0 dw=0 priv=0 psw=0 gpr=0 extra"],
        "SIMCHECK nonsense appended (Codex r3)": lambda L: L + ["SIMCHECK nonsense"],
        "the G 2 and G 3 records reversed (cross-instruction G order, Codex r3 note)": lambda L: swap_g23(L),
        "an incorrect TRAP followed by the expected TRAP": lambda L: ins_before(L, "TRAP", "TRAP opcode ipc=0010050e"),
        "SIMCHECK errors=7 ipc_errors=2 followed by SIMCHECK errors=0 ipc_errors=0": lambda L: ins_before(L, "SIMCHECK", "SIMCHECK errors=7 ipc_errors=2"),
        # Codex r4 reproductions (R3a fixes 4), on the passing log: the review's exact lines, appended
        "MR io addr=c0000a data=0000 be=11 appended (Codex r4)": lambda L: L + ["MR io addr=c0000a data=0000 be=11"],
        "Waddr=00200000 size=4 data=00000001 appended (Codex r4)": lambda L: L + ["Waddr=00200000 size=4 data=00000001"],
        "FATAL: test failure appended (Codex r4)": lambda L: L + ["FATAL: test failure"],
        "DRAIN extra=1 appended (Codex r4)": lambda L: L + ["DRAIN extra=1"],
        "MEMDRAIN reqs=0 extra=1 appended (Codex r4)": lambda L: L + ["MEMDRAIN reqs=0 extra=1"],
        "an empty line appended (blank lines never occur in a real S0 log)": lambda L: L + [""],
        "a lower-case trap record appended": lambda L: L + ["trap mode ipc=0010051c"],
        "a record with a tab separator appended": lambda L: L + ["TRAP\tmode ipc=00000099"],
        "MEMSTAT read wram 1 (a data read of work RAM)": lambda L: ins_before(L, "MEMSTAT rom_writes", "MEMSTAT read  wram           1"),
        "MEMSTAT write io 3": lambda L: ins_before(L, "MEMSTAT rom_writes", "MEMSTAT write io             3"),
        "the MEMDRAIN record removed (no +drainrec)": lambda L: [l for l in L if not l.startswith("MEMDRAIN")],
        "the DRAIN record removed (no +drainrec)": lambda L: [l for l in L if not l.startswith("DRAIN ")],
        "DRAIN data_busy=1 (data port busy at the end)": sub(r"data_busy=0 d_active", "data_busy=1 d_active", "DRAIN "),
        "DRAIN d_starts=1 (a data-port transaction)": sub(r"d_starts=0", "d_starts=1", "DRAIN "),
        "MEMDRAIN wr_reqs=1 outstanding_wr=1 (an unacknowledged write request)": sub(r"wr_reqs=0 wr_acks=0 outstanding=(\d+) outstanding_wr=0", r"wr_reqs=1 wr_acks=0 outstanding=\1 outstanding_wr=1", "MEMDRAIN"),
    }
    # the Codex r2 cases name the message their rejection must contain (the FULL stderr is searched), so each is shown to fail for
    # the INTENDED reason and not through some side effect of the edit
    intended = {
        "M R io addr=c0000a data=0000 be=11 before the trap (Codex r2)": "memory-access record(s) outside program ROM",
        "MEMSTAT write wram 1 before the trap (Codex r2)": "V2 MEMSTAT: memory writes [('wram', 1)]",
        "CU 4 dr=1 dr=0 dw=0 priv=0 psw=0 gpr=0 (duplicate field, Codex r2)": "CU 4 has duplicate field(s) ['dr']",
        "CU 4 dr=0 dw=0 priv=0 psw=2 psw=0 gpr=0 (duplicate field, Codex r2)": "CU 4 has duplicate field(s) ['psw']",
        "the L 0 and L 1 lines swapped, ids retained (Codex r2)": "V2 log: lock-step record L 1 out of order",
        "MEMSTAT read wram 1 (a data read of work RAM)": "reads outside instruction fetch",
        "MEMSTAT write io 3": "V2 MEMSTAT: memory writes [('io', 3)]",
        "the MEMDRAIN record removed (no +drainrec)": "no MEMDRAIN record",
        "the DRAIN record removed (no +drainrec)": "no DRAIN record",
        "DRAIN data_busy=1 (data port busy at the end)": "data port not idle at the end",
        "DRAIN d_starts=1 (a data-port transaction)": "data-port transactions started 1",
        "MEMDRAIN wr_reqs=1 outstanding_wr=1 (an unacknowledged write request)": "write requests 1, acknowledged 0, outstanding 1",
        "MR io addr=c0000a data=0000 be=11 appended (Codex r4)": "V2 log: unrecognised line",
        "Waddr=00200000 size=4 data=00000001 appended (Codex r4)": "V2 log: unrecognised line",
        "FATAL: test failure appended (Codex r4)": "V2 log: unrecognised line",
        "DRAIN extra=1 appended (Codex r4)": "V2 log: malformed DRAIN record",
        "MEMDRAIN reqs=0 extra=1 appended (Codex r4)": "V2 log: malformed MEMDRAIN record",
        "an empty line appended (blank lines never occur in a real S0 log)": "V2 log: blank line",
        "a lower-case trap record appended": "V2 log: unrecognised line",
        "a record with a tab separator appended": "V2 log: control or non-ASCII character",
        "val=deadbeef appended to the P 1 record (Codex r3)": "V2 log: malformed privileged-write record",
        "val=deadbeef appended to the PR id=5 record (Codex r3)": "V2 log: malformed privileged dump record",
        "M R io addr=c0000a data=ZZZZ be=11 appended (Codex r3)": "V2 log: malformed memory-access record",
        "C 4 dr=1 dr=0 dw=0 priv=0 psw=0 gpr=0 extra appended (Codex r3)": "V2 log: malformed C record",
        "SIMCHECK nonsense appended (Codex r3)": "V2 log: malformed SIMCHECK record",
        "the G 2 and G 3 records reversed (cross-instruction G order, Codex r3 note)": "GPR event of instruction 2 after one of instruction 3",
        "an incorrect TRAP followed by the expected TRAP": "2 TRAP record",
        "SIMCHECK errors=7 ipc_errors=2 followed by SIMCHECK errors=0 ipc_errors=0": "duplicate SIMCHECK line",
    }
    for what, fn in corr.items():
        (d / "v2_bad.log").write_text(edit(fn))
        rc, err, _ = compare(d / "up_prefix.log", d / "v2_bad.log", mame, "--r3b-check")
        need = intended.get(what)
        negctl.append(("s0c", f"--r3b-check must FAIL when V2's log has: {what}" + (f" [reason must contain: {need}]" if need else ""),
                       rc == 1 and (need is None or need in err), err[:150]))
    # the swapped retirement lines in BOTH logs (so the two logs still agree with each other and reach == 4): only the order check can reject
    ul0 = up_t.splitlines()
    (d / "up_swap.log").write_text("\n".join(swap_l01(list(ul0))) + "\n")
    (d / "v2_swap.log").write_text(edit(lambda L: swap_l01(L)))
    rc, err, _ = compare(d / "up_swap.log", d / "v2_swap.log", mame, "--r3b-check")
    negctl.append(("s0c", "--r3b-check must FAIL when the L 0 and L 1 lines are swapped (ids retained) in BOTH logs, so they still agree with each other [reason must contain: out of order]",
                   rc == 1 and "lock-step record L 1 out of order" in err, err[:150]))
    # upstream-side corruptions
    ul = up_t.splitlines()
    ucorr = {
        "upstream record 2 with another R31 value": lambda L: [re.sub(r"R31=00210000", "R31=00210002", l) if l.startswith("L 2 ") else l for l in L],
        "upstream with a data write in the prefix": lambda L: ["W addr=00200000 size=4 data=00000001"] + L,
        "upstream with a non-ROM access in the prefix (+memlog)": lambda L: ["M R io addr=c0000a data=0000 be=11"] + L,
        "upstream prefix log with MEMSTAT write wram 1": lambda L: [l for l in L if not l.startswith("MEMSTAT rom_writes")] + ["MEMSTAT write wram 1", "MEMSTAT rom_writes_ignored 0"],
        "upstream prefix log with MEMSTAT read io 2": lambda L: [l for l in L if not l.startswith("MEMSTAT rom_writes")] + ["MEMSTAT read  io             2", "MEMSTAT rom_writes_ignored 0"],
        "upstream prefix log without the MEMSTAT block": lambda L: [l for l in L if not l.startswith("MEMSTAT")],
        "upstream with the L 0 and L 1 lines swapped, ids retained": lambda L: swap_l01(list(L)),
        "upstream P 1 record with val=deadbeef appended (Codex r3)": lambda L: [(l + " val=deadbeef" if l.startswith("P 1 ") else l) for l in L],
        "upstream PR id=5 record with val=deadbeef appended (Codex r3)": lambda L: [(l + " val=deadbeef" if l.startswith("PR id=5 ") else l) for l in L],
        "upstream with M R io addr=c0000a data=ZZZZ be=11 appended (Codex r3)": lambda L: L + ["M R io addr=c0000a data=ZZZZ be=11"],
        "upstream with SIMCHECK nonsense appended (Codex r3)": lambda L: L + ["SIMCHECK nonsense"],
        "upstream with MR io addr=c0000a data=0000 be=11 appended (Codex r4)": lambda L: L + ["MR io addr=c0000a data=0000 be=11"],
        "upstream with Waddr=00200000 size=4 data=00000001 appended (Codex r4)": lambda L: L + ["Waddr=00200000 size=4 data=00000001"],
        "upstream with FATAL: test failure appended (Codex r4)": lambda L: L + ["FATAL: test failure"],
        "upstream with DRAIN extra=1 appended (Codex r4)": lambda L: L + ["DRAIN extra=1"],
        "upstream with MEMDRAIN reqs=0 extra=1 appended (Codex r4)": lambda L: L + ["MEMDRAIN reqs=0 extra=1"],
        "upstream with an empty line appended": lambda L: L + [""],
        "upstream counter record with a duplicate field (C 1 dr=0 dr=0 dw=0 priv=1)": lambda L: [("C 1 dr=0 dr=0 dw=0 priv=1" if l.startswith("C 1 ") else l) for l in L],
    }
    u_intended = {
        "upstream prefix log with MEMSTAT write wram 1": "upstream MEMSTAT: memory writes",
        "upstream prefix log with MEMSTAT read io 2": "upstream MEMSTAT: reads outside instruction fetch",
        "upstream prefix log without the MEMSTAT block": "upstream log has no MEMSTAT block",
        "upstream with the L 0 and L 1 lines swapped, ids retained": "upstream log: lock-step record L 1 out of order",
        "upstream with MR io addr=c0000a data=0000 be=11 appended (Codex r4)": "upstream log: unrecognised line",
        "upstream with Waddr=00200000 size=4 data=00000001 appended (Codex r4)": "upstream log: unrecognised line",
        "upstream with FATAL: test failure appended (Codex r4)": "upstream log: unrecognised line",
        "upstream with DRAIN extra=1 appended (Codex r4)": "upstream log: malformed DRAIN record",
        "upstream with MEMDRAIN reqs=0 extra=1 appended (Codex r4)": "upstream log: malformed MEMDRAIN record",
        "upstream with an empty line appended": "upstream log: blank line",
        "upstream counter record with a duplicate field (C 1 dr=0 dr=0 dw=0 priv=1)": "upstream log: C 1 has duplicate field(s) ['dr']",
        "upstream P 1 record with val=deadbeef appended (Codex r3)": "upstream log: malformed privileged-write record",
        "upstream PR id=5 record with val=deadbeef appended (Codex r3)": "upstream log: malformed privileged dump record",
        "upstream with M R io addr=c0000a data=ZZZZ be=11 appended (Codex r3)": "upstream log: malformed memory-access record",
        "upstream with SIMCHECK nonsense appended (Codex r3)": "upstream log: malformed SIMCHECK record",
    }
    for what, fn in ucorr.items():
        (d / "up_bad.log").write_text("\n".join(fn(ul)) + "\n")
        rc, err, _ = compare(d / "up_bad.log", d / "v2_r3b.log", mame, "--r3b-check")
        need = u_intended.get(what)
        negctl.append(("s0c", f"--r3b-check must FAIL when upstream's log has: {what}" + (f" [reason must contain: {need}]" if need else ""),
                       rc == 1 and (need is None or need in err), err[:150]))
    # logs without privileged records
    (d / "up_nopriv.log").write_text("\n".join(l for l in ul if not l.startswith(("P ", "PR "))) + "\n")
    rc, err, _ = compare(d / "up_nopriv.log", d / "v2_r3b.log", mame, "--r3b-check")
    negctl.append(("s0c", "--r3b-check must FAIL when upstream's log has no P/PR records (run without +privev)", rc == 1, err[:120]))
    # default mode and --r2b-check are unchanged by the new option: the archived R2b logs still pass --r2b-check
    arch = root / "build/M1/r2b/s0"
    if (arch / "up2.log").exists() and (arch / "v2.log").exists():
        rc, err, _ = compare(arch / "up2.log", arch / "v2.log", trace, "--r2b-check")
        negctl.append(("s0d0", "control: the archived R2b S0 logs still pass --r2b-check (the new option changed nothing)", rc == 0, err[:60]))
        rc, err, _ = compare(arch / "up2.log", arch / "v2.log", trace, "--r3b-check")
        negctl.append(("s0d", "the archived R2b S0 logs (reach 2) FAIL --r3b-check", rc == 1 and "reach 2 != 4" in err, err[:120]))
