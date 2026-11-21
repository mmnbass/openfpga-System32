#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gate of the first full D2 Pocket-top fit (experiments/D2-pocket-top), run after quartus_fit and quartus_sta. Prints PASS/FAIL per
CORE criterion and INFORMATIONAL items (never fail); exit 1 only if a core criterion fails. Reuses tools/pll_gate.py for the PLL
criteria (imported, not copied): the fit-report parser, G1-G5 and the timing-log parser are exactly the ones that judged the PLL
diagnostic (run 42116132593).

  d2_gate.py --fit-rpt ga2_d2.fit.rpt --fit-summary ga2_d2.fit.summary --timing-log d2_timing.log [--sta-summary ga2_d2.sta.summary]
             [--postmap-out postmap_gate.txt] [--blocks-csv blocks.csv] [--logs map.log fit.log sta.log] [--usage-out pll_usage_summary.txt]

CORE (a FAIL fails the job):
  G1 fitter successful.                                                       (pll_gate)
  G2 exactly one PLL, and it is mf_pll_ga2 (instance mf_pll_ga2:mp).          (pll_gate)
  G3 its five outputs within 1 ppm of experiments/D2-pocket-top/pll_plan.py's targets.   (pll_gate)
  G4 the 90-degree clk_vid output at 90.000 degrees, the others at 0.         (pll_gate)
  G5 reference clock clk_74a at 74.25 MHz.                                    (pll_gate)
  R1 M10K blocks (fit summary "Total RAM Blocks") <= 308.   R2 ALMs ("Logic utilization (in ALMs)") <= 18,480.  Both are implied by a
     successful fit; printed as an independent check.
  G6 internal register-to-register setup / hold / recovery / removal >= 0 for every PLL output clock (general[0..4]) in all four corners
     (Slow 85C, Slow 0C, Fast 85C, Fast 0C). Setup and hold rows must be numeric for clk_ram, clk_sys and clk_vid (general[0], [1], [3]:
     they carry registers); clk_v25 / clk_vid_90 ([2], [4]) and recovery / removal may be NA (no such paths). A missing row, a
     PLLT_ERR, no PLLT_DONE or an unparsable value is a FAIL, never a pass.
  (G8 no programming file, no assembler trace: a SEPARATE step, tools/progfile_guard.py check; the job fails on it independently.)
INFORMATIONAL (printed, never pass/fail):
  post-map gate verdict (first run); I/O register packing of dram_* / sram_* (which pins are / are not packed); SDRAM I/O slack
  (A4/A5, upstream profile, unverified board assumptions); SRAM-in slack (A6-A8) and SRAM-out slack (A10/A11, generic-SRAM
  assumptions, quartus_sta only) each as its own group; resources vs the README estimates (ALMs 13.7-14.2K, M10K 266-268);
  internal slack of non-PLL clocks; the 10 worst register-to-register setup paths; the PLL usage; the per-block table.
"""
import argparse
import csv
import importlib.util
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import pll_gate  # noqa: E402

PLAN = ROOT / "experiments/D2-pocket-top/pll_plan.py"
M10K_LIMIT, ALM_LIMIT = 308, 18480
EST_ALM, EST_M10K = (13700, 14200), (266, 268)
PLL_CLK = re.compile(r"general\[(\d)\]\.gpll~PLL_OUTPUT_COUNTER\|divclk$")
NEED_NUMERIC = (0, 1, 3)            # clk_ram, clk_sys, clk_vid carry registers; clk_v25 [2] and clk_vid_90 [4] may report NA


def load_targets(path=PLAN):
    sp = importlib.util.spec_from_file_location("d2_pll_plan", path)
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    t = m.TARGET
    return [("clk_ram", float(t["clk_ram"]), 0.0), ("clk_sys", float(t["clk_sys"]), 0.0), ("clk_v25", float(t["clk_v25"]), 0.0),
            ("clk_vid", float(t["clk_vid"]), 0.0), ("clk_vid_90", float(t["clk_vid"]), 90.0)]


def summary_val(text, key):
    m = re.search(rf"(?m)^{re.escape(key)}\s*:\s*([\d,]+)\s*/\s*([\d,]+)", text or "")
    return (int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))) if m else (None, None)


def parse_extra(timing_text):
    sram, paths = [], []
    for ln in (timing_text or "").splitlines():
        t = ln.split()
        if t and t[0] == "D2T_SRAMOUT":
            k = next((i for i, w in enumerate(t) if w in ("ad", "dq", "strobe")), None)
            if k and k + 2 < len(t):
                sram.append((" ".join(t[1:k]), t[k], t[k + 1], t[-1]))
        elif ln.startswith("D2T_PATH\t"):
            f = ln.split("\t")
            if len(f) >= 5 and pll_gate.num(f[2]) is not None:
                paths.append((float(f[2]), f[1], f[3], f[4]))
    return sram, sorted(paths)


def parse_holdcore(timing_text):
    """D2T_HOLD / D2T_HOLDCLK rows -> [(slack, corner, to, from, launch, latch, skew, data_delay, levels, tag)];
    D2T_CORE rows -> [(slack, corner, label, to, from, latch)]. Added after run 42126911324 (unnamed -0.002 ns clk_ram hold)."""
    hold, core = [], []
    for ln in (timing_text or "").splitlines():
        f = ln.split("\t")
        if f[0] in ("D2T_HOLD", "D2T_HOLDCLK") and len(f) >= 10 and pll_gate.num(f[2]) is not None:
            hold.append((float(f[2]), f[1], f[3], f[4], f[5], f[6], f[7], f[8], f[9], f[0]))
        elif f[0] == "D2T_CORE" and len(f) >= 7 and pll_gate.num(f[3]) is not None:
            core.append((float(f[3]), f[1], f[2], f[4], f[5], f[6]))
    return sorted(hold), sorted(core)


def short_clk(n):
    m = re.search(r"general\[(\d)\]", n or "")
    return {"0": "clk_ram", "1": "clk_sys", "2": "clk_v25", "3": "clk_vid", "4": "clk_vid_90"}.get(m.group(1), n) if m else (n or "?")


def hold_class(launch, latch, to):
    """Path-type classification of one hold path: same clock / synchronous crossing between outputs of the one PLL / other
    crossing (would be a CDC or constraint question), plus the endpoint kind (fixed-timing block inputs cannot take hold delay)."""
    pll = lambda n: bool(re.search(r"general\[\d\]\.gpll~PLL_OUTPUT_COUNTER", n or ""))
    if launch == latch:
        k = "same clock"
    elif pll(launch) and pll(latch):
        k = "synchronous crossing between outputs of the one PLL"
    else:
        k = "OTHER CROSSING (CDC / constraint question)"
    t = to or ""
    if re.search(r"ram_block|altsyncram|~porta|~portb", t):
        e = "M10K input register"
    elif re.search(r"mac|DSP|~OUTPUT_REGISTER|Mult", t):
        e = "DSP"
    elif re.search(r"ddio|~input|~output|_IO", t, re.I):
        e = "I/O"
    else:
        e = "fabric register"
    return k, e


def internal_slacks(td):
    """-> (problems, worst{(corner,kind)->(slack,clk)}, nonpll_negative[(corner,kind,clk,slack)], na_notes)."""
    probs, worst, nonpll, na = [], {}, [], set()
    if not td["done"]:
        probs.append("no PLLT_DONE (the timing script did not finish)")
    if td["err"]:
        probs.append(f"{len(td['err'])} PLLT_ERR line(s): {td['err'][0][:100]}")
    keys = {}
    for cn in sorted({r[0] for r in td["int"]}):
        k = pll_gate.corner_key(cn)
        if k is None or k in keys:
            probs.append(f"corner {cn!r} not classifiable / duplicated")
        else:
            keys[k] = cn
    if sorted(keys) != sorted(pll_gate.CORNERS):
        probs.append(f"corners seen {sorted(keys)} (need {sorted(pll_gate.CORNERS)})")
    for k, cn in keys.items():
        for kind in ("setup", "hold", "recovery", "removal"):
            rows = [(clk, v) for c_, kd, clk, v in td["int"] if c_ == cn and kd == kind]
            vals = []
            for idx in range(5):
                mine = [(clk, v) for clk, v in rows if (m := PLL_CLK.search(clk)) and int(m.group(1)) == idx]
                if len(mine) != 1:
                    probs.append(f"{k} {kind}: {len(mine)} rows for PLL output clock [{idx}] (1 expected)")
                    continue
                clk, v = mine[0]
                x = pll_gate.num(v)
                if v == "NA":
                    if kind in ("setup", "hold") and idx in NEED_NUMERIC:
                        probs.append(f"{k} {kind}: NA (no paths) for PLL output clock [{idx}], which carries registers")
                    na.add((kind, idx))
                elif x is None:
                    probs.append(f"{k} {kind} [{idx}]: unparsable/errored value {v!r}")
                else:
                    vals.append((x, idx))
                    if x < 0:
                        probs.append(f"{k} {kind} general[{idx}]: slack {x} < 0")
            for clk, v in rows:
                if not PLL_CLK.search(clk):
                    x = pll_gate.num(v)
                    if x is not None and x < 0:
                        nonpll.append((k, kind, clk, x))
            if vals:
                worst[(k, kind)] = min(vals)
    return probs, worst, nonpll, na


def gate(fit_text, fit_summary, timing_text, targets, sta_summary=None, logs=None, postmap_text=None, blocks_rows=None):
    """-> (core [(tag, 'PASS'|'FAIL', detail)], info [str], usage_lines|None)"""
    core, info = [], []
    pres, pinfo, usage = pll_gate.gate(fit_text, timing_text, targets, sta_summary, logs)
    for tag, st, det in pres:
        if tag.split()[0] in ("G1", "G2", "G3", "G4", "G5"):
            core.append((tag, st, det))

    # ---- R1 / R2 resources
    s = fit_summary if fit_summary else fit_text
    m10k, m10k_tot = summary_val(s, "Total RAM Blocks")
    alm, alm_tot = summary_val(s, "Logic utilization (in ALMs)")
    core.append(("R1 M10K <= 308", "PASS" if m10k is not None and m10k <= M10K_LIMIT else "FAIL",
                 f"Total RAM Blocks {m10k} / {m10k_tot} (limit {M10K_LIMIT})"))
    core.append(("R2 ALMs <= 18,480", "PASS" if alm is not None and alm <= ALM_LIMIT else "FAIL",
                 f"Logic utilization {alm} / {alm_tot} (limit {ALM_LIMIT})"))

    # ---- G6 internal slacks (PLL clocks)
    td = pll_gate.parse_timing_log(timing_text or "")
    probs, worst, nonpll, na = internal_slacks(td)
    core.append(("G6 internal slacks >= 0, PLL clocks, 4 corners", "PASS" if not probs and len(worst) >= 8 else "FAIL",
                 "; ".join(f"{k} {kind} {w[0]:+.3f} (general[{w[1]}])" for (k, kind), w in sorted(worst.items())) if not probs
                 else "; ".join(probs[:8]) + (f" (+{len(probs) - 8} more)" if len(probs) > 8 else "")))

    # ---- informational
    info.append("---- INFORMATIONAL (never fails the job) ----")
    pm = None
    if postmap_text:
        mm = re.findall(r"D2 POSTMAP GATE (PASS|FAIL[^\n]*)", postmap_text)
        pm = mm[-1] if mm else None
        fails = [ln for ln in postmap_text.splitlines() if ln.startswith("FAIL")]
    info.append(f"post-map gate (first run, informational): {('D2 POSTMAP GATE ' + pm) if pm else 'NO VERDICT (not run or unreadable)'}")
    if postmap_text and pm and pm.startswith("FAIL"):
        info += ["    " + f[:200] for f in fails[:10]]
    info.append(f"resources vs estimate: ALMs {alm} (estimate {EST_ALM[0]}-{EST_ALM[1]}: "
                f"{'inside' if alm is not None and EST_ALM[0] <= alm <= EST_ALM[1] else 'OUTSIDE'}); "
                f"M10K {m10k} (estimate {EST_M10K[0]}-{EST_M10K[1]}: {'inside' if m10k is not None and EST_M10K[0] <= m10k <= EST_M10K[1] else 'OUTSIDE'}). "
                "Outside the estimate is a finding, not a failure.")
    for key in ("Total registers", "Total block memory bits", "Total DSP Blocks", "Total pins", "Total PLLs"):
        mv = re.search(rf"(?m)^{re.escape(key)}\s*:\s*(.*)$", s or "")
        if mv:
            info.append(f"  {key}: {mv.group(1).strip()}")
    info += pinfo[:pinfo.index(next(x for x in pinfo if x.startswith("INFORMATIONAL")))] if any(x.startswith("INFORMATIONAL") for x in pinfo) else []

    outs, bid = pll_gate.parse_pins(fit_text or "", "Output Pins"), pll_gate.parse_pins(fit_text or "", "Bidir Pins")
    if outs is None or bid is None:
        info.append("I/O register packing: Output Pins / Bidir Pins table not found")
    else:
        unpacked, missing, packed = [], [], 0
        for n in pll_gate.OUT_NAMES:
            r = outs.get(n)
            if r is None:
                missing.append(n)
            elif r.get("Output Register") != "yes":
                unpacked.append(f"{n} (Output Register {r.get('Output Register')})")
            else:
                packed += 1
        for n in pll_gate.BIDIR_NAMES:
            r = bid.get(n)
            if r is None:
                missing.append(n)
                continue
            bad = [c for c in ("Input Register", "Output Register", "Output Enable Register") if r.get(c) != "yes"]
            if bad:
                unpacked.append(f"{n} ({', '.join(c + ' ' + str(r.get(c)) for c in bad)})")
            else:
                packed += 1
        tot = len(pll_gate.OUT_NAMES) + len(pll_gate.BIDIR_NAMES)
        info.append(f"I/O register packing (dram_*/sram_*): {packed} of {tot} pins packed; not packed: "
                    + (", ".join(unpacked) if unpacked else "none") + "; not in the tables: " + (", ".join(missing) if missing else "none"))

    sram, paths = parse_extra(timing_text)
    info.append("I/O slack per corner (setup / hold, ns). SDRAM groups use A4/A5 = upstream MiSTer numbers; SRAM-in A6-A8; SRAM-out A10/A11 "
                "(generic-SRAM assumptions, quartus_sta only). ALL ARE ASSUMPTION-BASED, NOT POCKET BOARD DATA; NA = no constraint / no path:")
    for cn in sorted({r[0] for r in td["io"]}):
        for grp, label in (("dram_out", "SDRAM out (A4)"), ("dram_in", "SDRAM in (A4,A5)"), ("sram_in", "SRAM in (A6-A8)"), ("sram_out", "SRAM out, all pins (A10,A11)")):
            row = {kd: v for c_, g, kd, v in td["io"] if c_ == cn and g == grp}
            info.append(f"  {pll_gate.corner_key(cn) or cn:9s} {label:30s} setup {row.get('setup', '-'):>8s}  hold {row.get('hold', '-'):>8s}")
    for cn in sorted({r[0] for r in sram}):
        for sub, label in (("ad", "SRAM out A/UB/LB (A10)"), ("dq", "SRAM out DQ write data (A10)"), ("strobe", "SRAM out OE/WE strobes (A11)")):
            row = {kd: v for c_, g, kd, v in sram if c_ == cn and g == sub}
            info.append(f"  {pll_gate.corner_key(cn) or cn:9s} {label:30s} setup {row.get('setup', '-'):>8s}  hold {row.get('hold', '-'):>8s}")
    if not sram:
        info.append("  (no D2T_SRAMOUT rows in the timing log)")
    info.append("  SRAM-out is reported separately from the verified/structural constraints above (G6); a negative value here is a finding about "
                "the assumed profile (and, with the part unknown, about d2_sram_port's write timing), not a gate failure.")
    info.append(f"  dram_clk_out generated clock in the timing netlist: {'yes' if any(n == 'dram_clk_out' for n, _ in td['clock']) else 'NO'}")
    info.append("internal slack of non-PLL clocks: " + ("all >= 0 or NA" if not nonpll else
                "NEGATIVE: " + "; ".join(f"{k} {kind} {clk} {x:+.3f}" for k, kind, clk, x in nonpll[:8])))
    info.append(f"NA (no paths) internal setup/hold rows seen, by PLL output index: {sorted(i for k, i in na if k in ('setup', 'hold'))}")
    info.append("10 worst register-to-register setup paths (any corner):")
    for sl, cn, to, fr in paths[:10]:
        info.append(f"  {sl:+8.3f}  {pll_gate.corner_key(cn) or cn}  {to}\n             <- {fr}")
    if not paths:
        info.append("  (no D2T_PATH rows)")
    hold, core_paths = parse_holdcore(timing_text)
    info.append("worst hold paths (D2T_HOLD: register to register, any clock; D2T_HOLDCLK: latched by clk_ram, any endpoint):")
    seen = set()
    for sl, cn, to, fr, lc, tc, sk, dd, lv, tag in hold:
        key = (cn, to, fr)
        if key in seen:
            continue
        seen.add(key)
        if len(seen) > 12:
            break
        k, e = hold_class(lc, tc, to)
        info.append(f"  {sl:+8.3f}  {pll_gate.corner_key(cn) or cn}  {short_clk(lc)} -> {short_clk(tc)} [{k}; endpoint {e}] skew {sk} "
                    f"data {dd} levels {lv} ({tag})\n             {to}\n             <- {fr}")
    if not hold:
        info.append("  (no D2T_HOLD rows)")
    info.append("GA2 core (s32_core) worst setup paths (D2T_CORE; the verifier and the Pocket top are excluded by construction):")
    for lbl in ("clk_ram", "any"):
        rows = [r for r in core_paths if r[2] == lbl]
        if rows:
            sl, cn, _l, to, fr, tc = rows[0]
            info.append(f"  {lbl:8s} worst {sl:+8.3f} at {pll_gate.corner_key(cn) or cn} ({short_clk(tc)})\n             {to}\n             <- {fr}")
        else:
            info.append(f"  {lbl:8s} (no D2T_CORE rows)")
    for ln in logs or []:
        if "D2 SDC" in ln:
            info.append("  SDC message: " + ln.strip()[:200])
    if sta_summary:
        rows = re.findall(r"Type\s*:\s*(.*?) Model (Setup|Hold|Recovery|Removal) '(.*?)'\s*\nSlack\s*:\s*(-?[\d.]+)", sta_summary)
        w = {}
        for mdl, kind, clk, sl in rows:
            k = (pll_gate.corner_key(mdl + " Model") or mdl, kind)
            if k not in w or float(sl) < w[k][0]:
                w[k] = (float(sl), clk)
        for k, (sl, clk) in sorted(w.items()):
            info.append(f"  quartus_sta summary (ALL paths incl. the assumed I/O delays) {k[0]} {k[1]}: worst {sl:+.3f} on {clk.split('|')[-2] if '|' in clk else clk}")
    if blocks_rows:
        info.append("per-block resources (e1_report.py blocks.csv):")
        info.append("  " + f"{'block':26s}{'ALMs':>9s}{'M10K':>6s}{'DSP':>5s}")
        for r in blocks_rows:
            info.append("  " + f"{r.get('block', '?')[:25]:26s}{r.get('fit_ALMs', '?'):>9s}{r.get('fit_M10K', '?'):>6s}{r.get('fit_DSP', '?'):>5s}")
    else:
        info.append("per-block resources: no blocks.csv (e1_report.py not run or failed; informational)")
    return core, info, usage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit-rpt", type=Path, required=True)
    ap.add_argument("--fit-summary", type=Path)
    ap.add_argument("--timing-log", type=Path, required=True)
    ap.add_argument("--sta-summary", type=Path)
    ap.add_argument("--postmap-out", type=Path)
    ap.add_argument("--blocks-csv", type=Path)
    ap.add_argument("--logs", type=Path, nargs="*", default=[])
    ap.add_argument("--usage-out", type=Path)
    a = ap.parse_args()

    def rd(p):
        try:
            return p.read_text(errors="replace") if p else None
        except OSError:
            return None
    blocks = None
    if a.blocks_csv and a.blocks_csv.is_file():
        with open(a.blocks_csv, newline="") as f:
            blocks = list(csv.DictReader(f))
    loglines = [ln for p in a.logs for ln in (rd(p) or "").splitlines()]
    core, info, usage = gate(rd(a.fit_rpt), rd(a.fit_summary), rd(a.timing_log), load_targets(), rd(a.sta_summary), loglines, rd(a.postmap_out), blocks)
    print("---- CORE criteria (a FAIL fails the job) ----")
    for tag, st, det in core:
        print(f"{st}  {tag}: {det}")
    print("(programming file / assembler trace: separate step, tools/progfile_guard.py check)\n")
    for ln in info:
        print(ln)
    if a.usage_out:
        a.usage_out.parent.mkdir(parents=True, exist_ok=True)
        a.usage_out.write_text("\n".join(usage) + "\n" if usage else "PLL Usage Summary not found in the fit report\n")
        print(f"\nPLL Usage Summary saved verbatim to {a.usage_out}")
    fails = [t for t, st, _ in core if st == "FAIL"]
    print(f"\nD2 GATE {'PASS' if not fails else 'FAIL (' + str(len(fails)) + ': ' + ', '.join(t.split()[0] for t in fails) + ')'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
