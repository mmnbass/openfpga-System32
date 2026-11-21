#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gate of the PLL diagnostic fit (experiments/PLL-diag), run after quartus_fit and quartus_sta. Reads the fit report and the
output of pocket/pll_timing.tcl; prints PASS/FAIL per criterion; exit 0 only if every criterion passes.

  pll_gate.py --fit-rpt <pll_diag.fit.rpt> --timing-log <pll_timing.log> [--sta-summary <pll_diag.sta.summary>]
              [--logs <map/fit/sta .log> ...] [--usage-out <file>]

CRITERIA (PASS/FAIL):
  G1 fitter successful ("Fitter Status ; Successful").
  G2 exactly one PLL used: "Total PLLs" = 1 and one FRACTIONAL_PLL entry in the PLL Usage Summary, of the mf_pll_ga2 instance.
  G3 the five outputs exist (5 output counters, matched to pll_plan.py's targets by frequency and phase: clk_ram, clk_sys, clk_v25,
     clk_vid at 0 degrees, clk_vid at 90 degrees) and each actual frequency is within TOL_PPM of the target.
       TOL_PPM = 1.0 ppm. Basis: the fit report prints frequencies with 6 decimals (resolution 1e-6 MHz = up to 0.083 ppm at
       12.08 MHz); the 6-decimal strings in mf_pll_ga2.v are within 0.09 ppm of the exact VCO/C targets; the 32-bit fractional K
       quantises the VCO by ~2e-5 ppm; HW-001's PLL string was accepted at 0.016 ppm. So ~0.1 ppm is the expected error; 1 ppm
       leaves 10x margin and still rejects every non-fractional answer (integer mode is +85 ppm at best, pll_plan.py).
  G4 phase: the second clk_vid output is at 90 degrees within PHASE_TOL_DEG = 0.01 degree and every other output at 0. One
     phase step is VCO/8 = 0.70 degree of clk_vid (VCO 773 MHz), and 90 degrees = 16 VCO periods = 128 steps exactly, so the
     report must print 90.000000; 0.01 degree is far below one legal step and above the 6-decimal print resolution.
  G5 the reference clock is clk_74a, 74.25 MHz (Reference Clock Frequency and a CLKIN source named clk_74a).
  G6 internal clock-domain slacks >= 0 (setup, hold, recovery, removal; register-to-register paths only, from PLLT_INT rows of
     pll_timing.tcl) in all four corners (Slow 85C, Slow 0C, Fast 85C, Fast 0C). Setup and hold rows must exist for every PLL
     output clock that has registers (clk_ram, clk_sys, clk_v25, clk_vid); a missing or errored row is a FAIL, never a pass.
     Recovery/removal rows may be NA (the design has no asynchronous resets).
  G7 FAST_OUTPUT_REGISTER / FAST_OUTPUT_ENABLE_REGISTER / FAST_INPUT_REGISTER packed into the I/O cells for every dram_* / sram_*
     pin that has them: Output Register = yes for dram_a/ba/dqm/ras_n/cas_n/we_n/cke and sram_a/oe_n/we_n/ub_n/lb_n; Input Register,
     Output Register and Output Enable Register = yes for dram_dq[*] and sram_dq[*] (fit report Output Pins / Bidir Pins tables).
     dram_clk (driven by the altddio_out) is not in the criterion; its row is printed.
INFORMATIONAL (printed, never pass/fail; ASSUMPTION-based because the SDC's board delays are assumptions, see pocket/pll_diag.sdc):
  SDRAM / SRAM I/O setup and hold slack per corner (PLLT_IO rows), all-path summary slacks, the PLL's VCO / M / N / C /
  fractional setting, SDC block failure messages, the dram_clk_out generated clock.
"""
import argparse
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]

TOL_PPM = 1.0
PHASE_TOL_DEG = 0.01
CORNERS = ("slow_85c", "slow_0c", "fast_85c", "fast_0c")
OUT_NAMES = ([f"dram_a[{i}]" for i in range(13)] + [f"dram_ba[{i}]" for i in range(2)] + [f"dram_dqm[{i}]" for i in range(2)]
             + ["dram_ras_n", "dram_cas_n", "dram_we_n", "dram_cke"]
             + [f"sram_a[{i}]" for i in range(17)] + ["sram_oe_n", "sram_we_n", "sram_ub_n", "sram_lb_n"])
BIDIR_NAMES = [f"dram_dq[{i}]" for i in range(16)] + [f"sram_dq[{i}]" for i in range(16)]
# required PLL output clocks with registers (outclk index): clk_ram 0, clk_sys 1, clk_v25 2, clk_vid 3
REQ_CLK_IDX = (0, 1, 2, 3)


def load_targets():
    import importlib.util
    p = ROOT / "experiments/PLL-diag/pll_plan.py"
    sp = importlib.util.spec_from_file_location("pll_plan", p)
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    t = m.TARGET
    # (name, MHz as float of the exact Fraction, phase degrees)
    return [("clk_ram", float(t["clk_ram"]), 0.0), ("clk_sys", float(t["clk_sys"]), 0.0), ("clk_v25", float(t["clk_v25"]), 0.0),
            ("clk_vid", float(t["clk_vid"]), 0.0), ("clk_vid_90", float(t["clk_vid"]), 90.0)]


def cells(line):
    s = line.strip()
    if not s.startswith(";"):
        return None
    return [c.strip() for c in s.strip(";").split(";")]


def extract_section(text, title):
    """Verbatim lines of the table whose title row is '; <title>' (from the +---+ line above it to the closing +---+ line)."""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        c = cells(ln)
        if c and len(c) >= 1 and c[0] == title and i > 0 and lines[i - 1].startswith("+-"):
            out = [lines[i - 1]]
            j = i
            while j < len(lines) and lines[j].strip() != "":
                out.append(lines[j])
                j += 1
            return out
    return None


def parse_pll_usage(text):
    """-> list of PLL dicts {name, kv{}, clkin[], counters[{name,idx,freq,phase,c,duty}]} from the PLL Usage Summary."""
    sec = extract_section(text, "PLL Usage Summary")
    plls = []
    if not sec:
        return plls
    cur = cnt = None
    for ln in sec:
        c = cells(ln)
        if not c or len(c) < 2:
            continue
        name = re.sub(r"^(--\s*)+", "", c[0]).strip()
        val = c[1]
        if name.endswith("~FRACTIONAL_PLL"):
            cur = {"name": name, "kv": {}, "clkin": [], "counters": []}
            plls.append(cur)
            cnt = None
        elif cur is None:
            continue
        elif name.endswith("~PLL_OUTPUT_COUNTER"):
            m = re.search(r"general\[(\d+)\]", name)
            cnt = {"name": name, "idx": int(m.group(1)) if m else None}
            cur["counters"].append(cnt)
        elif name == "Output Clock Frequency" and cnt is not None:
            m = re.match(r"([\d.]+) MHz", val)
            cnt["freq"] = float(m.group(1)) if m else None
        elif name == "Phase Shift" and cnt is not None:
            m = re.match(r"(-?[\d.]+) degrees", val)
            cnt["phase"] = float(m.group(1)) if m else None
        elif name == "C Counter" and cnt is not None:
            cnt["c"] = int(val) if val.isdigit() else None
        elif name == "Duty Cycle" and cnt is not None:
            cnt["duty"] = val
        else:
            m = re.match(r"CLKIN\((\d)\) source", name)
            if m and val not in ("N/A", ""):
                cur["clkin"].append(val)
            elif cnt is None:
                cur["kv"][name] = val
    return plls


def parse_pins(text, title):
    """Output Pins / Bidir Pins table -> {pin name: {column: value}}."""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        c = cells(ln)
        if c and c[0] == title and i > 0 and lines[i - 1].startswith("+-") and i + 4 < len(lines):
            hdr = cells(lines[i + 2])
            if not hdr or hdr[0] != "Name":
                continue
            rows = {}
            for r in lines[i + 4:]:
                if r.strip() == "" or r.startswith("+-"):
                    break
                v = cells(r)
                if v and len(v) == len(hdr):
                    rows[v[0]] = dict(zip(hdr, v))
            return rows
    return None


def corner_key(s):
    s = s.lower()
    sf = "slow" if "slow" in s else "fast" if "fast" in s else None
    t = "85c" if "85c" in s else "0c" if re.search(r"(?<![\d])0c", s) else None
    return f"{sf}_{t}" if sf and t else None


def parse_timing_log(text):
    d = {"int": [], "io": [], "clock": [], "err": [], "done": False}
    for ln in text.splitlines():
        t = ln.split()
        if not t:
            continue
        if t[0] == "PLLT_DONE":
            d["done"] = True
        elif t[0] == "PLLT_ERR":
            d["err"].append(ln[len("PLLT_ERR"):].strip())
        elif t[0] == "PLLT_CLOCK" and len(t) >= 3:
            d["clock"].append((" ".join(t[1:-1]), t[-1]))
        elif t[0] == "PLLT_INT" and len(t) >= 5:
            # corner names may contain spaces ("Slow 1100mV 85C Model"): kind is the first of the four kind words
            k = next((i for i, w in enumerate(t) if w in ("setup", "hold", "recovery", "removal")), None)
            if k and k + 2 < len(t):
                d["int"].append((" ".join(t[1:k]), t[k], " ".join(t[k + 1:-1]), t[-1]))
        elif t[0] == "PLLT_IO" and len(t) >= 5:
            k = next((i for i, w in enumerate(t) if w in ("dram_out", "dram_in", "sram_out", "sram_in")), None)
            if k and k + 2 < len(t):
                d["io"].append((" ".join(t[1:k]), t[k], t[k + 1], t[-1]))
    return d


def num(s):
    try:
        v = float(s)
        return v if v == v and abs(v) != float("inf") else None
    except ValueError:
        return None


def gate(fit_text, timing_text, targets, sta_summary=None, logs=None):
    """-> (results [(tag, 'PASS'|'FAIL', detail)], info [str], usage_lines|None)"""
    res, info = [], []

    def add(tag, ok, detail):
        res.append((tag, "PASS" if ok else "FAIL", detail))

    fit_text = fit_text or ""
    m = re.search(r"(?m)^; Fitter Status\s*;\s*(.*?)\s*;\s*$", fit_text)
    add("G1 fitter successful", bool(m and m.group(1).startswith("Successful")), m.group(1) if m else "no 'Fitter Status' row in the fit report")

    plls = parse_pll_usage(fit_text)
    usage = extract_section(fit_text, "PLL Usage Summary")
    mt = re.search(r"(?m)^; Total PLLs\s*;\s*(\d+) / (\d+)", fit_text)
    total = int(mt.group(1)) if mt else None
    add("G2 exactly one PLL", total == 1 and len(plls) == 1 and "mf_pll_ga2:mp" in (plls[0]["name"] if plls else ""),
        f"Total PLLs = {total}; PLL Usage Summary entries = {len(plls)}" + (f"; {plls[0]['name']}" if plls else ""))

    p = plls[0] if len(plls) == 1 else None
    # ---- G3 / G4: five outputs
    cs = None
    if p is None or len(p["counters"]) != 5 or any(c.get("freq") is None or c.get("phase") is None for c in p["counters"]):
        n = len(p["counters"]) if p else 0
        add("G3 five outputs, frequencies", False, f"{n} output counters (5 expected) or unparsable rows")
        add("G4 phase of the 90-degree output", False, "no complete output counter list")
    else:
        # match by nearest target frequency (a wrong output must not shift the others), then by phase within the clk_vid pair
        import math
        names = ["clk_ram", "clk_sys", "clk_v25", "clk_vid"]
        tf = {t[0]: t[1] for t in targets}
        groups = {n: [] for n in names}
        for c in p["counters"]:
            groups[min(names, key=lambda n: abs(math.log(c["freq"] / tf[n])))].append(c)
        if [len(groups[n]) for n in names] != [1, 1, 1, 2]:
            add("G3 five outputs, frequencies", False, "output frequencies do not fall into the groups 1/1/1/2: " + str({n: [c["freq"] for c in groups[n]] for n in names}))
            add("G4 phase of the 90-degree output", False, "no consistent output grouping")
        else:
            cs = groups["clk_ram"] + groups["clk_sys"] + groups["clk_v25"] + sorted(groups["clk_vid"], key=lambda c: c["phase"])
    if cs is not None:
        ts = targets
        det3, det4, ok3, ok4 = [], [], True, True
        for c, t in zip(cs, ts):
            ppm = (c["freq"] - t[1]) / t[1] * 1e6
            good3 = abs(ppm) <= TOL_PPM
            good4 = abs(c["phase"] - t[2]) <= PHASE_TOL_DEG
            ok3 &= good3
            ok4 &= good4
            det3.append(f"{t[0]} [general[{c['idx']}], C={c.get('c')}] {c['freq']:.6f} MHz vs {t[1]:.6f}: {ppm:+.3f} ppm")
            det4.append(f"{t[0]} {c['phase']:.6f} deg (want {t[2]:g})")
        add("G3 five outputs, frequencies", ok3, f"tolerance {TOL_PPM} ppm; " + "; ".join(det3))
        add("G4 phase of the 90-degree output", ok4, f"tolerance {PHASE_TOL_DEG} deg; " + "; ".join(det4))
    # ---- G5 reference
    if p:
        ref = p["kv"].get("Reference Clock Frequency", "")
        add("G5 reference clock clk_74a", ref == "74.25 MHz" and any("clk_74a" in s for s in p["clkin"]),
            f"Reference Clock Frequency {ref!r}; CLKIN sources {p['clkin']}")
        info.append("PLL as reported: " + "; ".join(f"{k} = {p['kv'][k]}" for k in
                    ("PLL Type", "PLL Location", "PLL VCO Frequency", "M Counter", "N Counter", "PLL Fractional Division", "PLL Operation Mode", "PLL Bandwidth")
                    if k in p["kv"]))
        for c in p["counters"]:
            info.append(f"  output general[{c['idx']}]: {c.get('freq')} MHz, phase {c.get('phase')} deg, C counter {c.get('c')}, duty {c.get('duty')}")
    else:
        add("G5 reference clock clk_74a", False, "no single PLL entry")

    # ---- G6 internal slacks
    td = parse_timing_log(timing_text or "")
    probs, worst = [], {}
    if not td["done"]:
        probs.append("no PLLT_DONE (the timing script did not finish)")
    if td["err"]:
        probs.append(f"{len(td['err'])} PLLT_ERR line(s): {td['err'][0][:100]}")
    corners = sorted({r[0] for r in td["int"]})
    keys = {}
    for cn in corners:
        k = corner_key(cn)
        if k is None or k in keys:
            probs.append(f"corner {cn!r} not classifiable / duplicated")
        else:
            keys[k] = cn
    if sorted(keys) != sorted(CORNERS):
        probs.append(f"corners seen {sorted(keys)} (need {sorted(CORNERS)})")
    for k, cn in keys.items():
        for kind in ("setup", "hold", "recovery", "removal"):
            rows = [(clk, v) for c_, kd, clk, v in td["int"] if c_ == cn and kd == kind]
            vals = []
            for clk, v in rows:
                x = num(v)
                if v == "NA":
                    continue
                if x is None:
                    probs.append(f"{k} {kind} {clk}: unparsable/errored value {v!r}")
                else:
                    vals.append((x, clk))
                    if x < 0:
                        probs.append(f"{k} {kind} {clk}: slack {x} < 0")
            if kind in ("setup", "hold"):
                for idx in REQ_CLK_IDX:
                    if not any(re.search(rf"general\[{idx}\]", clk) and num(v) is not None for clk, v in rows):
                        probs.append(f"{k} {kind}: no numeric row for PLL output clock [{idx}]")
            if vals:
                worst[(k, kind)] = min(vals)
    add("G6 internal slacks >= 0, 4 corners", not probs and len(worst) >= 8,
        "; ".join(f"{k} {kind} {w[0]:+.3f} ({w[1].split('|')[-2] if '|' in w[1] else w[1]})" for (k, kind), w in sorted(worst.items())) if not probs
        else "; ".join(probs[:8]) + (f" (+{len(probs) - 8} more)" if len(probs) > 8 else ""))

    # ---- G7 packed I/O registers
    outs, bid = parse_pins(fit_text, "Output Pins"), parse_pins(fit_text, "Bidir Pins")
    miss, unpacked = [], []
    if outs is None or bid is None:
        add("G7 I/O registers packed", False, "Output Pins / Bidir Pins table not found")
    else:
        for n in OUT_NAMES:
            r = outs.get(n)
            if r is None:
                miss.append(n)
            elif r.get("Output Register") != "yes":
                unpacked.append(f"{n} (Output Register {r.get('Output Register')})")
        for n in BIDIR_NAMES:
            r = bid.get(n)
            if r is None:
                miss.append(n)
                continue
            for col in ("Input Register", "Output Register", "Output Enable Register"):
                if r.get(col) != "yes":
                    unpacked.append(f"{n} ({col} {r.get(col)})")
        total_pins = len(OUT_NAMES) + len(BIDIR_NAMES)
        add("G7 I/O registers packed", not miss and not unpacked,
            f"{total_pins - len(miss)} of {total_pins} dram_*/sram_* pins in the tables; " + ("all packed" if not miss and not unpacked else
                f"missing {miss[:6]}; not packed {unpacked[:8]} ({len(unpacked)} total)"))
        if "dram_clk" in outs:
            info.append(f"dram_clk (altddio_out): Output Register {outs['dram_clk'].get('Output Register')}, I/O standard {outs['dram_clk'].get('I/O Standard')}, "
                        f"pin {outs['dram_clk'].get('Pin #')}")

    # ---- informational
    info.append("INFORMATIONAL (ASSUMPTION-based: the SDC's SDRAM/SRAM board delays are assumptions, not Pocket data):")
    for cn in sorted({r[0] for r in td["io"]}):
        for grp in ("dram_out", "dram_in", "sram_out", "sram_in"):
            row = {kd: v for c_, g, kd, v in td["io"] if c_ == cn and g == grp}
            info.append(f"  I/O slack {corner_key(cn) or cn} {grp}: setup {row.get('setup', '-')}, hold {row.get('hold', '-')}")
    info.append(f"  dram_clk_out generated clock in the timing netlist: {'yes' if any(n == 'dram_clk_out' for n, _ in td['clock']) else 'NO'}")
    if sta_summary:
        rows = re.findall(r"Type\s*:\s*(.*?) Model (Setup|Hold|Recovery|Removal) '(.*?)'\s*\nSlack\s*:\s*(-?[\d.]+)", sta_summary)
        w = {}
        for mdl, kind, clk, sl in rows:
            k = (corner_key(mdl + " Model") or mdl, kind)
            if k not in w or float(sl) < w[k][0]:
                w[k] = (float(sl), clk)
        for k, (sl, clk) in sorted(w.items()):
            info.append(f"  quartus_sta summary (ALL paths incl. the assumed I/O delays) {k[0]} {k[1]}: worst {sl:+.3f} on {clk.split('|')[-2] if '|' in clk else clk}")
    for ln in logs or []:
        if "pll_diag SDC" in ln:
            info.append("  SDC message: " + ln.strip()[:200])
    return res, info, usage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit-rpt", type=Path, required=True)
    ap.add_argument("--timing-log", type=Path, required=True)
    ap.add_argument("--sta-summary", type=Path)
    ap.add_argument("--logs", type=Path, nargs="*", default=[])
    ap.add_argument("--usage-out", type=Path)
    a = ap.parse_args()

    def rd(p):
        try:
            return p.read_text(errors="replace")
        except OSError:
            return None
    fit, tlog = rd(a.fit_rpt), rd(a.timing_log)
    sta = rd(a.sta_summary) if a.sta_summary else None
    loglines = [ln for p in a.logs for ln in (rd(p) or "").splitlines()]
    res, info, usage = gate(fit, tlog, load_targets(), sta, loglines)
    for tag, st, det in res:
        print(f"{st}  {tag}: {det}")
    print()
    for ln in info:
        print(ln)
    if a.usage_out:
        a.usage_out.parent.mkdir(parents=True, exist_ok=True)
        a.usage_out.write_text("\n".join(usage) + "\n" if usage else "PLL Usage Summary not found in the fit report\n")
        print(f"\nPLL Usage Summary saved verbatim to {a.usage_out}")
    fails = [t for t, st, _ in res if st == "FAIL"]
    print(f"\nPLL GATE {'PASS' if not fails else 'FAIL (' + str(len(fails)) + ': ' + ', '.join(t.split()[0] for t in fails) + ')'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
