#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Static validation of the generated CPU-test Quartus project, BEFORE Quartus
runs (added after builds 40261037125 and 40262881111 failed on project
configuration). No Quartus is needed; exit 1 on any failure.

  hwt_project_check.py <project-dir> [--post <jaguar_post.qsf>] [--refqip QIP ...]

Checks:
  Q1  VERILOG_MACRO "SYNTHESIS=1" is in the QSF (exactly upstream's line) and
      no VERILOG_MACRO / SEARCH_PATH appears in any QIP the project uses.
  Q2  every source file, SDC and QIP the QSF/QIPs name resolves; every
      SEARCH_PATH exists; every `include in the sources resolves through them;
      every $readmemh default image the CPU-test RTL reads is in the project dir.
  Q3  the project's own QIP (hwt.qip) uses only assignment names that appear in
      known-working QIPs (--refqip, plus the template's apf.qip in the project).
  Q4  the seven ROM/RAM images in the project dir match
      experiments/HW-cpu-test/verified/images.sha256.
  Q6  every altera_pll in the project's Verilog has one VCO, in an assumed
      600-1300 MHz range, from which every output is an integer division (C
      1..512) within 10 ppm (run 40311704414 failed in the Fitter on this).
  Q7  the video PLL's altera_pll parameter block is identical (normalized-text
      sha256) to the Jaguar core's mf_pllbase.v, which ran on hardware.
  Q8  clock wiring in core_top: both PLLs from clk_74a; clk_sys from mp_sys feeds
      only hwt_soc and the clk_sys reset logic; clk_vid/clk_vid_90 from mp_vid
      feed hwt_video and the APF video clock pins; locked = both locks.
  Q9  CDC: every clk_sys-domain signal entering hwt_video or the bridge is read
      only by a first synchronizer stage (x_m <= in), whose output only feeds a
      second stage (x_s <= x_m); the SDC puts mp_sys and mp_vid in separate
      asynchronous groups.
  Q10 reset: the CPU reset counter reloads while the two-flop-synchronized
      combined lock is low and releases synchronously in clk_sys.
  Q11 every file an SDC pulls in with read_sdc exists, relative to the project
      directory (the template's apf_constraints.sdc reads
      core/core_constraints.sdc; run 40375644108 critical warning 332012).
  Q12 constraints read once and consistently: the only SDC_FILE is the
      template's apf/apf_constraints.sdc; core/core_constraints.sdc (read by it)
      is byte-identical to pocket/core_constraints.sdc; it creates no clocks
      (no duplicates of apf's); its hold-margin uncertainty is guarded to the
      fitter (tools/sdc_context_test.tcl PASS).
  Q5  conventions: family/device/top/config/RBF as the Jaguar POST project
      (--post), the pin and I/O assignments identical to it, SignalTap off,
      no absolute paths in the QSF or hwt.qip, upstream's SYNTHESIS line
      byte-identical.
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSIGN = re.compile(r'^\s*set_(?:global|instance)_assignment\s+(?:.*?\s)?-name\s+(\S+)\s+(.*?)\s*$')
JOIN = re.compile(r'\[file join \$::quartus\(qip_path\)\s+([^\]]+)\]')
UPSTREAM_SYN = 'set_global_assignment -name VERILOG_MACRO "SYNTHESIS=1"'
PIN_KEYS = ("set_location_assignment", "IO_STANDARD", "CURRENT_STRENGTH", "SLEW_RATE",
            "WEAK_PULL", "FAST_", "TERMINATION", "DELAY_CHAIN", "PCI_IO")
fails = []


def check(ok, msg):
    print(("ok    " if ok else "FAIL  ") + msg)
    if not ok:
        fails.append(msg)


def names(path):
    return [m.group(1) for m in (ASSIGN.match(l) for l in Path(path).read_text().splitlines()) if m]


def expand(qfile, base, out):
    """(name, resolved path or raw value, from-file) for every assignment."""
    if not Path(qfile).is_file():
        check(False, f"Q2 QIP/QSF file exists: {qfile}")
        return
    for line in Path(qfile).read_text().splitlines():
        m = ASSIGN.match(line)
        if not m:
            continue
        name, val = m.group(1), m.group(2).strip()
        j = JOIN.fullmatch(val)
        p = (Path(qfile).parent / j.group(1).strip().strip('"')) if j else (base / val.strip('"'))
        out.append((name, p.resolve(), qfile, val))
        if name == "QIP_FILE":
            expand(p, base, out)


def pll_feasible(freqs, vmin=600.0, vmax=1300.0, ppm=10.0):
    """One VCO = f_i * C_i for all outputs (integer C_i in 1..512). Returns
    (feasible, VCO MHz) or (False, the smallest worst-output error in ppm)."""
    best_err = float("inf")
    f0 = freqs[0]
    for c0 in range(1, 513):
        vco = f0 * c0
        if not vmin <= vco <= vmax:
            continue
        err = 0.0
        for f in freqs[1:]:
            c = max(1, round(vco / f))
            err = max(err, abs(vco / c - f) / f * 1e6)
        if err <= ppm:
            return True, vco
        best_err = min(best_err, err)
    return False, best_err


JAGUAR_PLL_SHA = "a330905609d457b51fe9d3adb3d3d7bb05c57232dabbdb958cde8fbd4e3b12ab"   # mf_pllbase.v @ pocket_jaguar


def pll_block(text):
    b = text[text.index("altera_pll #("):]
    return re.sub(r"\s+", " ", b[:b.index(") altera_pll_i")]).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("--post", type=Path)
    ap.add_argument("--refqip", type=Path, nargs="*", default=[])
    a = ap.parse_args()
    proj = a.project.resolve()
    qsf = proj / "hwt.qsf"
    hwt_qip = ROOT / "experiments/HW-cpu-test/pocket/hwt.qip"
    qsf_text = qsf.read_text()
    items = []
    expand(qsf, proj, items)
    qips = [p for n, p, _, _ in items if n == "QIP_FILE" and p.is_file()]

    # Q1
    check(UPSTREAM_SYN in qsf_text.splitlines(), "Q1 QSF defines SYNTHESIS=1 (upstream's exact line)")
    upstream_qsf = (ROOT / "references/s32/Arcade-SegaSystem32.qsf").read_text().splitlines()
    check(UPSTREAM_SYN in upstream_qsf, "Q1 the same line is in upstream's Arcade-SegaSystem32.qsf")
    for q in qips:
        bad = [n for n in names(q) if n in ("VERILOG_MACRO", "SEARCH_PATH")]
        check(not bad, f"Q1 no VERILOG_MACRO/SEARCH_PATH in {q.relative_to(ROOT) if ROOT in q.parents else q.name}")

    # Q2
    # *_FILE names that are switches (ON/OFF), and the pre-flow script ("tool:path")
    files, notes = [], []
    for n, p, src, raw in items:
        if not n.endswith("_FILE") or raw.strip('"') in ("ON", "OFF"):
            continue
        if n == "PRE_FLOW_SCRIPT_FILE":
            p = (proj / raw.strip('"').split(":", 1)[1]).resolve()
        if n == "MISC_FILE" and not p.exists():
            notes.append(p.name)            # metadata only; see the note below
            continue
        files.append((n, p))
    missing = [str(p) for n, p in files if not p.exists()]
    check(not missing, f"Q2 {len(files)} source/SDC/QIP/script paths resolve" + (f": missing {missing}" if missing else ""))
    if notes:
        tmpl = [n for n in notes if not (ROOT / "build/HWT/template/src/fpga/apf" / n).exists()]
        print(f"note  Q2 MISC_FILE (pin-planner metadata, not compiled) absent: {notes}; also absent "
              f"from Analogue's template and the Jaguar POST build that ran on hardware")
    search = [p for n, p, _, _ in items if n == "SEARCH_PATH"]
    check(all(p.is_dir() for p in search), f"Q2 {len(search)} SEARCH_PATHs exist")
    inc_missing = []
    for n, p in files:
        if p.suffix in (".sv", ".v", ".svh") and p.exists():
            for inc in re.findall(r'^\s*`include\s+"([^"]+)"', p.read_text(errors="replace"), re.M):
                if not any((d / inc).exists() for d in [p.parent] + search):
                    inc_missing.append(f"{p.name}: {inc}")
    check(not inc_missing, "Q2 every `include resolves" + (f": {inc_missing}" if inc_missing else ""))
    images = ["v2_cstore.hex", "v2_dispatch.hex", "v2_mode.hex", "hwt_mem_lo.hex", "hwt_mem_hi.hex",
              "hwt_expect.hex", "hwt_cfg.svh"]
    defaults = set()
    for f in ("experiments/V2-datapath-floor/rtl/v2_useq.sv", "experiments/HW-cpu-test/rtl/hwt_mem.sv",
              "experiments/HW-cpu-test/rtl/hwt_checker.sv"):
        defaults |= set(re.findall(r'parameter\s+\w+\s*=\s*"([\w.]+\.hex)"', (ROOT / f).read_text()))
    check(defaults <= set(images), f"Q2 RTL default image names {sorted(defaults)} are the manifest's")
    check(all((proj / i).exists() for i in images), "Q2 all seven images are in the project directory")

    # Q3
    allowed = set()
    for r in list(a.refqip) + [proj / "apf/apf.qip"]:
        allowed |= set(names(r))
    ours = set(names(hwt_qip)) if hwt_qip.is_file() else set()
    check(ours <= allowed, f"Q3 hwt.qip assignment names {sorted(ours)} all appear in working QIPs {sorted(allowed)}")

    # Q4
    man = {l.split()[1]: l.split()[0] for l in
           (ROOT / "experiments/HW-cpu-test/verified/images.sha256").read_text().splitlines() if l.strip()}
    bad = [i for i in images if hashlib.sha256((proj / i).read_bytes()).hexdigest() != man.get(i)]
    check(len(man) == 7 and not bad, "Q4 seven images match verified/images.sha256" + (f": {bad}" if bad else ""))

    # Q5
    def g(text, name):
        m = re.search(rf'-name {name}\s+"?([^"\n]+)"?', text)
        return m.group(1).strip() if m else None
    if a.post:
        post = a.post.read_text()
        for k in ("FAMILY", "DEVICE", "TOP_LEVEL_ENTITY", "STRATIXV_CONFIGURATION_SCHEME",
                  "GENERATE_RBF_FILE", "USE_CONFIGURATION_DEVICE", "CRC_ERROR_OPEN_DRAIN",
                  "MIN_CORE_JUNCTION_TEMP", "MAX_CORE_JUNCTION_TEMP"):
            check(g(qsf_text, k) == g(post, k), f"Q5 {k} = {g(qsf_text, k)} (POST: {g(post, k)})")
        pins = lambda t: sorted(re.sub(r"\s+", " ", l).strip() for l in t.splitlines()
                                if any(k in l for k in PIN_KEYS) and "AUTO_DELAY_CHAINS" not in l)
        check(pins(qsf_text) == pins(post), f"Q5 {len(pins(qsf_text))} pin/IO assignments identical to POST")
    else:
        print("skip  Q5 POST comparison (no --post)")
    check(g(qsf_text, "ENABLE_SIGNALTAP") == "OFF", "Q5 SignalTap off")
    absq = [l for l in qsf_text.splitlines() + hwt_qip.read_text().splitlines()
            if ASSIGN.match(l) and re.search(r'"/|\s/(Users|home|build)/', l)]
    check(not absq, "Q5 no absolute paths in the QSF or hwt.qip" + (f": {absq[:2]}" if absq else ""))

    # Q6
    for n, p in files:
        if p.suffix == ".v" and p.exists() and "altera_pll" in p.read_text():
            freqs = [float(f) for f in re.findall(r'output_clock_frequency\d+\s*\("([\d.]+) MHz"\)', p.read_text())
                     if float(f) > 0]
            ok, best = pll_feasible(freqs)
            check(ok, f"Q6 PLL {p.name} outputs {freqs} MHz share one VCO " + (f"({best:.1f} MHz)" if ok else
                  f"- no VCO in 600-1300 MHz within 10 ppm (closest: {best:.0f} ppm)"))

    pk = ROOT / "experiments/HW-cpu-test/pocket"
    # Q7
    vid = (pk / "mf_pll_vid.v").read_text()
    check(hashlib.sha256(pll_block(vid).encode()).hexdigest() == JAGUAR_PLL_SHA,
          "Q7 mf_pll_vid parameter block == Jaguar mf_pllbase.v (sha256 of normalized text)")
    # Q8
    ct = re.sub(r"//[^\n]*", "", (pk / "core_top.sv").read_text())
    flat = re.sub(r"\s+", " ", ct)
    need = {
        "mp_sys from clk_74a -> clk_sys": r"mf_pll_sys mp_sys \( ?\.refclk\(clk_74a\), \.rst\(0\), \.outclk_0\(clk_sys\), \.locked\(pll_sys_locked\)\)",
        "mp_vid from clk_74a -> clk_vid, clk_vid_90": r"mf_pll_vid mp_vid \( ?\.refclk\(clk_74a\), \.rst\(0\), \.outclk_0\(\), \.outclk_1\(clk_vid\), \.outclk_2\(clk_vid_90\), \.locked\(pll_vid_locked\)\)",
        "locked = both": r"assign pll_core_locked = pll_sys_locked & pll_vid_locked;",
        "video_rgb_clock = clk_vid": r"assign video_rgb_clock = clk_vid;",
        "video_rgb_clock_90 = clk_vid_90": r"assign video_rgb_clock_90 = clk_vid_90;",
        "hwt_soc on clk_sys": r"hwt_soc #\(.*?\) soc \( ?\.clk\(clk_sys\)",
        "hwt_video on clk_vid": r"hwt_video vid \( ?\.clk_vid\(clk_vid\)",
    }
    for what, rx in need.items():
        check(re.search(rx, flat) is not None, f"Q8 {what}")
    uses = {c: len(re.findall(rf"\b{c}\b", ct)) for c in ("clk_sys", "clk_vid", "clk_vid_90")}
    # clk_sys: declaration, mp_sys output, the clk_sys reset/lock block, hwt_soc.
    # clk_vid: declaration, mp_vid output, hwt_video, video_rgb_clock, the
    # hwt_video port name (.clk_vid). clk_vid_90: declaration, mp_vid, the pin.
    check(uses == {"clk_sys": 4, "clk_vid": 5, "clk_vid_90": 3},
          f"Q8 clock nets used only where listed (declaration, PLL, consumers): {uses}")
    # Q9
    hv = re.sub(r"//[^\n]*", "", (ROOT / "experiments/HW-cpu-test/rtl/hwt_video.sv").read_text())
    ins = ["pll_locked", "pass_count", "fail", "fail_code", "fail_idx"]
    body = hv[hv.index(");"):]
    bad = []
    for i in ins:
        for m in re.finditer(rf"\b{i}\b", body):
            line = body[body.rfind("\n", 0, m.start()) + 1: body.find("\n", m.start())]
            if not re.search(rf"\w+_m\s*<=\s*{i}\s*;", line):
                bad.append(f"{i}: {line.strip()}")
    check(not bad, "Q9 hwt_video reads clk_sys-domain inputs only in first-stage synchronizers" + (f": {bad}" if bad else ""))
    firsts = re.findall(r"(\w+)_m\s*<=\s*\w+\s*;", body)
    bad2 = [f for f in firsts if len(re.findall(rf"\b{f}_m\b", body)) != 3]   # decl, first stage, second stage
    check(not bad2, f"Q9 hwt_video first stages {firsts} feed only their second stage" + (f": {bad2}" if bad2 else ""))
    br = re.search(r"always @\(posedge clk_74a\) begin(.*?)\nend", ct, re.S)
    sys_sigs = ["pass_count", "fail_idx", "fail_code", "fail_got", "fail_exp", "fail"]
    bad3 = []
    for sig in sys_sigs:
        for m in re.finditer(rf"\b{sig}\b", ct):
            line = ct[ct.rfind("\n", 0, m.start()) + 1: ct.find("\n", m.start())]
            if re.search(r"<=", line) and not re.search(r"b_\w+_m\s*<=", line):
                bad3.append(line.strip())
    check(br is not None and not bad3, "Q9 bridge reads clk_sys-domain results only via b_*_m first stages (clk_74a)"
          + (f": {bad3}" if bad3 else ""))
    sdc = (pk / "core_constraints.sdc").read_text()
    groups = re.findall(r"-group \{([^}]*)\}", sdc)
    gs = [g for g in groups if "mp_sys" in g]
    gv = [g for g in groups if "mp_vid" in g]
    check(len(gs) == 1 and len(gv) == 1 and "mp_vid" not in gs[0] and "mp_sys" not in gv[0],
          "Q9 SDC: mp_sys and mp_vid outputs in separate asynchronous groups")
    # Q10
    for what, rx in {
        "lock synchronized into clk_sys (two flops)": r"always @\(posedge clk_sys\) begin lock_sys <= \{lock_sys\[0\], pll_core_locked\};",
        "reset reloads while synced lock is low": r"if \(!lock_sys\[1\]\) rst_cnt <= 8'hFF;",
        "synchronous release by down-counter": r"else if \(rst_cnt != 0\) rst_cnt <= rst_cnt - 1'b1;",
        "rst = counter running": r"wire rst = \(rst_cnt != 0\);",
        "hwt_soc reset is that rst": r"\.clk\(clk_sys\), \.rst\(rst\)",
    }.items():
        check(re.search(rx, flat) is not None, f"Q10 {what}")

    # Q11
    sdcs = [p for n, p in files if n == "SDC_FILE" and p.exists()]
    missing_sdc = []
    for sdc_p in sdcs:
        for ref in re.findall(r'^\s*read_sdc\s+"?([^"\s]+)"?', sdc_p.read_text(), re.M):
            if not (proj / ref).exists():
                missing_sdc.append(f"{sdc_p.name} -> {ref}")
    check(not missing_sdc, "Q11 read_sdc targets exist" + (f": missing {missing_sdc}" if missing_sdc else ""))

    # Q12
    sdc_files = [p for n, p in files if n == "SDC_FILE"]
    check([p.name for p in sdc_files] == ["apf_constraints.sdc"],
          f"Q12 the only SDC_FILE is apf_constraints.sdc ({[str(p.name) for p in sdc_files]})")
    core_sdc = proj / "core" / "core_constraints.sdc"
    check(core_sdc.is_file() and core_sdc.read_bytes() == (pk / "core_constraints.sdc").read_bytes(),
          "Q12 core/core_constraints.sdc == pocket/core_constraints.sdc")
    ctext = re.sub(r"#[^\n]*", "", (pk / "core_constraints.sdc").read_text())
    check(not re.search(r"\b(create_clock|create_generated_clock|derive_pll_clocks|set_false_path|set_multicycle_path|set_max_delay|set_min_delay)\b", ctext),
          "Q12 core constraints create no clocks and add no false-path/multicycle/delay exceptions")
    import subprocess
    r = subprocess.run(["tclsh", str(ROOT / "tools/sdc_context_test.tcl"), str(pk / "core_constraints.sdc")],
                       capture_output=True, text=True)
    check(r.returncode == 0 and "SDC CONTEXT TEST PASS" in r.stdout,
          "Q12 hold-margin uncertainty only in the fitter / tightened report (sdc_context_test)")

    print(f"\nPROJECT CHECK {'PASS' if not fails else 'FAIL (' + str(len(fails)) + ')'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
