#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""R3a default-output identity: the V2 benches rebuilt with the R3a observer (+gprev added, default off) must print
NORMALISED-IDENTICAL output to the benches built from the pre-R3a observer on every existing program, with the same microcode
and default plusargs. "Normalised" is literal: run() removes every line starting with "- " (the Verilator banner / $finish /
walltime lines, which carry run-time-dependent numbers) and splits and re-joins lines (line endings normalised); everything
else, including the END, SIMCHECK, MEMSTAT and counter lines, is compared exactly. A pair counts as identical ONLY IF both
simulator runs exit 0 and are complete AND successful: exactly one END record (halted, instrs and cycles all parsed) and exactly
one SIMCHECK summary (errors and ipc_errors parsed) with BOTH counts zero (completion_problem); a non-zero exit, a timeout, an
incomplete, malformed or duplicated record or a run with simulation-check errors is a separate FAILED-RUN count and makes the
script fail. Covers the R1a / R2a / R3a program sets, the M1.1 / M1.2 suites, 4 fetch configurations with +lockstep (the
plusargs of the earlier identity checks) and 2 with +counters +privev +drain (the printers this change touches).

  m1_r3a_identity.py <pre-bench-run-dir> <new-bench-run-dir> <r3a-run-dir>
      <pre/new run dir>: contain obj_v2/sim, obj_s0v2/sim; the microcode is taken from <r3a-run-dir>/mc, mc.s0 (production, S0)
"""
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path.home() / "ga2-pocket-research"


def sym(mc):
    return dict(l.split("=") for l in (mc / "m1_symbols.txt").read_text().split())


END_RE = re.compile(r"^END halted=([01]) instrs=(\d+) cycles=(\d+)(?: ce=(\d+))?$")
SIMCHECK_RE = re.compile(r"^SIMCHECK errors=(\d+) ipc_errors=(\d+)$")


def completion_problem(returncode, text):
    """None if the run COMPLETED SUCCESSFULLY, else why not (R3a fixes 2, Codex r2 should-fix 3). Presence of a record is not
    enough: exactly one END record whose fields all parse (halted, instrs, cycles), exactly one SIMCHECK summary whose fields
    parse, and BOTH simulation-check counts zero. A bare `END `, `SIMCHECK errors=` with no numbers, a duplicate or a malformed
    record, `SIMCHECK errors=7 ipc_errors=2` and any other line that begins END / SIMCHECK without being one of these are problems."""
    if returncode != 0:
        return f"exit status {returncode}"
    lines = text.splitlines()
    ends = [l for l in lines if l.startswith("END")]
    scs = [l for l in lines if l.startswith("SIMCHECK")]
    if not ends:
        return "no END record (incomplete run)"
    if len(ends) > 1:
        return f"{len(ends)} END records"
    if not END_RE.match(ends[0]):
        return f"malformed END record {ends[0][:60]!r}"
    summaries = [l for l in scs if l.startswith("SIMCHECK errors=")]
    if not summaries:
        return "no SIMCHECK line (incomplete run)" if not scs else f"simulation checks reported violations without a summary: {scs[0][:60]!r}"
    if len(summaries) > 1:
        return f"{len(summaries)} SIMCHECK summary records"
    m = SIMCHECK_RE.match(summaries[0])
    if not m:
        return f"malformed SIMCHECK record {summaries[0][:60]!r}"
    if (int(m.group(1)), int(m.group(2))) != (0, 0):
        return f"simulation checks reported errors={m.group(1)} ipc_errors={m.group(2)}"
    if len(scs) != 1:
        return f"{len(scs) - 1} further SIMCHECK line(s) besides the summary"
    return None


def run(sim, mc, args):
    """-> (normalised output, problem or None). problem: non-zero exit, timeout, or an END / SIMCHECK record that is missing,
    duplicated, malformed or reports simulation-check errors (completion_problem)"""
    s = sym(mc)
    a = [str(sim)] + args + [f"+mc={mc}", f"+haltpc={s['halt']}", f"+trappc={s['trap']}", f"+trapmodepc={s['trap_mode']}", f"+trapimmpc={s['trap_immdest']}"]
    try:
        o = subprocess.run(a, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return "", "timeout"
    text = "\n".join(l for l in (o.stdout + o.stderr).splitlines() if not l.startswith("- "))
    return text, completion_problem(o.returncode, text)


def main():
    pre, new, r3 = (Path(a) for a in sys.argv[1:4])
    cfgs = ["", "+memwait=2", "+memwait=5 +memjit=4", "+wide"]
    setA = ["+lockstep"]
    setB = ["+lockstep", "+counters", "+privev", "+drain=300"]
    jobs = []
    roots = [(r3 / "progs", "r3a"), (ROOT / "build/M1/r2b/run/progs", "r2a"), (ROOT / "build/M1/r1b/run/progs", "r1"),
             (ROOT / "build/M1/r1b/reg/m11/progs", "m11"), (ROOT / "build/M1/r1b/reg/m12/progs", "m12")]
    for d, tag in roots:
        if not d.exists():
            continue
        names = [f.stem for f in sorted(d.glob("*.hex")) if not f.name.endswith(".rom.hex") and f.with_suffix(".bin").exists()]
        s0names = [f.name[:-8] for f in sorted(d.glob("*.rom.hex"))]
        for n in names:
            for cfg in cfgs:
                jobs.append((f"{tag}:{n}", "obj_v2", "mc", [f"+img={d}/{n}.hex", "+maxcyc=400000"] + cfg.split() + setA))
            for cfg in ("", "+wide"):
                jobs.append((f"{tag}:{n}", "obj_v2", "mc", [f"+img={d}/{n}.hex", "+maxcyc=400000"] + cfg.split() + setB))
        for n in s0names:
            for cfg in cfgs:
                jobs.append((f"{tag}:{n}[s0]", "obj_s0v2", "mc.s0", [f"+rom={d}/{n}.rom.hex", "+maxinstr=1000", "+maxcyc=400000"] + cfg.split() + setA))
            jobs.append((f"{tag}:{n}[s0]", "obj_s0v2", "mc.s0", [f"+rom={d}/{n}.rom.hex", "+maxinstr=1000", "+maxcyc=400000"] + setB))

    def one(j):
        name, obj, mc, args = j
        mcd = r3 / mc
        (o0, p0), (o1, p1) = run(pre / obj / "sim", mcd, args), run(new / obj / "sim", mcd, args)
        return j, o0 == o1, len(o0), (p0, p1)
    nd, nf, nbytes, nid = 0, 0, 0, 0
    diffs, fails = [], []
    with ThreadPoolExecutor(4) as ex:
        for j, same, nb, probs in ex.map(one, jobs):
            tag = j[0] + " " + " ".join(j[3][1:])
            if any(probs):
                nf += 1
                fails.append(f"{tag}: pre={probs[0]} new={probs[1]}")
                continue
            nbytes += nb
            if same:
                nid += 1
            else:
                nd += 1
                diffs.append(tag)
    print(f"IDENTITY (pre-R3a observer vs R3a observer, same microcode, default plusargs; logs NORMALISED: '- ' lines removed, line endings normalised): "
          f"{len(jobs)} log pairs, {nid} normalised-identical (both runs exit 0 and complete), {nd} differ, {nf} failed/incomplete runs; "
          f"{nbytes} normalised bytes compared in the complete pairs")
    for d in diffs[:10]:
        print("  differs:", d)
    for d in fails[:10]:
        print("  FAILED RUN:", d)
    # R3a fixes 2 (Codex r2 blocker): the new opt-in S0 records (+drainrec: MEMDRAIN from s0_mem, DRAIN from tb_s0_v2) are the ONLY
    # difference they make: on every S0 program and fetch configuration, the new bench's +drainrec output with exactly those two
    # record kinds removed equals its own default output (and the default output is the pre-bench output compared above)
    dr_jobs = [j for j in jobs if j[1] == "obj_s0v2"]

    def additive(j):
        name, obj, mc, args = j
        mcd = r3 / mc
        base, p0 = run(new / obj / "sim", mcd, args)
        withd, p1 = run(new / obj / "sim", mcd, args + ["+drainrec"])
        recs = [l for l in withd.splitlines() if l.startswith(("DRAIN ", "MEMDRAIN "))]
        rest = "\n".join(l for l in withd.splitlines() if not l.startswith(("DRAIN ", "MEMDRAIN ")))
        return j, (p0, p1), rest == base, len(recs)
    a_ok = a_bad = a_fail = a_recs = 0
    a_bads = []
    with ThreadPoolExecutor(4) as ex:
        for j, probs, same, nrec in ex.map(additive, dr_jobs):
            if any(probs):
                a_fail += 1
                a_bads.append(f"{j[0]} {' '.join(j[3][1:])}: {probs}")
            elif same and nrec == 2:
                a_ok += 1
                a_recs += nrec
            else:
                a_bad += 1
                a_bads.append(f"{j[0]} {' '.join(j[3][1:])}: records added {nrec}, rest identical {same}")
    print(f"DRAINREC ADDITIVE (new S0 bench, +drainrec vs default): {len(dr_jobs)} pairs, {a_ok} identical after removing the {a_recs} DRAIN / MEMDRAIN records "
          f"(exactly 2 added per run), {a_bad} differ, {a_fail} failed/incomplete runs")
    for d in a_bads[:10]:
        print("  DRAINREC PROBLEM:", d)
    return 0 if nd == 0 and nf == 0 and nid == len(jobs) and a_bad == 0 and a_fail == 0 and a_ok == len(dr_jobs) else 1


if __name__ == "__main__":
    sys.exit(main())
