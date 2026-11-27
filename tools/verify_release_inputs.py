#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""verify_release_inputs.py: show that a Quartus project prepared from this public repository has exactly the build inputs of the
released v0.2.0 bitstream (CI run 45907881404, seed 1), using the release's own configuration reference.

    python3 tools/verify_release_inputs.py [--project build/D2REL/seed1] [--ci-root /home/runner/work/ga2-pocket-research/ga2-pocket-research]

Run it after the build steps in README.md (gen_d2.py, then prepare_quartus_d2.py with experiments/D2-pocket-top/reference/
microcode_pin_rel_public.json). It runs tools/d2rbf_identity.py config against
experiments/D2-pocket-top/reference/d2_rel_seed1_config_reference.json (the fitted build's reference, unchanged) and requires:
  - C2 / C3 / C4 identical: the 5 recorded source hashes, the .qsf / .sdc, all 185 source files and 22 project files
    (path-embedding files compared after rebasing this checkout's root onto the CI root), including the three V2 microcode images;
  - every other difference to be one of the four documented public-repository differences, each checked here:
      1. C1 microcode.pin_sha256 / microcode.verified_against: the public pin microcode_pin_rel_public.json is used instead of the
         fitted pin. Checked: both pins are equal in every field except `source` (same builder sha256, arguments, command and the
         three image hashes).
      2. C5 experiments/M1-functional tree: the public tree differs from the fitted one (34b33084) only in research files that the
         build does not read. Checked: against fitted_m1_functional_tree.txt, only the listed sanitized files differ; the builder
         m1_m11_microcode.py and mc_pack.py are byte-identical.
      3. references/s32 is the history-free snapshot of upstream accd644 (commit 2b9c265d, branch upstream/s32-accd644), not accd644
         itself: accd644's tree (1f410d4a) with exactly one file removed, the diagnostic screenshot verif/modelsim_romboot/dump80.png
         (no build, simulation or check reads it). Accepted only when ALL of these hold: the submodule's HEAD is the snapshot
         commit, its tree is exactly SNAPSHOT_TREE (d06cd528; every other upstream file byte-identical), its work tree is clean (untracked files included), the
         superproject's gitlink is the snapshot commit, and UPSTREAM.lock has the single row s32 / references/s32 / this
         repository / the snapshot commit. Then exactly these differences are accepted, nothing else:
           - C5 git object references/s32 (the gitlink): reference accd644, here the snapshot commit;
           - C5 git object UPSTREAM.lock: the commit, URL and comments;
           - C1 run.json upstream_s32.commit and upstream_s32.pin: reference accd644, here the snapshot commit;
           - C1 run.json gen_dir_files.sources.json: after the same path rebase the identity tool uses, the file must contain the
             snapshot commit exactly once, as its "upstream_s32" line; with that one line set back to accd644 its sha256 must equal
             the reference's. Any other change to sources.json is refused.
Prints RELEASE INPUTS IDENTICAL (exit 0) or the unexplained differences (exit 1)."""
import argparse, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "experiments/D2-pocket-top/reference"
SANITIZED = {"results/r4b_identity_2026-11-04.json"}     # relative to experiments/M1-functional   # a research log: a local home path replaced by <repo>
MUST_EQUAL = {"m1_m11_microcode.py", "mc_pack.py", "mc_basebits.json"}
ACCD644 = "accd644d139eb74110668b004abe2b48d8e4b07c"          # the upstream commit the release was built from
ACCD644_TREE = "1f410d4ad0418c15c450e127df60e06bcb25ae1e"     # its git tree: the content hash of every upstream file and mode
EXCLUDED = {"verif/modelsim_romboot/dump80.png": "e9ad5b1edff04fc523b7e647f2215e2025d209dc"}   # the one file not published (a game screenshot)
SNAPSHOT_TREE = "d06cd5281ce6fc956c000136f209df99e3123348"    # = ACCD644_TREE minus EXCLUDED
SNAPSHOT = "2b9c265d6278440fcbefe107773d1dd62b257791"         # the history-free snapshot commit of SNAPSHOT_TREE (upstream/s32-accd644)
PUBLIC_URL = "https://github.com/mmnbass/openfpga-System32.git"


def git(*a):
    return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True, check=True).stdout


def snapshot_problems():
    """[] when references/s32 is exactly the snapshot of accd644's tree; otherwise the reasons."""
    sub = ["git", "-C", str(ROOT / "references/s32")]
    run = lambda *a: subprocess.run([*sub, *a], capture_output=True, text=True).stdout.strip()
    p = []
    if run("rev-parse", "HEAD") != SNAPSHOT: p.append("references/s32 HEAD is not the snapshot commit")
    if run("rev-parse", "HEAD^{tree}") != SNAPSHOT_TREE: p.append("references/s32 tree is not the snapshot tree (accd644's minus EXCLUDED)")
    if run("status", "--porcelain", "--untracked-files=all"): p.append("references/s32 work tree is not clean")
    if git("ls-tree", "HEAD", "references/s32").split()[2:3] != [SNAPSHOT]: p.append("the gitlink is not the snapshot commit")
    rows = [l.split() for l in (ROOT / "UPSTREAM.lock").read_text().splitlines() if l.strip() and not l.startswith("#")]
    if rows != [["s32", "references/s32", PUBLIC_URL, SNAPSHOT]]: p.append("UPSTREAM.lock is not the single snapshot row")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="build/D2REL/seed1")
    ap.add_argument("--ci-root", default="/home/runner/work/ga2-pocket-research/ga2-pocket-research")
    a = ap.parse_args()
    bad = []
    P = "experiments/D2-pocket-top"
    src = [f"{P}/rtl/mf_pll_ga2.v", f"{P}/pocket/d2_pocket_io.py", f"{P}/pll_plan.py", f"{P}/pocket/d2_core_constraints.sdc", f"{P}/pocket/d2_timing.tcl"]
    import hashlib
    sha_txt = ROOT / "build/verify_source_sha256.txt"; sha_txt.parent.mkdir(exist_ok=True)
    sha_txt.write_text("".join(f"{hashlib.sha256((ROOT / s).read_bytes()).hexdigest()}  {s}\n" for s in src))
    r = subprocess.run([sys.executable, "-B", str(ROOT / "tools/d2rbf_identity.py"), "config", "--reference", str(REF / "d2_rel_seed1_config_reference.json"),
                        "--root", str(ROOT), "--project", a.project, "--rebase-root", str(ROOT), a.ci_root, "--source-sha", str(sha_txt),
                        "--out", str(ROOT / "build/verify_cfg.json"), "--text", str(ROOT / "build/verify_cfg.txt")], capture_output=True, text=True, cwd=ROOT)
    text = (ROOT / "build/verify_cfg.txt").read_text() if (ROOT / "build/verify_cfg.txt").exists() else r.stdout + r.stderr
    for need in ("C2 source_sha256.txt: 5 lines identical", "C4 185 source files and 22 project files re-hashed from disk: identical"):
        if need not in text: bad.append(f"missing: {need}")
    fails = [l.strip() for l in text.splitlines() if l.strip().startswith("FAIL")]
    snap = snapshot_problems()
    ref_sources = json.loads((REF / "d2_rel_seed1_config_reference.json").read_text())["run_json"]["gen_dir_files"]["sources.json"]
    for f in fails:
        if re.match(r"FAIL: C1 run\.json differs at microcode\.(pin_sha256|verified_against):", f):
            fp = json.loads((REF / "microcode_pin_rel.json").read_text()); pp = json.loads((REF / "microcode_pin_rel_public.json").read_text())
            if {k: v for k, v in fp.items() if k != "source"} != {k: v for k, v in pp.items() if k != "source"}: bad.append(f + "  [public pin differs beyond `source`]")
        elif f.startswith("FAIL: C5 git object experiments/M1-functional:"):
            fitted = {l.split("\t")[1]: l.split()[2] for l in (REF / "fitted_m1_functional_tree.txt").read_text().splitlines() if l.strip()}
            now = {l.split("\t")[1]: l.split()[2] for l in git("ls-tree", "-r", "HEAD:experiments/M1-functional").splitlines() if l.strip()}
            diff = {p for p in set(fitted) | set(now) if fitted.get(p) != now.get(p)}
            if diff - SANITIZED: bad.append(f + f"  [unexplained M1-functional differences: {sorted(diff - SANITIZED)}]")
            if any(fitted.get(p) != now.get(p) for p in MUST_EQUAL): bad.append("builder files differ from the fitted tree")
        elif f.startswith("FAIL: C5 git object UPSTREAM.lock:"):
            if snap: bad.append(f + f"  [{'; '.join(snap)}]")
        elif f == f"FAIL: C5 git object references/s32: HEAD has {SNAPSHOT}, reference {ACCD644}":
            if snap: bad.append(f + f"  [{'; '.join(snap)}]")
        elif f in (f"FAIL: C1 run.json differs at upstream_s32.{k}: reference '{ACCD644}', got '{SNAPSHOT}'" for k in ("commit", "pin")):
            if snap: bad.append(f + f"  [{'; '.join(snap)}]")
        elif f.startswith("FAIL: C1 run.json differs at gen_dir_files.sources.json:"):
            import hashlib as _h
            proj = Path(a.project) if Path(a.project).is_absolute() else ROOT / a.project
            b = (proj.parent / "gen" / "sources.json").read_bytes()          # where d2rbf_identity.py reads it
            b = b.replace(str(ROOT).encode(), a.ci_root.encode())
            line = f'"upstream_s32": "{SNAPSHOT}"'.encode()
            if snap or b.count(SNAPSHOT.encode()) != 1 or b.count(line) != 1 or \
               _h.sha256(b.replace(line, f'"upstream_s32": "{ACCD644}"'.encode())).hexdigest() != ref_sources:
                bad.append(f + "  [sources.json differs beyond the upstream_s32 commit line]")
        elif f == "D2RBF CONFIGURATION IDENTITY FAIL" or f.startswith("FAIL: D2RBF"):
            continue
        else:
            bad.append(f)
    print("\n".join(l for l in text.splitlines() if re.match(r"\s*(C2|C3|C4)", l)))
    if bad:
        print("UNEXPLAINED DIFFERENCES:\n  " + "\n  ".join(bad)); print("RELEASE INPUTS DIFFER"); return 1
    print(f"upstream: references/s32 = snapshot {SNAPSHOT[:8]}, tree {SNAPSHOT_TREE[:8]} == accd644's {ACCD644_TREE[:8]} minus {sorted(EXCLUDED)} "
          "(every other upstream file byte-identical)")
    print(f"documented public-repository differences: {len(fails)} (public pin identity, experiments/M1-functional research file(s) {sorted(SANITIZED)}, "
          "upstream snapshot commit in the gitlink, UPSTREAM.lock, run.json upstream_s32 and sources.json)")
    print("RELEASE INPUTS IDENTICAL"); return 0


if __name__ == "__main__":
    sys.exit(main())
