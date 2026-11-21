#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assemble the Quartus project for the D2 Pocket top (GA2 first pixels). It does NOT run Quartus: building needs approval.
Pattern of experiments/HW-cpu-test/pocket/prepare_quartus.py (HW-001, whose core ran on the Pocket).

  gen_d2.py <fresh-gen-dir> --template <core-template>/src/fpga --config fit
  prepare_quartus_d2.py <gen-dir> <fresh-out-dir> --template <core-template>/src/fpga [--mc-ref HEAD] [--seed N] [--microcode-pin FILE]
  d2_project_check.py <out-dir> --template <core-template>/src/fpga        # static check, then
  d2_project_check.py <out-dir> --template ... --selftest                   # negative controls (tampered copies)

<gen-dir> is gen_d2.py's output for --config fit or --config sp (GA2Play2: fit + the SP sprite path; same QSF / SDC / PLL / I/O,
only the compiled source list differs; the s2 configuration cannot fit: README) or --config spid (SpidPlay1: sp with the Spider-Man
top and upstream s32_io.sv, experiments/SM1-spidman) or --config univ (U1b: sp with the universal top / core, experiments/U1-universal;
its core SDC = the base SDC + the univ-only fragment) or --config au (exp/audio1: sp + sound; the same
QSF; its core/core_constraints.sdc = pocket/d2_core_constraints.sdc byte for byte FOLLOWED by pocket/d2_au_constraints.sdc, and
run.json additionally records sdc_au_append_sha256) or --config au3 (exp/audio3, GA2Audio3: au with the Z80 sound ROM in PSRAM
chip 1; the same QSF; core/core_constraints.sdc = the base SDC + pocket/d2_au_constraints.sdc + pocket/d2_au3_constraints.sdc, each
byte for byte; run.json records sdc_au_append_sha256 and sdc_au3_append_sha256) or --config rel (release/s32-v1: au3 with the
universal top / core; core SDC = the au3 SDC followed by the univ-only fragment, each byte for byte). --template must be a checkout
of Analogue's core-template at the pin of hw-build.yml (TEMPLATE_REV, da3a021), unmodified. <out-dir> must not exist.

What it writes into <out-dir> (project revision ga2_d2, top entity apf_top):
  ga2_d2.qpf / ga2_d2.qsf   the template's ap_core.qsf minus the template's own core files and SignalTap, then
                            d2_pocket_io.apply() (sram_* 3.3-V LVTTL + the I/O-register / 8 mA block), then: core_bridge_cmd.v,
                            d2_core.qip, ENABLE_SIGNALTAP OFF, VERILOG_MACRO "SYNTHESIS=1" and upstream's macros, the
                            SEARCH_PATHs of the QIP header (QSF assignments: Quartus rejects both inside a QIP), SEED
  d2_core.qip               gen_d2.py's draft QIP with every path rebased to be relative to <out-dir>
  apf/, core_bridge_cmd.v   copied from the template, unchanged
  core/core_constraints.sdc pocket/d2_core_constraints.sdc: the project's only SDC besides apf/apf_constraints.sdc, which
                            read_sdc's it once. --config univ ONLY (U1b): the same bytes followed by the bytes of
                            experiments/U1-universal/pocket/u1_fit_hold.sdc (UNIV_SDC_FRAGMENT: the fitter-only clk_sys -> clk_ram
                            hold margin A12), recorded in run.json as sdc_univ_fragment {path, sha256}; run.json sdc_sha256 stays the
                            shared file's. Every other configuration copies the shared file unchanged and records no fragment.
  v2_cstore.hex v2_dispatch.hex v2_mode.hex   the CS_ROM = 1 images ($readmemh paths are relative to the project directory)
                            from the production microcode command (m1_m11_microcode.py --reset-native), generated here from
                            COMMITTED source (git archive of --mc-ref) and refused unless their sha256 equal the ones in
                            experiments/P1-pocket-top/BASELINE_S2.md section 9
  microcode.bin             upstream's V25 microcode (Microcode.sv reads "./microcode.bin"); refused unless its sha256 equals
                            the one in BASELINE_S2.md section 9
  files_synth_resolved.f    the project's compiled-file list (for check_synth_set.py)
  run.json                  provenance: repo commit and dirty state, template rev, upstream pin, config, macros, hashes
Scratch (git archive of the microcode sources and its output) goes to <out-dir>.mc/ next to <out-dir>.

--microcode-pin FILE (default: none = exactly the behaviour above). A DELIBERATE microcode change is introduced through a pin
(tools/microcode_pin.py, experiments/D2-pocket-top/reference/microcode_pin_<name>.json), never by weakening the check: the expected
sha256 of the three images and of the builder then come from the pin instead of BASELINE_S2.md section 9 (the V25 microcode.bin
value still comes from BASELINE_S2.md and must equal the pin's), and the M1-functional tree at --mc-ref must be the pin's. run.json
then records verified_against = the pin's repository path and pin_sha256 (keys images_sha256 / baseline_s2_expected unchanged).
The builder is run with the pin's `args` (default ["--reset-native"]; a pin may add "--mc-pack=tail,zero", see tools/microcode_pin.py) and
never with GA2_MC_PACK in its environment; for a pin that has `args`, run.json also records microcode.builder_args.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
D2 = HERE.parent
ROOT = D2.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))
import d2_pocket_io  # noqa: E402
import microcode_pin  # noqa: E402

TEMPLATE_REV = "da3a021b1eaf742604d86d8dc9b33a6666263e6a"        # .github/workflows/hw-build.yml TEMPLATE_REV (v1.3.0)
REVISION = "ga2_d2"
AU_SDC = HERE / "d2_au_constraints.sdc"          # au / au3: appended to the base SDC
AU3_SDC = HERE / "d2_au3_constraints.sdc"        # au3 only: appended after the au appendix
BASELINE = ROOT / "experiments/P1-pocket-top/BASELINE_S2.md"
# U1b (Michael 2026-11-14, "a univ-only fitter hold-optimization margin, not an RTL change"): appended to the core SDC for --config univ only
UNIV_SDC_FRAGMENT = "experiments/U1-universal/pocket/u1_fit_hold.sdc"
MC_DIRS = ["experiments/M1-functional", "experiments/V2-datapath-floor", "tools"]
MC_BUILDER = "experiments/M1-functional/m1_m11_microcode.py"
# template lines that are the template's own core (HW-001's DROP list) or SignalTap
DROP = ("core/core_top.v", "core/core_bridge_cmd.v", "core/core_constraints.sdc",
        "core/mf_pllbase.qip", "core/mf_pllbase.sip", "stp1.stp", "SLD_FILE", "ENABLE_SIGNALTAP")
QIP_PATH = re.compile(r'^(set_global_assignment -name (?:VERILOG_FILE|SYSTEMVERILOG_FILE|VHDL_FILE) )'
                      r'\[file join \$::quartus\(qip_path\) (\S+)\]\s*$')
HDR_MACRO = re.compile(r'^#\s+VERILOG_MACRO "([^"]+)"\s*$')
HDR_SEARCH = re.compile(r'^#\s+SEARCH_PATH (\S+)\s*$')


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def git(*a, cwd=ROOT, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"git {' '.join(a)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def expected_hashes(pin=None):
    """sha256 of the microcode images and upstream's V25 microcode.bin, and of the builder, from BASELINE_S2.md section 9.
    pin (a path): the images and the builder come from the validated pin instead; the V25 microcode.bin value is still
    BASELINE_S2.md's and the pin's must equal it."""
    t = BASELINE.read_text()
    sec = t[t.index("## 9. Source identity"):]
    out = {}
    for name in ("m1_cstore.hex", "m1_dispatch.hex", "m1_mode.hex"):
        m = re.search(rf"{re.escape(name)}`?\s+`([0-9a-f]{{64}})`", sec)
        if not m:
            raise SystemExit(f"BASELINE_S2.md section 9: no sha256 for {name}")
        out[name] = m.group(1)
    m = re.search(r"V25 `microcode\.bin`\s+`([0-9a-f]{64})`", sec)
    if not m:
        raise SystemExit("BASELINE_S2.md section 9: no sha256 for microcode.bin")
    out["microcode.bin"] = m.group(1)
    m = re.search(r"builder `m1_m11_microcode\.py` sha256 `([0-9a-f]{64})`", sec)
    if not m:
        raise SystemExit("BASELINE_S2.md section 9: no sha256 for the builder")
    out["builder"] = m.group(1)
    if pin is not None:
        p = microcode_pin.load_pin(pin)
        if p["v25_microcode_bin_sha256"] != out["microcode.bin"]:
            raise SystemExit(f"refusing: pin V25 microcode.bin {p['v25_microcode_bin_sha256']} != BASELINE_S2.md section 9 {out['microcode.bin']}")
        out.update(p["images_sha256"])
        out["builder"] = p["builder_sha256"]
    return out


def parse_qip(qip_text):
    """(file lines [(directive, path-relative-to-D2)], macros, search paths) of gen_d2.py's draft QIP."""
    files, macros, search = [], [], []
    for ln in qip_text.splitlines():
        m = QIP_PATH.match(ln)
        if m:
            files.append((m.group(1), m.group(2)))
        m = HDR_MACRO.match(ln)
        if m:
            macros.append(m.group(1))
        m = HDR_SEARCH.match(ln)
        if m:
            search.append(m.group(1))
    return files, macros, search


def rel_to(p, base):
    return Path(os.path.relpath(Path(p), base)).as_posix()


def microcode(mcdir, ref, expect, what="BASELINE_S2.md section 9", args=None):
    """Generate the production microcode from the COMMITTED source at <ref>; refuse on any hash mismatch (what: the name of the
    expectation, for messages only)."""
    if git("diff", "--name-only", ref, "--", *MC_DIRS):
        raise SystemExit(f"refusing: {MC_DIRS} differ from {ref} (uncommitted changes); the microcode must come from committed source")
    src = mcdir / "src"
    src.mkdir(parents=True)
    arch = subprocess.run(["git", "-C", str(ROOT), "archive", ref, *MC_DIRS], capture_output=True)
    if arch.returncode:
        raise SystemExit(f"git archive {ref} failed: {arch.stderr.decode()}")
    subprocess.run(["tar", "-x", "-C", str(src)], input=arch.stdout, check=True)
    builder = src / MC_BUILDER
    if sha(builder) != expect["builder"]:
        raise SystemExit(f"refusing: builder sha256 {sha(builder)} != {what} {expect['builder']}")
    out = mcdir / "mc"
    r = subprocess.run([sys.executable, "-B", str(builder), str(out), *(microcode_pin.DEFAULT_ARGS if args is None else args)],
                       capture_output=True, text=True, env=microcode_pin.builder_env())     # packing only from the pin's arguments, never GA2_MC_PACK
    (mcdir / "mc.log").write_text(r.stdout + r.stderr)
    if r.returncode:
        raise SystemExit("microcode generation failed (see mc.log)")
    got = {n: sha(out / n) for n in ("m1_cstore.hex", "m1_dispatch.hex", "m1_mode.hex")}
    bad = {n: (got[n], expect[n]) for n in got if got[n] != expect[n]}
    for n in got:
        print(f"microcode {n}: {got[n]} {'== ' + ('BASELINE_S2.md' if what.startswith('BASELINE') else what) if n not in bad else 'MISMATCH'}")
    if bad:
        raise SystemExit(f"refusing: microcode images differ from {what}: {bad}")
    return out, got, builder


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gen", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--template", type=Path, required=True, help="core-template/src/fpga at the pinned rev")
    ap.add_argument("--mc-ref", default="HEAD", help="commit the microcode is generated from (default HEAD)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--microcode-pin", type=Path, help="deliberate microcode change: expected hashes from this pin (tools/microcode_pin.py) instead of BASELINE_S2.md section 9")
    a = ap.parse_args()
    gen, out, t = a.gen.resolve(), a.out.resolve(), a.template.resolve()
    if out.exists():
        raise SystemExit(f"refusing: {out} exists")
    mcdir = out.parent / (out.name + ".mc")
    if mcdir.exists():
        raise SystemExit(f"refusing: {mcdir} exists")
    for f in ("d2_core.qip", "sources.json", "files_synth.f", "files_synth_vhdl.f"):
        if not (gen / f).is_file():
            raise SystemExit(f"{gen / f} missing: run gen_d2.py --config fit (or sp / spid / univ / au / au3 / rel) first")
    src = json.loads((gen / "sources.json").read_text())
    if src["config"] not in ("fit", "sp", "spid", "univ", "au", "au3", "rel"):
        raise SystemExit(f"refusing: gen-dir config is {src['config']!r}; only 'fit', 'sp', 'spid', 'univ', 'au', 'au3' and 'rel' are prepared for the device (README)")
    if src["config"] in ("au", "au3", "rel") and src.get("t80_sim"):
        raise SystemExit(f"refusing: an {src['config']} gen dir made with --t80-sim (a simulation-only list option) is not a device project input")
    # --- template: pinned rev, unmodified, same files gen_d2.py hashed
    rev = git("rev-parse", "HEAD", cwd=t)
    if rev != TEMPLATE_REV:
        raise SystemExit(f"refusing: template HEAD {rev} != pinned {TEMPLATE_REV}")
    if git("status", "--porcelain", "--untracked-files=no", cwd=t):
        raise SystemExit("refusing: template has tracked modifications")
    for f, h in src["template_sha256"].items():
        if sha(t / f) != h:
            raise SystemExit(f"refusing: template {f} differs from what gen_d2.py hashed")
    for f in ("ap_core.qsf", "apf/apf.qip", "core/core_bridge_cmd.v", "apf/apf_constraints.sdc"):
        assert (t / f).is_file(), t / f
    # --- upstream pin
    lock = [l.split() for l in (ROOT / "UPSTREAM.lock").read_text().splitlines() if l.strip() and not l.startswith("#")]
    pin = {n: c for n, _p, _u, c in lock}["s32"]
    s32_head = git("rev-parse", "HEAD", cwd=ROOT / "references/s32")
    if s32_head != pin or src["upstream_s32"] != pin:
        raise SystemExit(f"refusing: references/s32 {s32_head} / gen {src['upstream_s32']} != UPSTREAM.lock {pin}")
    s32_dirty = git("status", "--porcelain", "--untracked-files=no", cwd=ROOT / "references/s32")
    if s32_dirty:
        raise SystemExit("refusing: references/s32 has tracked modifications")
    expect = expected_hashes(a.microcode_pin)
    pin_path = pin_sha = None
    if a.microcode_pin is not None:
        pin_path = microcode_pin.pin_repo_path(a.microcode_pin, ROOT)
        pin_sha = sha(a.microcode_pin)
        if git("ls-files", "--error-unmatch", "--", pin_path, check=False) != pin_path or git("diff", "HEAD", "--name-only", "--", pin_path):
            raise SystemExit(f"refusing: the pin {pin_path} is not committed (tracked and equal to HEAD)")
        pin_tree = microcode_pin.load_pin(a.microcode_pin)["source"]["m1_functional_tree"]
        got_tree = git("rev-parse", f"{a.mc_ref}:experiments/M1-functional")
        if got_tree != pin_tree:
            raise SystemExit(f"refusing: experiments/M1-functional at {a.mc_ref} is tree {got_tree}, the pin is for {pin_tree}")
    if sha(ROOT / "references/s32/rtl/cpu/v25/s80x86/generated/microcode.bin") != expect["microcode.bin"]:
        raise SystemExit("refusing: V25 microcode.bin differs from BASELINE_S2.md section 9")
    # --- QIP: rebase every path from experiments/D2-pocket-top to the project directory
    qip_text = (gen / "d2_core.qip").read_text()
    qfiles, qmacros, qsearch = parse_qip(qip_text)
    if qmacros != ["SYNTHESIS=1"] + src["macros"]:
        raise SystemExit(f"refusing: QIP-header macros {qmacros} != gen sources.json {src['macros']}")
    synth_list = [Path(x) for x in (gen / "files_synth.f").read_text().split()]
    vhdl_list = [Path(x) for x in (gen / "files_synth_vhdl.f").read_text().split()]
    # the QIP must list exactly files_synth.f / files_synth_vhdl.f minus the template's own files
    in_qip = sorted((D2 / p).resolve().as_posix() for _d, p in qfiles)
    expected = sorted([f.resolve().as_posix() for f in synth_list + vhdl_list if not str(f).startswith(str(t))])
    if in_qip != expected:
        raise SystemExit("refusing: the QIP's files differ from files_synth.f / files_synth_vhdl.f")
    for _d, p in qfiles:
        if not (D2 / p).resolve().is_file():
            raise SystemExit(f"refusing: QIP path does not exist: {p}")
    # --- microcode (before anything is written)
    mcdir.mkdir(parents=True)
    pin_json = microcode_pin.load_pin(a.microcode_pin) if pin_path is not None else None
    mc_args = microcode_pin.builder_args(pin_json)
    mc_out, mc_hash, builder = microcode(mcdir, a.mc_ref, expect, "BASELINE_S2.md section 9" if pin_path is None else f"the pin {pin_path}", mc_args)
    # --- project
    out.mkdir(parents=True)
    shutil.copytree(t / "apf", out / "apf")
    shutil.copy2(t / "core" / "core_bridge_cmd.v", out / "core_bridge_cmd.v")
    (out / "core").mkdir()
    if src["config"] == "univ":            # the shared SDC, byte for byte, then the univ-only fragment (nothing else differs)
        (out / "core" / "core_constraints.sdc").write_bytes((HERE / "d2_core_constraints.sdc").read_bytes() + (ROOT / UNIV_SDC_FRAGMENT).read_bytes())
    elif src["config"] == "au":        # AU: the base SDC byte for byte, then the AU appendix (nothing above it changes)
        (out / "core" / "core_constraints.sdc").write_bytes((HERE / "d2_core_constraints.sdc").read_bytes() + AU_SDC.read_bytes())
    elif src["config"] == "au3":   # AU3: the base SDC, the AU appendix and the AU3 appendix, each byte for byte
        (out / "core" / "core_constraints.sdc").write_bytes((HERE / "d2_core_constraints.sdc").read_bytes() + AU_SDC.read_bytes() + AU3_SDC.read_bytes())
    elif src["config"] == "rel":   # rel: the au3 SDC, then the univ-only hold fragment (A12), each byte for byte
        (out / "core" / "core_constraints.sdc").write_bytes((HERE / "d2_core_constraints.sdc").read_bytes() + AU_SDC.read_bytes() + AU3_SDC.read_bytes()
                                                             + (ROOT / UNIV_SDC_FRAGMENT).read_bytes())
    else:
        shutil.copy2(HERE / "d2_core_constraints.sdc", out / "core" / "core_constraints.sdc")
    for src_n, dst_n in (("m1_cstore.hex", "v2_cstore.hex"), ("m1_dispatch.hex", "v2_dispatch.hex"), ("m1_mode.hex", "v2_mode.hex")):
        shutil.copy2(mc_out / src_n, out / dst_n)
    shutil.copy2(ROOT / "references/s32/rtl/cpu/v25/s80x86/generated/microcode.bin", out / "microcode.bin")
    qlines = ["# SPDX-License-Identifier: GPL-3.0-or-later",
              "# GENERATED by experiments/D2-pocket-top/pocket/prepare_quartus_d2.py from gen_d2.py's d2_core.qip: paths rebased",
              "# to this project directory. No VERILOG_MACRO / SEARCH_PATH here (Quartus rejects them in a QIP, run 40262881111):",
              "# they are QSF assignments in ga2_d2.qsf."]
    for ln in qip_text.splitlines():
        m = QIP_PATH.match(ln)
        if m:
            qlines.append(f"{m.group(1)}[file join $::quartus(qip_path) {rel_to((D2 / m.group(2)).resolve(), out)}]")
    (out / "d2_core.qip").write_text("\n".join(qlines) + "\n")
    # --- QSF
    text = (t / "ap_core.qsf").read_bytes().decode()                  # CRLF kept, d2_pocket_io keeps it
    kept = []
    for ln in text.splitlines(keepends=True):
        if any(d in ln for d in DROP):
            continue
        kept.append(ln)
    qsf = d2_pocket_io.apply("".join(kept))
    add = ["", "# ---- D2 Pocket top (prepare_quartus_d2.py) ----",
           "set_global_assignment -name VERILOG_FILE core_bridge_cmd.v",
           'set_global_assignment -name QIP_FILE d2_core.qip',
           "set_global_assignment -name ENABLE_SIGNALTAP OFF",
           "# QSF assignments (Quartus rejects them inside a QIP, run 40262881111). SYNTHESIS=1 is upstream's own line:",
           "# removes s32_v60_bus.sv's simulation-only $isunknown assertion."]
    add += [f'set_global_assignment -name VERILOG_MACRO "{m}"' for m in qmacros]
    add += ["set_global_assignment -name SEARCH_PATH ."]
    add += [f'set_global_assignment -name SEARCH_PATH "{rel_to((D2 / p).resolve(), out)}"' for p in qsearch]
    add += [f"set_global_assignment -name SEED {a.seed}"]
    full = qsf + "\n".join(add) + "\n"
    # One line-ending convention for the whole file: the template's CRLF (d2_pocket_io's appended block and the lines above
    # are LF). Line endings only; no assignment text changes (d2_project_check C6 compares after the same normalisation).
    full = full.replace("\r\n", "\n").replace("\n", "\r\n")
    (out / f"{REVISION}.qsf").write_bytes(full.encode())
    (out / f"{REVISION}.qpf").write_text(f'PROJECT_REVISION = "{REVISION}"\n')
    resolved = [(D2 / p).resolve() for _d, p in qfiles] + [out / "core_bridge_cmd.v"] + sorted((out / "apf").glob("*.v"))
    (out / "files_synth_resolved.f").write_text("\n".join(str(p) for p in resolved) + "\n")
    # --- provenance
    dirty_tracked = git("status", "--porcelain", "--untracked-files=no").splitlines()
    untracked = [l for l in git("status", "--porcelain", "--untracked-files=all", "--", "experiments", "tools").splitlines() if l.startswith("??")]
    rec = {
        "experiment": "D2", "revision": REVISION, "config": src["config"], "seed": a.seed,
        "repo": {"commit": git("rev-parse", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
                 "dirty": bool(dirty_tracked), "dirty_tracked": dirty_tracked, "untracked_in_experiments_tools": untracked},
        "template": {"rev": rev, "pinned": TEMPLATE_REV, "tracked_clean": True, "sha256": src["template_sha256"]},
        "upstream_s32": {"commit": s32_head, "pin": pin, "tracked_clean": True},
        "macros": qmacros, "search_paths": qsearch,
        "microcode": {"ref": a.mc_ref, "ref_commit": git("rev-parse", a.mc_ref), "builder": MC_BUILDER, "builder_sha256": sha(builder),
                      "images_sha256": {k.replace("m1_", "v2_"): v for k, v in mc_hash.items()},
                      "baseline_s2_expected": {k.replace("m1_", "v2_"): v for k, v in expect.items() if k.startswith("m1_")},
                      "v25_microcode_bin_sha256": expect["microcode.bin"],
                      "verified_against": "experiments/P1-pocket-top/BASELINE_S2.md section 9" if pin_path is None else pin_path},
        "gen_dir_files": {n: sha(gen / n) for n in ("d2_core.qip", "files_synth.f", "files_synth_vhdl.f", "sources.json")},
        "sdc_sha256": sha(HERE / "d2_core_constraints.sdc"),
        **({"sdc_au_append_sha256": sha(AU_SDC)} if src["config"] in ("au", "au3", "rel") else {}),
        **({"sdc_au3_append_sha256": sha(AU3_SDC)} if src["config"] in ("au3", "rel") else {}),
        "io_script_sha256": sha(HERE / "d2_pocket_io.py"),
        "source_files": {p.relative_to(ROOT).as_posix() if ROOT in p.parents else str(p): sha(p) for p in resolved},
        "project_files": {p.relative_to(out).as_posix(): sha(p) for p in sorted(out.rglob("*")) if p.is_file()},
        "note": "NOT built: no Quartus was run. project_files are hashed at preparation time (run.json excluded).",
    }
    if src["config"] in ("univ", "rel"):     # recorded only for univ / rel (the other configurations' run.json is unchanged)
        rec["sdc_univ_fragment"] = {"path": UNIV_SDC_FRAGMENT, "sha256": sha(ROOT / UNIV_SDC_FRAGMENT)}
    if pin_path is not None:
        rec["microcode"]["pin_sha256"] = pin_sha
        if "args" in pin_json:                 # recorded only for pins that carry builder arguments (older pins' run.json is unchanged)
            rec["microcode"]["builder_args"] = mc_args
    (out / "run.json").write_text(json.dumps(rec, indent=2) + "\n")
    print(f"project {out / (REVISION + '.qpf')} ({len(qfiles)} QIP files; dirty={rec['repo']['dirty']}); scratch {mcdir}; not built")


if __name__ == "__main__":
    main()
