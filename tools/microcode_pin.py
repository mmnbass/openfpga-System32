#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Microcode pin: the identity of one set of V2 microcode images, as a small committed JSON file.

Background: prepare_quartus_d2.py refuses to build a D2 project unless the production microcode (experiments/M1-functional/
m1_m11_microcode.py <out> --reset-native) reproduces the sha256 of the three images, of the builder and of upstream's V25
microcode.bin that experiments/P1-pocket-top/BASELINE_S2.md section 9 records. A DELIBERATE microcode change (new M1-functional
commit) must therefore be introduced through a pin, never by weakening that check:

  tools/microcode_pin.py make  --commit C --out experiments/D2-pocket-top/reference/microcode_pin_<name>.json [--repo .] [--scratch DIR]
      git-archives the COMMITTED sources (experiments/M1-functional, experiments/V2-datapath-floor, tools: exactly what
      prepare_quartus_d2.py archives) at C into a fresh scratch directory (default <repo>/build/microcode_pin/<C12>, must not
      exist), runs the builder exactly as prepare_quartus_d2.py does, and writes the pin. The V25 microcode.bin value is taken from
      BASELINE_S2.md section 9 (the V25 is unchanged by a microcode change) and checked against references/s32 when present.
  tools/microcode_pin.py check --pin F [--repo .] [--commit C]
      validates the pin's structure, its V25 value against BASELINE_S2.md, and (repo files) the builder sha256 and the M1-functional
      tree against the repository's HEAD (or --commit C); with --commit C it also REBUILDS the images from C and compares them.

Pin fields (required, no others except `args`): schema (1), what, source {repo_commit, m1_functional_tree}, builder (path), builder_sha256,
command, images_sha256 {m1_cstore.hex, m1_dispatch.hex, m1_mode.hex}, v25_microcode_bin_sha256, word_counts (informational).
Optional `args` (list): the EXACT builder arguments after <out>. Absent = ["--reset-native"] (the pins mc2 / s2test; their files and
behaviour are unchanged). Allowed values (ALLOWED_ARGS, anything else is refused): ["--reset-native"],
["--reset-native", "--mc-pack=tail,zero"] (MC-compact packing, needed by R-play1) and, on release/s32-v1-ro2 / -ro3,
["--reset-native", "--mc-pack=tail,zero", "--arith=mul,div"] (the S1 multiply / divide routine; `make --mc-pack tail,zero --arith mul,div`).
An ambient C1_GEN is refused (builder_env), so only the pin's arguments select the arithmetic routine. `command` must then equal
"m1_m11_microcode.py <out> " + the arguments. The builder is always run WITHOUT GA2_MC_PACK in its environment, so the pin's arguments
are the only way packing is selected. `make --mc-pack tail,zero` writes such a pin.
prepare_quartus_d2.py --microcode-pin F expects exactly these hashes (and refuses unless the M1-functional tree at --mc-ref is the
pin's); d2_project_check.py --microcode-pin F checks the project against them. Deletes nothing.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

SCHEMA = 1
MC_DIRS = ["experiments/M1-functional", "experiments/V2-datapath-floor", "tools"]          # = prepare_quartus_d2.py MC_DIRS
MC_BUILDER = "experiments/M1-functional/m1_m11_microcode.py"                                 # = prepare_quartus_d2.py MC_BUILDER
IMAGES = ("m1_cstore.hex", "m1_dispatch.hex", "m1_mode.hex")
PIN_KEYS = {"schema", "what", "source", "builder", "builder_sha256", "command", "images_sha256", "v25_microcode_bin_sha256", "word_counts"}
PIN_KEYS_OPTIONAL = {"args"}
DEFAULT_ARGS = ["--reset-native"]
# fit/s32-dmap SN (SegaSonic integration, 2026-11-23): the fourth list selects every ported switch of research/v60-sonic-cpu1 (S1 to S3-C, indexed
# JSR, MOVCFU) with the pack search budget those images were built with; `make --mc-pack tail,zero --gen <GEN_SONIC> --pack-budget 20000`.
# BS (2026-11-24): + bs = MOVBSU / MOVBSD (research/v60-bs); the list without it is no longer accepted.
# CC (2026-11-24): + cc = CMPC.B / CMPC.H (research/v60-cc); ceiling 2,000 words (Michael).
GEN_SONIC = "var,dbcc,cv,mul,div,addc,bit,sh,am,rv,sf,xc,pp,gp,js,up,ld,st,jx,mf,bs,cc"
ALLOWED_ARGS = (["--reset-native"], ["--reset-native", "--mc-pack=tail,zero"],
                ["--reset-native", "--mc-pack=tail,zero", "--arith=mul,div"],
                ["--reset-native", "--mc-pack=tail,zero", f"--gen={GEN_SONIC}", "--pack-budget=20000"])   # closed: anything else is refused (fail closed)
# release/s32-v1-ro2: the third list selects the S1 multiply / divide routine (research/v60-s1md); the builder honours no environment switch for it.
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
COMMAND_PREFIX = "m1_m11_microcode.py <out> "
COMMAND = COMMAND_PREFIX + "--reset-native"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def git(repo, *a):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"git {' '.join(a)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def _prep():
    """prepare_quartus_d2 (lazy: it imports this module)."""
    sys.path.insert(0, str(ROOT / "experiments/D2-pocket-top/pocket"))
    import prepare_quartus_d2 as prep
    return prep


def baseline_v25():
    return _prep().expected_hashes()["microcode.bin"]


def load_pin(path):
    """-> the validated pin dict. Fail closed on a missing file, bad JSON, wrong schema, missing/unknown keys, malformed hashes."""
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"refusing: microcode pin {p} does not exist")
    try:
        pin = json.loads(p.read_text())
    except ValueError as e:
        raise SystemExit(f"refusing: microcode pin {p} is not JSON ({e})")
    if not isinstance(pin, dict) or pin.get("schema") != SCHEMA:
        raise SystemExit(f"refusing: microcode pin {p}: schema is not {SCHEMA}")
    if not (PIN_KEYS <= set(pin) <= PIN_KEYS | PIN_KEYS_OPTIONAL):
        raise SystemExit(f"refusing: microcode pin {p}: keys differ (missing {sorted(PIN_KEYS - set(pin))}, unknown {sorted(set(pin) - PIN_KEYS - PIN_KEYS_OPTIONAL)})")
    if "args" in pin and pin["args"] not in ALLOWED_ARGS:
        raise SystemExit(f"refusing: microcode pin {p}: args {pin['args']!r} are not one of the allowed builder argument lists {ALLOWED_ARGS}")
    src = pin["source"]
    if not (isinstance(src, dict) and set(src) == {"repo_commit", "m1_functional_tree"}
            and all(isinstance(v, str) and HEX40.match(v) for v in src.values())):
        raise SystemExit(f"refusing: microcode pin {p}: source must be {{repo_commit, m1_functional_tree}} (40-hex each)")
    if pin["builder"] != MC_BUILDER:
        raise SystemExit(f"refusing: microcode pin {p}: builder {pin['builder']!r} != {MC_BUILDER!r}")
    if pin["command"] != command_for(builder_args(pin)):
        raise SystemExit(f"refusing: microcode pin {p}: command {pin['command']!r} != {command_for(builder_args(pin))!r}")
    im = pin["images_sha256"]
    if not (isinstance(im, dict) and set(im) == set(IMAGES) and all(isinstance(v, str) and HEX64.match(v) for v in im.values())):
        raise SystemExit(f"refusing: microcode pin {p}: images_sha256 must hold exactly {list(IMAGES)} as 64-hex sha256")
    for k in ("builder_sha256", "v25_microcode_bin_sha256"):
        if not (isinstance(pin[k], str) and HEX64.match(pin[k])):
            raise SystemExit(f"refusing: microcode pin {p}: {k} is not a 64-hex sha256")
    wc = pin["word_counts"]
    if not (isinstance(wc, dict) and set(wc) == set(IMAGES) and all(isinstance(v, int) and v >= 0 for v in wc.values())):
        raise SystemExit(f"refusing: microcode pin {p}: word_counts must hold exactly {list(IMAGES)} as non-negative integers")
    return pin


def builder_args(pin):
    """The builder arguments after <out> a (validated or not yet validated) pin prescribes: its `args`, else ["--reset-native"]."""
    return list(pin["args"]) if isinstance(pin, dict) and "args" in pin else list(DEFAULT_ARGS)


def command_for(args):
    return COMMAND_PREFIX + " ".join(args)


def builder_env():
    """The environment of the builder run: the caller's without GA2_MC_PACK (packing comes ONLY from the pin's arguments).
    release/s32-v1-ro2: an ambient C1_GEN (the research arithmetic switch) is refused, never passed or silently dropped."""
    if "C1_GEN" in os.environ or "MC_PACK_BUDGET" in os.environ:
        raise SystemExit("refusing: C1_GEN / MC_PACK_BUDGET is set in the environment; microcode selection comes only from the pin's builder arguments")
    return {k: v for k, v in os.environ.items() if k != "GA2_MC_PACK"}


def pin_repo_path(path, root):
    """The pin's path relative to the repository root (it must be inside the repository: run.json records it)."""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        raise SystemExit(f"refusing: microcode pin {path} is not inside the repository {root}")


def generate_images(repo, commit, scratch, args=None):
    """git-archive MC_DIRS at <commit> of <repo> into <scratch>/src and run the builder exactly as prepare_quartus_d2.microcode()
    does. -> (out dir, {image: sha256}, builder path)."""
    scratch = Path(scratch)
    src = scratch / "src"
    src.mkdir(parents=True)
    arch = subprocess.run(["git", "-C", str(repo), "archive", commit, *MC_DIRS], capture_output=True)
    if arch.returncode:
        raise SystemExit(f"git archive {commit} failed: {arch.stderr.decode()}")
    subprocess.run(["tar", "-x", "-C", str(src)], input=arch.stdout, check=True)
    builder = src / MC_BUILDER
    out = scratch / "mc"
    r = subprocess.run([sys.executable, "-B", str(builder), str(out), *(DEFAULT_ARGS if args is None else args)], capture_output=True, text=True,
                       env=builder_env())
    (scratch / "mc.log").write_text(r.stdout + r.stderr)
    if r.returncode:
        raise SystemExit(f"microcode generation failed (see {scratch / 'mc.log'})")
    return out, {n: sha(out / n) for n in IMAGES}, builder


def words(p):
    return sum(1 for l in Path(p).read_text().splitlines() if l.strip() and not l.lstrip().startswith("//"))


def make(a):
    repo = Path(a.repo).resolve()
    commit = git(repo, "rev-parse", "--verify", a.commit + "^{commit}")
    scratch = Path(a.scratch) if a.scratch else repo / "build/microcode_pin" / commit[:12]
    if scratch.exists():
        raise SystemExit(f"refusing: scratch {scratch} exists (use a fresh --scratch)")
    out_p = Path(a.out)
    if out_p.exists():
        raise SystemExit(f"refusing: {out_p} exists")
    v25 = baseline_v25()
    f = repo / "references/s32/rtl/cpu/v25/s80x86/generated/microcode.bin"
    if f.is_file() and sha(f) != v25:
        raise SystemExit(f"refusing: references/s32 V25 microcode.bin {sha(f)} != BASELINE_S2.md section 9 {v25}")
    args = ["--reset-native", f"--mc-pack={a.mc_pack}"] if a.mc_pack else list(DEFAULT_ARGS)
    if a.arith:
        args.append(f"--arith={a.arith}")
    if a.gen:
        args.append(f"--gen={a.gen}")
    if a.pack_budget:
        args.append(f"--pack-budget={a.pack_budget}")
    if args not in ALLOWED_ARGS:
        raise SystemExit(f"refusing: builder arguments {args} are not one of {ALLOWED_ARGS}")
    out, got, builder = generate_images(repo, commit, scratch, args)
    pin = {
        "schema": SCHEMA,
        "what": "Microcode pin for the D2 d2rbf build: sha256 of the V2 microcode images produced by the builder below at "
                "source.repo_commit (experiments/M1-functional, --reset-native); see tools/microcode_pin.py",
        "source": {"repo_commit": commit, "m1_functional_tree": git(repo, "rev-parse", f"{commit}:experiments/M1-functional")},
        "builder": MC_BUILDER, "builder_sha256": sha(builder), "command": command_for(args),
        "images_sha256": got, "v25_microcode_bin_sha256": v25,
        "word_counts": {n: words(out / n) for n in IMAGES},
    }
    if a.mc_pack or a.arith or a.gen or a.pack_budget:
        pin["args"] = args                     # absent for the default (reset-native) so that pins made before this field stay as they were
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(pin, indent=1, sort_keys=True) + "\n")
    load_pin(out_p)
    print(f"pin written: {out_p} (commit {commit[:9]}, M1-functional {pin['source']['m1_functional_tree'][:9]}, scratch {scratch})")
    for n in IMAGES:
        print(f"  {n}: {got[n]} ({pin['word_counts'][n]} words)")
    print(f"  builder sha256 {pin['builder_sha256']}\n  V25 microcode.bin {v25} (== BASELINE_S2.md section 9)")
    return 0


def check(a):
    repo = Path(a.repo).resolve()
    pin = load_pin(a.pin)
    fails = []
    if pin["v25_microcode_bin_sha256"] != baseline_v25():
        fails.append("v25_microcode_bin_sha256 != BASELINE_S2.md section 9")
    ref = a.commit or "HEAD"
    commit = git(repo, "rev-parse", "--verify", ref + "^{commit}")
    bshow = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{MC_BUILDER}"], capture_output=True)
    if bshow.returncode or hashlib.sha256(bshow.stdout).hexdigest() != pin["builder_sha256"]:
        fails.append(f"builder sha256 at {commit[:9]} != pin")
    tree = git(repo, "rev-parse", f"{commit}:experiments/M1-functional")
    if tree != pin["source"]["m1_functional_tree"]:
        fails.append(f"experiments/M1-functional tree at {commit[:9]} is {tree}, pin has {pin['source']['m1_functional_tree']}")
    if a.commit:
        scratch = Path(a.scratch) if a.scratch else repo / "build/microcode_pin" / ("check_" + commit[:12])
        if scratch.exists():
            raise SystemExit(f"refusing: scratch {scratch} exists (use a fresh --scratch)")
        out, got, _b = generate_images(repo, commit, scratch, builder_args(pin))
        for n in IMAGES:
            if got[n] != pin["images_sha256"][n]:
                fails.append(f"rebuilt {n} {got[n]} != pin {pin['images_sha256'][n]}")
            if words(out / n) != pin["word_counts"][n]:
                fails.append(f"rebuilt {n} has {words(out / n)} words, pin {pin['word_counts'][n]}")
    for f in fails:
        print("FAIL  " + f)
    print(f"MICROCODE PIN CHECK {'FAIL' if fails else 'PASS'} ({a.pin}" + (f", rebuilt from {commit[:9]}" if a.commit else ", repository files only") + ")")
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    m = sub.add_parser("make")
    m.add_argument("--commit", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--repo", default=str(ROOT))
    m.add_argument("--scratch")
    m.add_argument("--mc-pack", help="MC-compact packing mode passed to the builder (allowed: tail,zero); recorded in the pin's `args`")
    m.add_argument("--gen", help="switch list passed to the builder as --gen=<v> (allowed: GEN_SONIC; with --mc-pack tail,zero --pack-budget 20000)")
    m.add_argument("--pack-budget", help="pack search budget passed to the builder as --pack-budget=<n> (allowed: 20000, with --gen)")
    m.add_argument("--arith", help="arithmetic routine selector passed to the builder as --arith=<v> (allowed: mul,div; with --mc-pack tail,zero)")
    c = sub.add_parser("check")
    c.add_argument("--pin", required=True)
    c.add_argument("--repo", default=str(ROOT))
    c.add_argument("--commit")
    c.add_argument("--scratch")
    x = ap.parse_args(argv)
    return make(x) if x.mode == "make" else check(x)


if __name__ == "__main__":
    sys.exit(main())
