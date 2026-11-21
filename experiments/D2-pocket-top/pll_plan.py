#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""D2 Pocket top: PLL counter plan for clk_ram 96.634615 / clk_sys 48.317307 / clk_v25 24.158653 / clk_vid 12.079327 MHz
(+ clk_vid at 90 deg) from clk_74a = 74.25 MHz on the Pocket's 5CEBA4F23C8. ARITHMETIC ONLY: what the counters would have
to be. Whether Quartus accepts the configuration is only known from a Quartus FIT (HW-001 run 40311704414 passed
synthesis and failed in the fitter on an illegal PLL output frequency).

Device limits used (E = from the Cyclone V datasheet / handbook as remembered, to be confirmed by the fit; K = observed in
this project's fits):
  VCO 600-1300 MHz for the -C8 speed grade (E; 1600 MHz for -C6/-C7)
  integer mode: fPFD = fref/N 5-325 MHz (E), M and C counters 1-512 (K(J): pocket_jaguar mf_pllbase.v notes M=520 is over
  the limit)
  fractional mode: N = 1 here (fPFD = 74.25 MHz), M = integer + K/2^32 (E); K: the HW-001 and Jaguar fits accepted
  fractional mode from clk_74a (HW-001 mp_sys: VCO ~628.1 MHz, C = 13 -> 48.317 MHz; the fit's derived SDC clock is
  written as refclk x 4331/512, a ratio rounded to 1/512, so read achieved frequencies from the fit report's PLL summary)
  output phase step = VCO period / 8 (E, Cyclone V handbook; also noted in docs/reviews/B2prep-codex-review.md)
Usage: pll_plan.py [--json out.json]"""
import json, sys
from fractions import Fraction as F

REF = F(7425, 100)                       # 74.25 MHz
RAM = F(125625, 1300)                    # 96.634615... = 1256.25/13 MHz (= 2 x 628.125/13, upstream's 48.317307 x 2)
TARGET = {"clk_ram": RAM, "clk_sys": RAM / 2, "clk_v25": RAM / 4, "clk_vid": RAM / 8}
VCO_MIN, VCO_MAX = 600, 1300

def fractional():
    out = []
    for k in range(1, 20):
        vco = RAM * k
        if not (VCO_MIN <= vco <= VCO_MAX): continue
        c = {"clk_ram": k, "clk_sys": 2 * k, "clk_v25": 4 * k, "clk_vid": 8 * k}
        if max(c.values()) > 512: continue
        m = vco / REF                                    # N = 1
        mi = m.numerator // m.denominator
        kfrac = (m - mi) * 2**32
        kq = round(kfrac)
        ach = REF * (mi + F(kq, 2**32))                  # achieved VCO with a 32-bit fractional K
        err_ppm = float((ach - vco) / vco * 10**6)
        step_ps = float(F(10**6) / vco / 8)              # phase step, ps
        vid_period_ps = float(F(10**6) / TARGET["clk_vid"])
        steps_90 = vid_period_ps / 4 / step_ps
        out.append({"vco_mhz": float(vco), "N": 1, "M": float(m), "M_int": mi, "K_32bit": kq, "C": c,
                    "achieved_err_ppm_32bit_K": round(err_ppm, 6), "tq_ratio_512": f"{round(float(m) * 512)}/512",
                    "tq_ratio_512_err_ppm": round(float((F(round(float(m) * 512), 512) - m) / m * 10**6), 1),
                    "phase_step_ps": round(step_ps, 2), "vid_90deg_ps": round(vid_period_ps / 4, 1), "vid_90deg_in_steps": round(steps_90, 6)})
    return out

def integer(best=8):
    res = []
    for n in range(1, 513):
        pfd = REF / n
        if pfd < 5: break
        if pfd > 325: continue
        for m in range(1, 513):
            vco = pfd * m
            if vco < VCO_MIN: continue
            if vco > VCO_MAX: break
            for cr in range(1, 65):
                if 8 * cr > 512: break
                f = vco / cr
                e = float((f - RAM) / RAM) * 1e6
                if abs(e) < 2000: res.append((abs(e), e, n, m, cr, float(vco), float(f)))
    res.sort()
    return [{"err_ppm": round(es, 2), "N": n, "M": m, "C": {"clk_ram": cr, "clk_sys": 2 * cr, "clk_v25": 4 * cr, "clk_vid": 8 * cr},
             "vco_mhz": round(v, 4), "clk_ram_mhz": round(f, 6)} for _a, es, n, m, cr, v, f in res[:best]]

def main():
    fr, it = fractional(), integer()
    print("Targets (MHz):", {k: round(float(v), 6) for k, v in TARGET.items()}, "(exact: clk_ram = 1256.25/13)")
    print("\nFRACTIONAL, one PLL, N = 1 (fPFD 74.25 MHz), all outputs integer divisions of one VCO:")
    for o in fr:
        print(f"  VCO {o['vco_mhz']:.6f} MHz  M = {o['M']:.9f} (int {o['M_int']}, K = {o['K_32bit']} / 2^32)  C = {o['C']}"
              f"  achieved error {o['achieved_err_ppm_32bit_K']:+.6f} ppm  (TimeQuest-style {o['tq_ratio_512']}: {o['tq_ratio_512_err_ppm']:+.1f} ppm)"
              f"  phase step {o['phase_step_ps']} ps, clk_vid 90 deg = {o['vid_90deg_ps']} ps = {o['vid_90deg_in_steps']} steps")
    print("\nINTEGER mode (no fractional M), best clk_ram errors with fPFD >= 5 MHz (clk_sys/v25/vid follow exactly):")
    for o in it:
        print(f"  N {o['N']:3d} M {o['M']:3d} VCO {o['vco_mhz']:.4f} C {o['C']}  clk_ram {o['clk_ram_mhz']:.6f} MHz  error {o['err_ppm']:+.2f} ppm")
    if "--json" in sys.argv:
        open(sys.argv[sys.argv.index("--json") + 1], "w").write(json.dumps({"fractional": fr, "integer": it}, indent=1))

if __name__ == "__main__":
    main()
