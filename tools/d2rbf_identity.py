#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration identity and result identity of the d2rbf job against the timing-closed D2 run (CI run 42135281517, research
commit 47406e3, seed 1). Pure Python, never runs Quartus, deletes nothing.

The reference is a committed JSON manifest, built from the preserved evidence of that run by `make-reference` (so every value in it
can be traced to a file of the evidence directory, whose own SHA256SUMS is verified first):

  d2rbf_identity.py make-reference --evidence DIR --repo . --repo-commit 47406e3 --out experiments/D2-pocket-top/reference/<name>.json
      DIR = .../artifacts/d2-reports/seed1 of the evidence (parent directories hold SHA256SUMS). Reads run.json, source_sha256.txt,
      ga2_d2.qsf, core_constraints.sdc, quartus_version.txt, template_rev.txt, ga2_d2.fit.summary, ga2_d2.sta.summary, d2_timing.log,
      pll_usage_summary.txt, and `git rev-parse <commit>:<path>` for the git objects listed in GIT_OBJECTS.

  d2rbf_identity.py config --reference REF --root . --project build/D2/seed1 --source-sha reports/.../source_sha256.txt --out J --text T
      BEFORE any Quartus step. FAILs unless the freshly generated project is the reference's project:
        C1 run.json (the generator's provenance record) equals the reference's, field by field, except EXCLUDED_RUN_JSON (below);
        C2 source_sha256.txt (the five D2 input files) equals the reference's, line by line;
        C3 ga2_d2.qsf and core/core_constraints.sdc sha256 equal the reference's;
        C4 every file hashed by run.json is RE-HASHED FROM DISK (source_files: the QIP's sources + core_bridge_cmd.v + apf/*.v;
           project_files: all 22 project files incl. QSF, QIP, SDC, the three microcode images, microcode.bin) and equals the
           reference; the project directory holds exactly those files + run.json; every file the QIP lists is among them;
        C5 git object ids of the inputs (the six source trees, the generator / project scripts, UPSTREAM.lock, toolchain.json,
           ci_quartus.sh, the s32 gitlink, the gate scripts) at the checked-out commit equal the ones at 47406e3.
      Without --project/--root the disk checks C4/C5 are reported as NOT CHECKED (used only against the evidence itself).
      Four of the hashed files embed the absolute workspace path of the runner (files_synth.f, files_synth_vhdl.f, sources.json in
      the gen dir; files_synth_resolved.f in the project): /home/runner/work/ga2-pocket-research/ga2-pocket-research. On the CI runner
      they match as they are (a different checkout path would fail closed). `--rebase-root FROM TO` rewrites FROM -> TO in those four
      files before hashing; it exists ONLY to validate this check on a local reproduction (the workflow never passes it; tests enforce that).

  d2rbf_identity.py result --reference REF --fit-summary F --sta-summary S --timing-log L --pll-usage P --out J --text T
      After quartus_sta and the D2 gate, BEFORE the assembler. FAILs unless, EXACTLY (the default; --slack-tol only for analysis):
        R1 the fit summary (resources, device, Quartus version; the fitter-status timestamp is the only masked text) is identical;
        R2 every Timing Analyzer summary row (type, corner, clock, slack, TNS) is identical;
        R3 every PLLT_* row of d2_timing.log (clocks, internal per-clock per-corner slack, I/O group slack) is identical;
        R4 the PLL usage summary is identical.

  d2rbf_identity.py compare --reference REF --fit-summary F --sta-summary S --timing-log L --pll-usage P [--pll-delta spid] --out J --text T
      THE WORKFLOW'S RESULT STEP (owner decision 2026-11-05: no exact slack-row equality across separate Quartus runs; the fresh
      run must pass the D2 gate instead). After quartus_sta and the D2 gate, BEFORE the assembler:
        P1 STRICT (exit 1 on any difference): the PLL parameters of the PLL usage summary (type, feedback, bandwidth, reference
           clock and source, VCO, operation mode, lock range, fractional division, M, N, and per output counter: frequency, duty,
           phase, C counter, odd/even duty, PRST) equal the timing-closed run's; only placement ("... Location") lines are ignored.
           --pll-delta spid (the spid job only): the same, against the timing-closed run with the closed, pinned PLL_DELTAS["spid"]
           applied, and the PLL block location must be PLL_PINNED_LOCATION["spid"].
        INFORMATIONAL (never fails): resources (ALMs, registers, M10K, DSP, block bits, pins, PLLs) and every per-clock per-corner
           internal setup/hold/recovery/removal worst slack and I/O-group slack vs the timing-closed run, with deltas; a difference
           is flagged MEANINGFUL when a slack moves by more than 0.10 ns or changes sign, or a resource moves by more than 1 %.

  d2rbf_identity.py derive-reference --base REF --pin PIN --project build/D2/seed1 --root . --source-sha F [--rebase-root FROM TO] [--delta-spec SPEC] --out NEW
      FOR A DELIBERATE MICROCODE CHANGE (tools/microcode_pin.py): writes a CONFIGURATION reference that equals the base reference in
      every field except the closed, enumerated ALLOWED DELTA below, and fails (writes nothing) if anything outside it differs, if the
      values inside it are not exactly the pin's / the repository's, or if `config` (C1-C5, strict, unchanged) does not pass against the
      new reference for the given project. The new reference records derived_from (base path, sha256, source run), microcode_pin (path,
      sha256) and the full delta (key, old, new); its `result` section is the base's, unchanged: the compare step stays against the
      original timing-closed run. ALLOWED DELTA (nothing else):
        git_objects: experiments/M1-functional; experiments/D2-pocket-top/pocket/prepare_quartus_d2.py (changed by --microcode-pin);
                     experiments/D2-pocket-top/pocket/d2_project_check.py and tools/microcode_pin.py (ADDED: scripts of this change)
        run_json.microcode: builder_sha256; images_sha256.v2_{cstore,dispatch,mode}.hex; baseline_s2_expected.v2_{cstore,dispatch,mode}.hex;
                     verified_against (the pin's repository path); pin_sha256 (ADDED)
        run_json.project_files: v2_cstore.hex, v2_dispatch.hex, v2_mode.hex
      Everything else (all RTL trees, the 173 source_files, QSF / SDC sha, source_sha256 lines, template, upstream, macros, microcode.bin,
      the path-embedding files after --rebase-root, the other project files) must be byte-identical to the base.
      That is the DEFAULT delta (MC2_SPEC, the variant of pin mc2). A variant with further deliberate differences (e.g. RTL) passes
      --delta-spec SPEC, a committed JSON file (experiments/D2-pocket-top/reference/delta_<name>.json) that lists EXACTLY which git
      objects, run.json keys (incl. `source_files.<path>`) and which files inside a git tree may differ: see load_spec for its
      format. The spec is checked against a closed UNIVERSE in this file (UNIVERSE_*: nothing outside it can ever be allowed: QSF, SDC,
      PLL file, upstream, template, microcode.bin, the gate scripts, ...), must be committed and equal to HEAD, and is recorded in the
      derived reference (delta_spec: path, sha256, name) so that `config` fails if the spec file is later changed. Pin and spec
      checks are as for the default: values inside the delta must be the pin's / the repository's.

  d2rbf_identity.py self-reference --base REF --pin PIN --project build/D2SP/seed1 --root . --source-sha F --config sp [--seed N] [--rebase-root FROM TO] --out NEW
      FOR A STRUCTURALLY NEW CONFIGURATION (GA2Play2, gen_d2.py --config sp: a different compiled source set, so it is NOT a delta of
      the timing-closed run and derive-reference cannot express it). Writes a CONFIGURATION reference whose run.json / source lists /
      project files / git objects are THIS project's and THIS commit's (a self-reference: in CI, `config` then proves the job builds
      exactly the project that was generated, simulated and checked locally at the same commit), and fails (writes nothing) unless:
        S1 the repository is clean (run.json repo.dirty / dirty_tracked / untracked empty) and repo.commit == microcode.ref_commit == HEAD;
        S2 run.json config == --config ("sp"; "spid" = SpidPlay1, experiments/SM1-spidman; "univ" = U1b, experiments/U1-universal;
           "au" or "au3"; "rel" = release/s32-v1, experiments/U1-universal rel_derive.py) and the project's seed == --seed (default 1; 1, 2 or 3);
        S3 the microcode values are exactly the pin's (images, builder, builder_args, pin path / sha256, V25 microcode.bin);
        S4 FROZEN vs the timing-closed base (byte for byte; the QSF modulo its single SEED line for seeds 2/3; the safeguard that no constraint, PLL, I/O, toolchain or fitter setting
           changed with the new source set): QSF sha256, SDC sha256, the five source_sha256 lines (PLL file, I/O script, PLL plan,
           SDC, timing tcl), run.json template / upstream_s32 / macros / search_paths / sdc_sha256 / io_script_sha256, the project
           files apf/*, core_bridge_cmd.v, core/core_constraints.sdc, ga2_d2.qsf, ga2_d2.qpf, microcode.bin, the source-file hash of
           rtl/mf_pll_ga2.v, and every git object of GIT_OBJECTS outside UNIVERSE_GIT (other RTL trees, PLL plan, I/O script, SDC,
           timing tcl, toolchain.json, ci_quartus.sh, BASELINE_S2.md, UPSTREAM.lock, the s32 gitlink, d2_gate.py, pll_gate.py);
           For --config univ (U1b, Michael 2026-11-14: "a univ-only fitter hold-optimization margin, not an RTL change") the core SDC is
           frozen as exactly the base's SDC bytes followed by ONE closed, pinned fragment, SDC_FRAGMENTS["univ"] (the fitter-only clk_sys ->
           clk_ram hold margin A12): the shared SDC file on disk must hash to the base's sdc_sha256, the fragment file to the pinned sha256,
           run.json sdc_univ_fragment must be that {path, sha256}, and the project's core/core_constraints.sdc (S4 sdc_sha256 and its
           project-file hash) must be their concatenation. run.json sdc_sha256 (the shared file) stays frozen to the base. Nothing else
           of S4 changes, and no other configuration may carry a fragment.
        S5 `config` (C1-C5, strict, unchanged) passes against the new reference for the given project.
      git_objects = GIT_OBJECTS + SELF_EXTRA_GIT[config] (sp: the SP RTL tree; spid: it and the SM1-spidman RTL tree; univ: those two, the
      U1 RTL tree, the U1 game files, experiments/U1-universal/games, from which the run-time configuration files are made, and the
      univ-only SDC fragment) at HEAD. `result` is the base's (the compare step stays against the
      AU (exp/audio1, --config au): S4 is the same freeze with ONE difference, stated exactly: the project's core/core_constraints.sdc
      must be the base's SDC BYTE FOR BYTE (its sha256 = the base's sdc_sha256, checked on that prefix) followed by exactly the committed
      pocket/d2_au_constraints.sdc (recorded as au_sdc_append {path, sha256}; run.json sdc_au_append_sha256 must equal it); every other
      S4 item, incl. the QSF, run.json sdc_sha256 and pocket/d2_core_constraints.sdc's git object, is frozen as for sp. The appendix
      and the AU RTL are added git objects (SELF_EXTRA_GIT_AU), not frozen ones.
      AU3 (exp/audio3, --config au3): as AU with the SDC = the base SDC byte for byte + pocket/d2_au_constraints.sdc + pocket/d2_au3_constraints.sdc
      (recorded as au_sdc_append and au3_sdc_append; run.json sdc_au_append_sha256 / sdc_au3_append_sha256 must equal them); both
      appendices and both I/O report scripts are added git objects (SELF_EXTRA_GIT_AU3).
      REL (release/s32-v1, --config rel): the core SDC = the base SDC byte for byte + pocket/d2_au_constraints.sdc + pocket/d2_au3_constraints.sdc
      + the pinned A12 fragment SDC_FRAGMENTS["rel"] (the same bytes as univ's), each byte for byte, in that order; the appendices are
      recorded as au3's are, the fragment as univ's (run.json sdc_univ_fragment); the git objects are au3's plus univ's.
      `result` is the base's (the compare step stays against the
      timing-closed run: PLL strict, resources / slacks informational). Recorded: self_reference {config, repo_commit, base path /
      sha256, frozen keys}. Any later commit that changes one of the hashed files or git objects invalidates it (`config` fails closed).

EXCLUDED_RUN_JSON, and why (everything else in run.json is compared):
  repo.commit          the job runs at a later commit (this tooling sits on top of 47406e3); replaced by C5, which proves every
                       input object is the one of 47406e3, and by C4, which re-hashes the files.
  repo.branch          not an input; the push branch is enforced by tools/ci_request.py.
  microcode.ref_commit = `git rev-parse HEAD` again (checked equal to repo.commit); the builder and image hashes ARE compared.
  (repo.dirty, repo.dirty_tracked and repo.untracked_in_experiments_tools are compared and must be clean; no timestamps are in run.json.)
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

EXCLUDED_RUN_JSON = ("repo.commit", "repo.branch", "microcode.ref_commit")
PATH_EMBEDDING = (("gen_dir_files", "files_synth.f", "gen"), ("gen_dir_files", "files_synth_vhdl.f", "gen"),
                  ("gen_dir_files", "sources.json", "gen"), ("project_files", "files_synth_resolved.f", None))
FITTER_STATUS = re.compile(r"^(Fitter Status : \S+) - .*$")
EVIDENCE_FILES = ("run.json", "source_sha256.txt", "ga2_d2.qsf", "core_constraints.sdc", "quartus_version.txt", "template_rev.txt",
                  "ga2_d2.fit.summary", "ga2_d2.sta.summary", "d2_timing.log", "pll_usage_summary.txt")
ALLOWED_GIT_DELTA = ("experiments/M1-functional", "experiments/D2-pocket-top/pocket/prepare_quartus_d2.py",
                     "experiments/D2-pocket-top/pocket/d2_project_check.py", "tools/microcode_pin.py")
HEX_NAMES = ("v2_cstore.hex", "v2_dispatch.hex", "v2_mode.hex")
ALLOWED_RUN_DELTA = (("microcode.builder_sha256", "microcode.verified_against", "microcode.pin_sha256")
                     + tuple(f"microcode.images_sha256.{n}" for n in HEX_NAMES) + tuple(f"microcode.baseline_s2_expected.{n}" for n in HEX_NAMES)
                     + tuple(f"project_files.{n}" for n in HEX_NAMES))
ABSENT = "<absent>"
PIN_ARGS_KEY = "microcode.builder_args"
# The closed universe of what ANY delta spec may allow (a spec lists a subset; anything else is refused at load time).
UNIVERSE_GIT = ("experiments/M1-functional", "experiments/D2-pocket-top/rtl", "experiments/D2-pocket-top/make_d2_derived.py",
                "experiments/D2-pocket-top/gen_d2.py",        # play1r2: the generator that puts the derived sprite file into the source lists
                "experiments/D2-pocket-top/pocket/prepare_quartus_d2.py", "experiments/D2-pocket-top/pocket/d2_project_check.py",
                "tools/microcode_pin.py")
UNIVERSE_RUN_KEYS = (("microcode.builder_sha256", "microcode.verified_against", "microcode.pin_sha256", PIN_ARGS_KEY)
                     + tuple(f"microcode.images_sha256.{n}" for n in HEX_NAMES) + tuple(f"microcode.baseline_s2_expected.{n}" for n in HEX_NAMES)
                     + tuple(f"project_files.{n}" for n in HEX_NAMES))
# play1r2: the source lists that name the compiled files (their content changes with the one swapped file; run.json hashes them)
SRCLIST_KEYS = ("gen_dir_files.d2_core.qip", "gen_dir_files.files_synth.f", "gen_dir_files.sources.json",
             "project_files.d2_core.qip", "project_files.files_synth_resolved.f")
# play1r2: upstream files that may be REMOVED from the compiled set because a D2 derived copy replaces them (key must be ABSENT in the
# project's run.json; the base's hash must be the pinned upstream file on disk). Only this one file; never an addition or a changed hash.
UNIVERSE_REMOVED_SOURCES = ("references/s32/rtl/video/s32_sprite.sv",)
UNIVERSE_RUN_KEYS = UNIVERSE_RUN_KEYS + SRCLIST_KEYS
UNIVERSE_SOURCE_RE = re.compile(r"^experiments/D2-pocket-top/rtl/(?!mf_pll_ga2\.v$)[A-Za-z0-9_./-]+\.(sv|v)$")     # D2's own RTL, never the PLL
UNIVERSE_TREE = "experiments/D2-pocket-top/rtl"            # the only tree whose changed files a spec may list (tree_files)
SPEC_KEYS = {"schema", "name", "what", "git_objects", "run_json_keys", "tree_files"}
SPEC_SCHEMA = 1
GIT_OBJECTS = [
    "experiments/D2-pocket-top/rtl", "experiments/V2-datapath-floor/rtl", "experiments/P1-pocket-top/rtl", "experiments/WC-sim/rtl",
    "experiments/F2-fullchip/rtl", "experiments/M1-functional",
    "experiments/D2-pocket-top/gen_d2.py", "experiments/D2-pocket-top/make_d2_derived.py", "experiments/D2-pocket-top/pll_plan.py",
    "experiments/D2-pocket-top/pocket/prepare_quartus_d2.py", "experiments/D2-pocket-top/pocket/d2_pocket_io.py",
    "experiments/D2-pocket-top/pocket/d2_core_constraints.sdc", "experiments/D2-pocket-top/pocket/d2_timing.tcl",
    "experiments/V2-datapath-floor/toolchain.json", "experiments/V2-datapath-floor/ci_quartus.sh",
    "experiments/P1-pocket-top/BASELINE_S2.md", "UPSTREAM.lock", "references/s32", "tools/d2_gate.py", "tools/pll_gate.py",
]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def git(repo, *a):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"git {' '.join(a)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def flatten(o, pre=""):
    """-> {dotted path: leaf} (lists are indexed)."""
    out = {}
    if isinstance(o, dict):
        for k, v in o.items():
            out.update(flatten(v, f"{pre}.{k}" if pre else str(k)))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out.update(flatten(v, f"{pre}[{i}]"))
        if not o:
            out[pre] = []
    else:
        out[pre] = o
    return out


LIST_KEYS = (PIN_ARGS_KEY,)       # run.json keys whose value is a list that a delta spec allows as ONE entry


def collapse(flat):
    """flatten() output with the list-valued LIST_KEYS folded back into one entry each."""
    out = {}
    for k, v in flat.items():
        m = re.match(r"^(.*)\[\d+\]$", k)
        if m and m.group(1) in LIST_KEYS:
            out.setdefault(m.group(1), []).append(v)
        else:
            out[k] = v
    return out


def lines_of(p):
    return Path(p).read_text(errors="replace").splitlines()


# ---------------------------------------------------------------- result extractors (shared by make-reference and result)
def fit_summary(p):
    return [FITTER_STATUS.sub(r"\1", l.rstrip()) for l in lines_of(p) if l.strip()]


def sta_rows(p):
    rows, cur = [], {}
    for l in lines_of(p):
        m = re.match(r"^(Type|Slack|TNS)\s*:\s*(.*)$", l.rstrip())
        if not m:
            continue
        cur[m.group(1)] = m.group(2).strip()
        if m.group(1) == "TNS":
            rows.append([cur.get("Type"), cur.get("Slack"), cur.get("TNS")])
            cur = {}
    return rows


def timing_rows(p):
    return [l.rstrip() for l in lines_of(p) if re.match(r"^PLLT_(CLOCK|INT|IO|DONE|ERR)\b", l)]


def pll_usage(p):
    return [l.rstrip() for l in lines_of(p)]


def pll_params(lines):
    """PLL usage summary -> sorted [(context, field, value)] without placement ("... Location") lines and table borders. The
    context is the PLL node or the output-counter node (general[N]), so a reordered table compares equal."""
    out, ctx = [], "pll"
    for l in lines:
        m = re.match(r"^;\s*(--\s*)?(.*?)\s*;\s*(.*?)\s*;?\s*$", l)
        if not m:
            continue
        field, val = m.group(2).strip(), m.group(3).strip()
        if "PLL_OUTPUT_COUNTER" in field:
            g = re.search(r"general\[(\d)\]", field)
            ctx = f"counter general[{g.group(1) if g else '?'}]"
            continue
        if "FRACTIONAL_PLL" in field:
            ctx = "pll"
            continue
        if not field or "Location" in field or field == "PLL Usage Summary":
            continue
        out.append((ctx, field, val))
    return sorted(out)


def kv_fit(lines):
    d = {}
    for l in lines:
        m = re.match(r"^\s*\*?\s*([^:]+?)\s*:\s*([\d,]+)\s*(/\s*[\d,]+)?", l)
        if m:
            d[m.group(1).strip()] = int(m.group(2).replace(",", ""))
    return d


def slack_map(rows):
    """PLLT_INT / PLLT_IO rows -> {key: slack}."""
    d = {}
    for l in rows:
        t = l.split()
        if t and t[0] in ("PLLT_INT", "PLLT_IO") and len(t) >= 5:
            v = num(t[-1])
            if v is not None:
                d[" ".join(t[:-1])] = v
    return d


# Per-configuration PLL deltas for P1 (compare --pll-delta NAME). Michael 2026-11-12: SpidPlay1 (the spid job only) uses a pinned
# Spider-Man PLL reference based on the measured result of CI run 43268357151. That run passed the D2 gate with the accepted five
# outputs (same M, N, C counters, phases, duty, bandwidth, PLL location); Quartus chose a different fractional VCO solution than
# the timing-closed GA2 run (+0.010 ppm), the same in both SpidPlay1 fits. The delta is CLOSED and EXACT: these five fields only,
# each with the timing-closed value it replaces (checked against the reference, so it cannot apply to another base) and the
# pinned value it must become. Every other field must still equal the timing-closed run's, exactly. No tolerance, and nothing is
# taken from the fit under test. PLL_PINNED_LOCATION adds a location check that P1 otherwise ignores. Evidence: the committed
# copy of that run's PLL usage summary (sha256 in PLL_DELTA_EVIDENCE); tools/test_d2rbf_identity.py class PllDelta.
PLL_DELTAS = {
    "spid": {
        ("pll", "PLL Fractional Division"): ("473879157 / 4294967296", "473879562 / 4294967296"),
        ("pll", "PLL VCO Frequency"): ("676.442268 MHz", "676.442275 MHz"),
        ("pll", "PLL Freq Max Lock"): ("142.695104 MHz", "142.695102 MHz"),
        ("counter general[0]", "Output Clock Frequency"): ("96.634609 MHz", "96.63461 MHz"),
        ("counter general[1]", "Output Clock Frequency"): ("48.317304 MHz", "48.317305 MHz"),
    },
}
PLL_PINNED_LOCATION = {"spid": "FRACTIONALPLL_X54_Y1_N0"}
PLL_DELTA_EVIDENCE = {"spid": ("experiments/D2-pocket-top/reference/spid_run43268357151_pll_usage_summary.txt",
                               "7fc1f682483a205e67c9560d98e4fb2eaabaf691275464b55a9027ecf5d4fe9b")}


def pll_location(lines):
    """The PLL block's own "PLL Location" value (not the output counters'), or None."""
    for l in lines:
        m = re.match(r"^;\s*(--\s*)?PLL Location\s*;\s*(.*?)\s*;?\s*$", l)
        if m:
            return m.group(2).strip()
    return None


def pll_expected(ref_lines, delta):
    """The timing-closed run's PLL parameters with the named delta applied. Refuses (SystemExit) unless every delta key occurs
    exactly once in the reference, with exactly the delta's base value."""
    a = pll_params(ref_lines)
    if not delta:
        return a
    d = PLL_DELTAS[delta]
    keys = [(c, f) for c, f, _ in a]
    for k in d:
        if keys.count(k) != 1:
            raise SystemExit(f"refusing: --pll-delta {delta}: {k} occurs {keys.count(k)} times in the reference (want 1)")
    out = []
    for ctx, field, val in a:
        k = (ctx, field)
        if k in d:
            if val != d[k][0]:
                raise SystemExit(f"refusing: --pll-delta {delta}: reference {k} is {val!r}, the delta replaces {d[k][0]!r}")
            val = d[k][1]
        out.append((ctx, field, val))
    return sorted(out)


def pll_check(ref, pll_p, delta=None):
    """P1: the PLL parameters (placement lines ignored) equal the timing-closed run's; with a delta, the timing-closed run's with
    exactly that closed delta applied, and the PLL block location must be the pinned one."""
    lines = pll_usage(pll_p)
    a, b = pll_expected(ref["result"]["pll_usage"], delta), pll_params(lines)
    fails, notes = [], []
    if a != b:
        sa, sb = set(a), set(b)
        fails += [f"P1 PLL parameter differs: ref {x}" for x in sorted(sa - sb)][:20] + [f"P1 PLL parameter differs: new {x}" for x in sorted(sb - sa)][:20]
    elif delta:
        notes.append(f"P1 PLL parameters identical to the timing-closed run with the pinned '{delta}' delta ({len(a)} fields, "
                     f"{len(PLL_DELTAS[delta])} pinned; output-counter placement lines ignored)")
    else:
        notes.append(f"P1 PLL parameters identical to the timing-closed run ({len(a)} fields; placement lines ignored)")
    if delta:
        want, ref_loc, got = PLL_PINNED_LOCATION[delta], pll_location(ref["result"]["pll_usage"]), pll_location(lines)
        if ref_loc != want:
            raise SystemExit(f"refusing: --pll-delta {delta}: reference PLL Location {ref_loc!r}, pinned {want!r}")
        if got != want:
            fails.append(f"P1 PLL Location differs: want {want}, new {got}")
        else:
            notes.append(f"P1 PLL Location {got} (pinned for '{delta}')")
    return fails, notes


def compare_check(ref, fit_p, sta_p, tim_p, pll_p, pll_delta=None):
    r = ref["result"]
    fails, notes = pll_check(ref, pll_p, pll_delta)
    meaningful = []
    fr, fn = kv_fit(r["fit_summary"]), kv_fit(fit_summary(fit_p))
    for k in ("Logic utilization (in ALMs)", "Total registers", "Total RAM Blocks", "Total DSP Blocks", "Total block memory bits", "Total pins", "Total PLLs"):
        if k in fr and k in fn:
            d = fn[k] - fr[k]
            pct = 100.0 * d / fr[k] if fr[k] else 0.0
            flag = abs(pct) > 1.0
            (meaningful if flag else notes).append(f"resource {k}: {fn[k]} vs {fr[k]} ({d:+d}, {pct:+.2f} %)" + ("  MEANINGFUL" if flag else ""))
        else:
            notes.append(f"resource {k}: not found (ref {k in fr}, new {k in fn})")
    sr, sn = slack_map(r["timing_rows"]), slack_map(timing_rows(tim_p))
    same = 0
    for k in sorted(set(sr) | set(sn)):
        x, y = sr.get(k), sn.get(k)
        if x is None or y is None:
            meaningful.append(f"slack row only in {'new' if x is None else 'reference'}: {k} = {y if x is None else x}  MEANINGFUL")
            continue
        d = y - x
        if d == 0:
            same += 1
            continue
        flag = abs(d) > 0.10 or (x >= 0) != (y >= 0)
        (meaningful if flag else notes).append(f"slack {k}: {y:+.3f} vs {x:+.3f} ({d:+.3f} ns)" + ("  MEANINGFUL" if flag else ""))
    notes.append(f"slack rows identical: {same} of {len(set(sr) | set(sn))}")
    notes.append(f"MEANINGFUL differences (informational, never block): {len(meaningful)}")
    return fails, notes + meaningful


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def diff_lists(name, ref, got, tol=0.0, limit=25):
    """-> list of difference strings; numeric last tokens may differ by <= tol (tol 0 = exact)."""
    out = []
    if len(ref) != len(got):
        out.append(f"{name}: {len(got)} rows, reference has {len(ref)}")
    for i, (a, b) in enumerate(zip(ref, got)):
        if a == b:
            continue
        if tol and isinstance(a, str) and isinstance(b, str):
            ta, tb = a.rsplit(" ", 1), b.rsplit(" ", 1)
            if len(ta) == 2 and len(tb) == 2 and ta[0] == tb[0] and None not in (num(ta[1]), num(tb[1])) and abs(num(ta[1]) - num(tb[1])) <= tol:
                continue
        if tol and isinstance(a, list) and isinstance(b, list) and len(a) == len(b) == 3 and a[0] == b[0] and a[2] == b[2] \
                and None not in (num(a[1]), num(b[1])) and abs(num(a[1]) - num(b[1])) <= tol:
            continue
        out.append(f"{name}[{i}]: reference {a!r} != got {b!r}")
        if len(out) >= limit:
            out.append(f"{name}: ... (stopped after {limit} differences)")
            break
    return out


# ---------------------------------------------------------------- make-reference
def verify_evidence(ev):
    """Verify the used files against the evidence run's SHA256SUMS (two levels up: <run>/SHA256SUMS lists ./artifacts/d2-reports/seed1/<f>)."""
    ev = Path(ev).resolve()
    run_dir = ev.parents[2]
    sums = run_dir / "SHA256SUMS"
    if not sums.is_file():
        raise SystemExit(f"{sums} missing: the evidence must be the preserved run directory")
    listed = {}
    for l in sums.read_text().splitlines():
        h, _, n = l.partition("  ")
        listed[n.lstrip("./")] = h
    out = {}
    for f in EVIDENCE_FILES:
        key = (ev / f).relative_to(run_dir).as_posix()
        if key not in listed:
            raise SystemExit(f"evidence file {key} is not in {sums}")
        if sha(ev / f) != listed[key]:
            raise SystemExit(f"evidence file {key} does not match {sums}")
        out[f] = listed[key]
    return out


def make_reference(a):
    ev = Path(a.evidence)
    ev_sha = verify_evidence(ev)
    run = json.loads((ev / "run.json").read_text())
    commit = git(a.repo, "rev-parse", a.repo_commit)
    if run["repo"]["commit"] != commit:
        raise SystemExit(f"run.json repo.commit {run['repo']['commit']} != --repo-commit {commit}")
    if run["repo"]["dirty"] or run["repo"]["dirty_tracked"] or run["repo"]["untracked_in_experiments_tools"]:
        raise SystemExit("the reference run was not built from a clean tree")
    if run["microcode"]["ref_commit"] != commit:
        raise SystemExit("run.json microcode.ref_commit != repo.commit")
    cmp_doc = json.loads(json.dumps(run))
    excluded = {}
    for path in EXCLUDED_RUN_JSON:
        d = cmp_doc
        keys = path.split(".")
        for k in keys[:-1]:
            d = d[k]
        excluded[path] = d.pop(keys[-1])
    ref = {
        "schema": 1,
        "what": "Reference for the d2rbf job: the timing-closed D2 Pocket-top fit, seed 1 (see tools/d2rbf_identity.py)",
        "source_run": {"ci_run": a.run_id, "repo_commit": commit, "seed": run["seed"], "evidence_dir": ev.parents[2].name},
        "evidence_sha256": ev_sha,
        "quartus_version": re.sub(r"^Version ", "", lines_of(ev / "quartus_version.txt")[1].strip()),
        "template_rev": lines_of(ev / "template_rev.txt")[0].strip(),
        "run_json": cmp_doc,
        "run_json_excluded_values": excluded,
        "source_sha256_lines": [l.rstrip() for l in lines_of(ev / "source_sha256.txt") if l.strip()],
        "qsf_sha256": sha(ev / "ga2_d2.qsf"),
        "sdc_sha256": sha(ev / "core_constraints.sdc"),
        "git_objects": {p: git(a.repo, "rev-parse", f"{commit}:{p}") for p in GIT_OBJECTS},
        "result": {
            "fit_summary": fit_summary(ev / "ga2_d2.fit.summary"),
            "sta_summary_rows": sta_rows(ev / "ga2_d2.sta.summary"),
            "timing_rows": timing_rows(ev / "d2_timing.log"),
            "pll_usage": pll_usage(ev / "pll_usage_summary.txt"),
        },
    }
    # self-consistency of the evidence itself
    pf = run["project_files"]
    problems = []
    if pf["ga2_d2.qsf"] != ref["qsf_sha256"]:
        problems.append("evidence ga2_d2.qsf != run.json project_files")
    if pf["core/core_constraints.sdc"] != ref["sdc_sha256"] or run["sdc_sha256"] != ref["sdc_sha256"]:
        problems.append("evidence core_constraints.sdc != run.json")
    if len(ref["source_sha256_lines"]) != 5 or not ref["result"]["sta_summary_rows"] or not ref["result"]["timing_rows"]:
        problems.append("evidence lists are unexpectedly short")
    if ref["result"]["timing_rows"][-1] != "PLLT_DONE":
        problems.append("d2_timing.log has no PLLT_DONE as its last PLLT row")
    if problems:
        raise SystemExit("evidence is inconsistent: " + "; ".join(problems))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(ref, indent=1, sort_keys=True) + "\n")
    r = ref["result"]
    print(f"reference written: {a.out} ({len(run['source_files'])} source files, {len(pf)} project files, {len(GIT_OBJECTS)} git objects, "
          f"{len(r['fit_summary'])} fit-summary lines, {len(r['sta_summary_rows'])} STA summary rows, {len(r['timing_rows'])} PLLT rows, "
          f"{len(r['pll_usage'])} PLL-usage lines)")
    return 0


# ---------------------------------------------------------------- config
def rebased_sha(p, rebase):
    b = Path(p).read_bytes()
    return hashlib.sha256(b.replace(rebase[0].encode(), rebase[1].encode())).hexdigest()


def rebase_run(run, project, rebase):
    """Replace, in run.json (in place), the hashes of the four path-embedding files by the hashes after FROM -> TO."""
    for sect, name, where in PATH_EMBEDDING:
        f = (Path(project).parent / "gen" / name) if where else (Path(project) / name)
        if f.is_file():
            run[sect][name] = rebased_sha(f, rebase)


def config_check(ref, root, project, run_json, source_sha, qsf, sdc, rebase=None):
    fails, notes = [], []
    run = json.loads(Path(run_json).read_text())
    if rebase and project is not None:
        rebase_run(run, project, rebase)
        notes.append(f"REBASED for a local reproduction: {rebase[0]} -> {rebase[1]} in {[n for _s, n, _w in PATH_EMBEDDING]} (never used in CI)")
    # C1
    cmp_doc = json.loads(json.dumps(run))
    got_excl = {}
    for path in EXCLUDED_RUN_JSON:
        d, keys = cmp_doc, path.split(".")
        try:
            for k in keys[:-1]:
                d = d[k]
            got_excl[path] = d.pop(keys[-1])
        except KeyError:
            fails.append(f"C1 run.json lacks {path}")
    if got_excl.get("microcode.ref_commit") != got_excl.get("repo.commit"):
        fails.append(f"C1 run.json microcode.ref_commit {got_excl.get('microcode.ref_commit')} != repo.commit {got_excl.get('repo.commit')}")
    a, b = flatten(ref["run_json"]), flatten(cmp_doc)
    d = [f"{k}: reference {a.get(k)!r}, got {b.get(k)!r}" for k in sorted(set(a) | set(b)) if a.get(k, "<absent>") != b.get(k, "<absent>")]
    for x in d[:25]:
        fails.append("C1 run.json differs at " + x)
    if len(d) > 25:
        fails.append(f"C1 ... and {len(d) - 25} more run.json differences")
    if not d:
        notes.append(f"C1 run.json equals the reference in {len(a)} leaf fields (excluded: {', '.join(EXCLUDED_RUN_JSON)})")
    # C2
    got_lines = [l.rstrip() for l in Path(source_sha).read_text().splitlines() if l.strip()]
    if got_lines != ref["source_sha256_lines"]:
        for x in diff_lists("C2 source_sha256", ref["source_sha256_lines"], got_lines):
            fails.append(x)
    else:
        notes.append(f"C2 source_sha256.txt: {len(got_lines)} lines identical")
    # C3
    for label, p, want in (("qsf", qsf, ref["qsf_sha256"]), ("sdc", sdc, ref["sdc_sha256"])):
        h = sha(p)
        if h != want:
            fails.append(f"C3 {label} sha256 {h} != reference {want}")
        else:
            notes.append(f"C3 {label} sha256 {h[:16]}... identical")
    # C4, C5
    if project is None or root is None:
        notes.append("C4/C5 NOT CHECKED (no --project/--root): only the metadata of the candidate files was compared")
    else:
        root, project = Path(root), Path(project)
        ok4 = True
        for key, want in ref["run_json"]["source_files"].items():
            p = root / key
            if not p.is_file():
                fails.append(f"C4 source file missing on disk: {key}")
                ok4 = False
            elif sha(p) != want:
                fails.append(f"C4 source file {key} differs from the reference (disk {sha(p)[:16]}, reference {want[:16]})")
                ok4 = False
        pf = ref["run_json"]["project_files"]
        for key, want in pf.items():
            p = project / key
            if not p.is_file():
                fails.append(f"C4 project file missing on disk: {key}")
                ok4 = False
            elif (rebased_sha(p, rebase) if rebase and key == "files_synth_resolved.f" else sha(p)) != want:
                fails.append(f"C4 project file {key} differs from the reference (disk {sha(p)[:16]}, reference {want[:16]})")
                ok4 = False
        on_disk = {q.relative_to(project).as_posix() for q in project.rglob("*") if q.is_file()}
        extra = sorted(on_disk - set(pf) - {"run.json"})
        if extra:
            ok4 = False
            fails.append(f"C4 files in the project directory that the reference does not have: {extra[:10]}")
        qip = project / "d2_core.qip"
        if qip.is_file():
            n = 0
            for ln in qip.read_text().splitlines():
                m = re.search(r"\[file join \$::quartus\(qip_path\) (\S+)\]", ln)
                if m:
                    n += 1
                    rp = (project / m.group(1)).resolve()
                    try:
                        k = rp.relative_to(Path(root).resolve()).as_posix()
                    except ValueError:
                        k = str(rp)
                    if k not in ref["run_json"]["source_files"]:
                        fails.append(f"C4 the QIP lists a file the reference does not hash: {m.group(1)}")
                        ok4 = False
            notes.append(f"C4 the QIP lists {n} files, each one hashed in the reference")
        if ok4:
            notes.append(f"C4 {len(ref['run_json']['source_files'])} source files and {len(pf)} project files re-hashed from disk: identical")
        if "delta_spec" in ref:
            ds = ref["delta_spec"]
            sp_path = root / ds["path"]
            r = subprocess.run(["git", "-C", str(root), "diff", "HEAD", "--quiet", "--", ds["path"]])
            if not sp_path.is_file() or sha(sp_path) != ds["sha256"] or r.returncode:
                fails.append(f"C6 delta spec {ds['path']} differs from the one the reference was derived with (sha256 {ds['sha256'][:16]}) or from HEAD")
            else:
                try:
                    load_spec(sp_path)
                    notes.append(f"C6 delta spec {ds['path']} ({ds['name']}) is the one the reference was derived with")
                except SystemExit as e:
                    fails.append(f"C6 delta spec invalid: {e}")
        bad5 = 0
        for p, want in ref["git_objects"].items():
            r = subprocess.run(["git", "-C", str(root), "rev-parse", f"HEAD:{p}"], capture_output=True, text=True)
            got = r.stdout.strip() if r.returncode == 0 else f"<{r.stderr.strip()}>"
            if got != want:
                bad5 += 1
                fails.append(f"C5 git object {p}: HEAD has {got}, reference {want}")
        if not bad5:
            notes.append(f"C5 {len(ref['git_objects'])} git objects (trees/blobs/gitlink) equal those of {ref['source_run']['repo_commit'][:7]}")
    return fails, notes


# ---------------------------------------------------------------- delta specs
MC2_SPEC = {"schema": SPEC_SCHEMA, "name": "mc2", "what": "microcode-only delta (pin mc2); the default of derive-reference",
            "git_objects": sorted(ALLOWED_GIT_DELTA), "run_json_keys": sorted(ALLOWED_RUN_DELTA), "tree_files": {}}


def load_spec(path):
    """-> the validated delta spec (a dict). Fail closed on anything unexpected. Format (JSON, all keys required, lists sorted + unique):
      schema 1; name; what;
      git_objects    [git paths (trees/blobs) that may differ from the base reference's],
      run_json_keys  [dotted run.json keys (flatten()) that may differ; `source_files.<repo path>` names one hashed source file],
      tree_files     {git tree: [paths relative to it]}: if a tree is in git_objects and listed here, `git diff --name-only <base commit> HEAD`
                     inside it must be a SUBSET of the listed files (only experiments/D2-pocket-top/rtl may be listed).
    Every entry must lie in the universe (UNIVERSE_*): D2's own RTL source files (never mf_pll_ga2.v), the microcode / pin keys, the
    three hex project files, and the six git objects of UNIVERSE_GIT."""
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"refusing: delta spec {p} does not exist")
    try:
        sp = json.loads(p.read_text())
    except ValueError as e:
        raise SystemExit(f"refusing: delta spec {p} is not JSON ({e})")
    if not isinstance(sp, dict) or set(sp) != SPEC_KEYS or sp.get("schema") != SPEC_SCHEMA:
        raise SystemExit(f"refusing: delta spec {p}: keys must be exactly {sorted(SPEC_KEYS)} with schema {SPEC_SCHEMA}")
    if not (isinstance(sp["name"], str) and re.fullmatch(r"[a-z0-9_]+", sp["name"]) and isinstance(sp["what"], str)):
        raise SystemExit(f"refusing: delta spec {p}: name/what malformed")
    for k in ("git_objects", "run_json_keys"):
        v = sp[k]
        if not (isinstance(v, list) and all(isinstance(x, str) for x in v) and v == sorted(set(v))):
            raise SystemExit(f"refusing: delta spec {p}: {k} must be a sorted list of unique strings")
    for g in sp["git_objects"]:
        if g not in UNIVERSE_GIT:
            raise SystemExit(f"refusing: delta spec {p}: git object {g!r} may never be allowed (universe: {list(UNIVERSE_GIT)})")
    for k in sp["run_json_keys"]:
        if not (k in UNIVERSE_RUN_KEYS or (k.startswith("source_files.") and (UNIVERSE_SOURCE_RE.match(k[len("source_files."):])
                                                                            or k[len("source_files."):] in UNIVERSE_REMOVED_SOURCES))):
            raise SystemExit(f"refusing: delta spec {p}: run.json key {k!r} may never be allowed")
    tf = sp["tree_files"]
    if not (isinstance(tf, dict) and set(tf) <= {UNIVERSE_TREE}):
        raise SystemExit(f"refusing: delta spec {p}: tree_files may only list {UNIVERSE_TREE}")
    for t, fl in tf.items():
        if not (isinstance(fl, list) and fl and fl == sorted(set(fl)) and all(isinstance(x, str) and UNIVERSE_SOURCE_RE.match(f"{t}/{x}") for x in fl)):
            raise SystemExit(f"refusing: delta spec {p}: tree_files[{t}] must be a sorted list of D2 RTL files (not mf_pll_ga2.v)")
        if t not in sp["git_objects"]:
            raise SystemExit(f"refusing: delta spec {p}: tree_files lists {t}, which is not in git_objects")
    # every listed source file lies in a tree whose change is listed (the rtl tree): otherwise the spec is inconsistent
    srcs = [k for k in sp["run_json_keys"] if k.startswith("source_files.")]
    if [k for k in srcs if k[len("source_files."):] in UNIVERSE_REMOVED_SOURCES] and not any(UNIVERSE_SOURCE_RE.match(k[len("source_files."):]) for k in srcs):
        raise SystemExit(f"refusing: delta spec {p}: an upstream source may only be removed together with the D2 file that replaces it")
    if srcs and UNIVERSE_TREE not in sp["git_objects"]:
        raise SystemExit(f"refusing: delta spec {p}: source files listed but {UNIVERSE_TREE} is not in git_objects")
    if UNIVERSE_TREE in sp["git_objects"] and not tf:
        raise SystemExit(f"refusing: delta spec {p}: {UNIVERSE_TREE} may differ but tree_files does not list which files")
    return sp


def check_spec_in_repo(spec_path, root):
    """-> (repo-relative path, sha256) of a spec that lies inside the repository, is tracked and equals HEAD; else SystemExit."""
    rel = Path(spec_path).resolve().relative_to(Path(root).resolve()).as_posix() if Path(spec_path).resolve().is_relative_to(Path(root).resolve()) else None
    if rel is None:
        raise SystemExit(f"refusing: delta spec {spec_path} is not inside the repository {root}")
    if subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", rel], capture_output=True).returncode \
            or subprocess.run(["git", "-C", str(root), "diff", "HEAD", "--quiet", "--", rel]).returncode:
        raise SystemExit(f"refusing: the delta spec {rel} is not committed (tracked and equal to HEAD)")
    return rel, sha(spec_path)


# ---------------------------------------------------------------- derive-reference
def derive_reference(a):
    root, project = Path(a.root).resolve(), Path(a.project).resolve()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import microcode_pin
    base_p = Path(a.base)
    base = json.loads(base_p.read_text())
    if "derived_from" in base:
        raise SystemExit("refusing: the base reference is itself derived; derive from the original timing-closed reference")
    pin = microcode_pin.load_pin(a.pin)
    pin_rel = microcode_pin.pin_repo_path(a.pin, root)
    pin_sha = sha(a.pin)
    if a.delta_spec:
        spec = load_spec(a.delta_spec)
        spec_rel, spec_sha = check_spec_in_repo(a.delta_spec, root)
    else:
        spec, spec_rel, spec_sha = MC2_SPEC, None, None
    allowed_run, allowed_git = set(spec["run_json_keys"]), list(spec["git_objects"])
    fails = []
    run = json.loads((project / "run.json").read_text())
    rebase = tuple(a.rebase_root) if a.rebase_root else None
    if rebase:
        rebase_run(run, project, rebase)
    cmp_doc = json.loads(json.dumps(run))
    for path in EXCLUDED_RUN_JSON:
        d, keys = cmp_doc, path.split(".")
        try:
            for k in keys[:-1]:
                d = d[k]
            d.pop(keys[-1])
        except KeyError:
            fails.append(f"run.json lacks {path}")
    # 1. run.json: any difference to the base outside the enumerated delta fails
    fa, fb = collapse(flatten(base["run_json"])), collapse(flatten(cmp_doc))
    changed = sorted(k for k in set(fa) | set(fb) if fa.get(k, ABSENT) != fb.get(k, ABSENT))
    delta = [{"key": "run_json." + k, "old": fa.get(k, ABSENT), "new": fb.get(k, ABSENT)} for k in changed if k in allowed_run]
    for k in changed:
        if k not in allowed_run:
            fails.append(f"run.json differs from the base OUTSIDE the allowed delta at {k}: base {fa.get(k, ABSENT)!r}, project {fb.get(k, ABSENT)!r}")
    # 2. values inside the delta must be exactly the pin's
    mc, pf = cmp_doc.get("microcode", {}), cmp_doc.get("project_files", {})
    want_im = {n.replace("m1_", "v2_"): h for n, h in pin["images_sha256"].items()}
    for label, got, want in (("microcode.images_sha256", mc.get("images_sha256"), want_im), ("microcode.baseline_s2_expected", mc.get("baseline_s2_expected"), want_im),
                             ("microcode.builder_sha256", mc.get("builder_sha256"), pin["builder_sha256"]),
                             ("microcode.verified_against", mc.get("verified_against"), pin_rel), ("microcode.pin_sha256", mc.get("pin_sha256"), pin_sha),
                             ("microcode.v25_microcode_bin_sha256", mc.get("v25_microcode_bin_sha256"), pin["v25_microcode_bin_sha256"]),
                             ("project_files.microcode.bin", pf.get("microcode.bin"), pin["v25_microcode_bin_sha256"])):
        if got != want:
            fails.append(f"run.json {label} is {got!r}, the pin says {want!r}")
    want_args = microcode_pin.builder_args(pin) if "args" in pin else None            # the key exists in run.json iff the pin has args
    if mc.get("builder_args", ABSENT) != (want_args if want_args is not None else ABSENT):
        fails.append(f"run.json microcode.builder_args is {mc.get('builder_args', ABSENT)!r}, the pin says {want_args if want_args is not None else ABSENT!r}")
    if want_args is not None and PIN_ARGS_KEY not in allowed_run:
        fails.append(f"the pin has builder args but the delta spec does not allow {PIN_ARGS_KEY}")
    # source files named by the spec: the recorded hash is the file's on disk, and the file is HEAD's (not a working-tree edit)
    for k in sorted(allowed_run):
        if k.startswith("source_files."):
            rel = k[len("source_files."):]
            got = cmp_doc.get("source_files", {}).get(rel)
            if rel in UNIVERSE_REMOVED_SOURCES:              # replaced by a D2 derived copy: absent now; the base's hash is the pinned upstream file
                if got is not None:
                    fails.append(f"run.json {k} must be absent (replaced by a derived copy), it is {got!r}")
                want = fa.get("source_files." + rel)
                if want is None or not (root / rel).is_file() or sha(root / rel) != want:
                    fails.append(f"run.json {k}: the base's hash is not the pinned upstream file on disk")
                continue
            if got is not None and (not (root / rel).is_file() or sha(root / rel) != got):
                fails.append(f"run.json {k} is not the file on disk")
            if subprocess.run(["git", "-C", str(root), "diff", "HEAD", "--quiet", "--", rel]).returncode:
                fails.append(f"{rel} differs from HEAD (uncommitted)")
    for n in HEX_NAMES:
        if pf.get(n) != want_im[n]:
            fails.append(f"run.json project_files.{n} is {pf.get(n)!r}, the pin says {want_im[n]!r}")
        elif (project / n).is_file() and sha(project / n) != want_im[n]:
            fails.append(f"project file {n} on disk differs from the pin")
    tree = git(root, "rev-parse", "HEAD:experiments/M1-functional")
    if tree != pin["source"]["m1_functional_tree"]:
        fails.append(f"experiments/M1-functional at HEAD is tree {tree}, the pin is for {pin['source']['m1_functional_tree']}")
    # 3. git objects: only the enumerated scripts/trees may differ; their new value is HEAD's
    go = dict(base["git_objects"])
    for t, files in spec["tree_files"].items():          # only the listed files inside the tree may have changed since the base commit
        bc = base["source_run"]["repo_commit"]
        if subprocess.run(["git", "-C", str(root), "cat-file", "-e", bc + "^{commit}"], capture_output=True).returncode:
            fails.append(f"tree_files of the spec cannot be checked: base commit {bc} is not in this repository")
        else:
            changed_files = git(root, "diff", "--name-only", bc, "HEAD", "--", t).splitlines()
            extra = sorted(x for x in changed_files if x[len(t) + 1:] not in files)
            if extra:
                fails.append(f"files changed inside {t} since the base commit {bc[:9]} that the delta spec does not list: {extra}")
    for k in allowed_git:
        new = git(root, "rev-parse", f"HEAD:{k}")
        old = go.get(k, ABSENT)
        if new != old:
            delta.append({"key": "git_objects." + k, "old": old, "new": new})
        go[k] = new
    # 4. everything else of the base is carried over unchanged (and re-verified by config below)
    new_ref = json.loads(json.dumps(base))
    new_ref["what"] = ("DERIVED configuration reference for the d2rbf job with deliberately changed V2 microcode: the base reference "
                       "plus the enumerated delta (tools/d2rbf_identity.py derive-reference). Its `result` section is the base's.")
    new_ref["run_json"] = cmp_doc
    new_ref["git_objects"] = go
    new_ref["derived_from"] = {"base_path": microcode_pin.pin_repo_path(base_p, root) if base_p.resolve().is_relative_to(root) else str(base_p),
                               "base_sha256": sha(base_p), "source_run": base["source_run"]}
    new_ref["microcode_pin"] = {"path": pin_rel, "sha256": pin_sha, "source": pin["source"]}
    if spec_rel:
        new_ref["delta_spec"] = {"path": spec_rel, "sha256": spec_sha, "name": spec["name"]}
    new_ref["delta"] = sorted(delta, key=lambda x: x["key"])
    if new_ref["result"] != base["result"]:
        fails.append("internal: result section changed")
    # 5. strict C1-C5 of the unchanged config check against the new reference
    cf, cn = config_check(new_ref, str(root), str(project), str(project / "run.json"), a.source_sha, str(project / "ga2_d2.qsf"),
                          str(project / "core" / "core_constraints.sdc"), rebase)
    fails += ["config check against the derived reference: " + x for x in cf]
    for x in cn:
        print("  " + x)
    print(f"ALLOWED DELTA applied ({len(new_ref['delta'])} entries):")
    for x in new_ref["delta"]:
        print(f"  {x['key']}: {x['old']} -> {x['new']}")
    if fails:
        for f in fails:
            print("FAIL  " + f)
        print("DERIVE-REFERENCE FAIL (nothing written)")
        return 1
    out = Path(a.out)
    if out.exists():
        raise SystemExit(f"refusing: {out} exists")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(new_ref, indent=1, sort_keys=True) + "\n")
    print(f"DERIVE-REFERENCE PASS: {out}")
    return 0


# ---------------------------------------------------------------- self-reference (structurally new configuration)
SELF_CONFIGS = ("sp", "spid", "univ", "au", "au3", "rel")
AU_SDC_APPEND = "experiments/D2-pocket-top/pocket/d2_au_constraints.sdc"
SELF_EXTRA_GIT_AU = (AU_SDC_APPEND, "experiments/D2-pocket-top/pocket/d2_au_io.tcl")   # au: added objects (never frozen)
AU3_SDC_APPEND = "experiments/D2-pocket-top/pocket/d2_au3_constraints.sdc"
SELF_EXTRA_GIT_AU3 = SELF_EXTRA_GIT_AU + (AU3_SDC_APPEND, "experiments/D2-pocket-top/pocket/d2_au3_io.tcl")   # au3: added objects (never frozen)
_UNIV_GIT = ("experiments/SP-sprite-path/rtl", "experiments/SM1-spidman/rtl", "experiments/U1-universal/rtl", "experiments/U1-universal/games",
             "experiments/U1-universal/pocket/u1_fit_hold.sdc")
SELF_EXTRA_GIT = {"sp": ("experiments/SP-sprite-path/rtl",),
                  "spid": ("experiments/SP-sprite-path/rtl", "experiments/SM1-spidman/rtl"),     # spid: the Spider-Man top and its read-back
                  # univ (U1b): + the universal top / core / configuration register file and the game files the configuration files come from
                  "univ": _UNIV_GIT,                                                              # + the univ-only SDC fragment (A12, SDC_FRAGMENTS)
                  "au": ("experiments/SP-sprite-path/rtl",) + SELF_EXTRA_GIT_AU,
                  "au3": ("experiments/SP-sprite-path/rtl",) + SELF_EXTRA_GIT_AU3,
                  "rel": _UNIV_GIT + SELF_EXTRA_GIT_AU3}                                          # rel: univ's objects + au3's
SELF_APPENDS = {"au": (("au_sdc_append", "sdc_au_append_sha256", AU_SDC_APPEND),),
                "au3": (("au_sdc_append", "sdc_au_append_sha256", AU_SDC_APPEND), ("au3_sdc_append", "sdc_au3_append_sha256", AU3_SDC_APPEND)),
                "rel": (("au_sdc_append", "sdc_au_append_sha256", AU_SDC_APPEND), ("au3_sdc_append", "sdc_au3_append_sha256", AU3_SDC_APPEND))}
SELF_WHAT = {"sp": "GA2Play2", "spid": "SpidPlay1, experiments/SM1-spidman", "univ": "U1b universal core, experiments/U1-universal",
             "au": "GA2Audio1, exp/audio1", "au3": "GA2Audio3, exp/audio3", "rel": "release/s32-v1 universal core, experiments/U1-universal rel_derive.py"}
FROZEN_RUN_KEYS = ("template", "upstream_s32", "macros", "search_paths", "sdc_sha256", "io_script_sha256", "revision", "experiment")
FROZEN_PROJECT_FILES_RE = re.compile(r"^(apf/.*|core_bridge_cmd\.v|core/core_constraints\.sdc|ga2_d2\.qsf|ga2_d2\.qpf|microcode\.bin)$")
PLL_SOURCE = "experiments/D2-pocket-top/rtl/mf_pll_ga2.v"
SHARED_SDC = "experiments/D2-pocket-top/pocket/d2_core_constraints.sdc"
# The closed set of SDC fragments a self-reference may freeze on top of the base's SDC (S4): U1b's univ-only fitter hold margin A12
# (Michael 2026-11-14, U1B.md section 11). The sha256 pins the exact bytes: any change of the constraint needs a change HERE (a reviewed
# code change), new self-references, and the matching code in experiments/D2-pocket-top/pocket/d2_project_check.py UNIV_FRAGMENT_CODE.
SDC_FRAGMENTS = {"univ": {"path": "experiments/U1-universal/pocket/u1_fit_hold.sdc",
                          "sha256": "def39288983bfd6ca50b713b38421b398419f79554170c3e93494312f2d14988"}}
SDC_FRAGMENTS["rel"] = dict(SDC_FRAGMENTS["univ"])   # release/s32-v1: the same pinned A12 bytes, after the au3 appendices
SDC_FRAGMENT_RUN_KEY = "sdc_univ_fragment"


def qsf_sha_seed1(qsf, seed):
    """sha256 of the QSF with exactly one line changed: `set_global_assignment -name SEED <seed>` -> SEED 1 (seed 1: the file's own hash).
    The QSF of seed N differs from the timing-closed seed-1 QSF in that one line and nothing else."""
    raw = Path(qsf).read_bytes()
    if seed == 1:
        return hashlib.sha256(raw).hexdigest()
    pat = re.compile(rb"(set_global_assignment -name SEED )" + str(seed).encode() + rb"(\r?\n)")
    if len(pat.findall(raw)) != 1 or raw.count(b"-name SEED ") != 1:
        raise SystemExit(f"S4 QSF must contain exactly one SEED line, 'SEED {seed}'")
    return hashlib.sha256(pat.sub(rb"\g<1>1\g<2>", raw)).hexdigest()


def self_reference(a):
    root, project = Path(a.root).resolve(), Path(a.project).resolve()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import microcode_pin
    if a.config not in SELF_CONFIGS:
        raise SystemExit(f"refusing: self-reference only for {SELF_CONFIGS}")
    base_p = Path(a.base)
    base = json.loads(base_p.read_text())
    if "derived_from" in base or "self_reference" in base:
        raise SystemExit("refusing: the base must be the original timing-closed reference")
    pin = microcode_pin.load_pin(a.pin)
    pin_rel, pin_sha = microcode_pin.pin_repo_path(a.pin, root), sha(a.pin)
    fails = []
    run = json.loads((project / "run.json").read_text())
    rebase = tuple(a.rebase_root) if a.rebase_root else None
    if rebase:
        rebase_run(run, project, rebase)
    head = git(root, "rev-parse", "HEAD")
    # S1
    rp = run.get("repo", {})
    if rp.get("dirty") or rp.get("dirty_tracked") or rp.get("untracked_in_experiments_tools"):
        fails.append("S1 the project was not generated from a clean tree")
    if rp.get("commit") != head or run.get("microcode", {}).get("ref_commit") != head:
        fails.append(f"S1 run.json repo.commit {rp.get('commit')} / microcode.ref_commit must be HEAD {head}")
    # S2
    if run.get("config") != a.config:
        fails.append(f"S2 run.json config {run.get('config')!r} != {a.config!r}")
    if run.get("seed") != a.seed:
        fails.append(f"S2 run.json seed {run.get('seed')!r} != --seed {a.seed}")
    # S3
    mc, pf = run.get("microcode", {}), run.get("project_files", {})
    want_im = {n.replace("m1_", "v2_"): h for n, h in pin["images_sha256"].items()}
    for label, got, want in (("microcode.images_sha256", mc.get("images_sha256"), want_im), ("microcode.baseline_s2_expected", mc.get("baseline_s2_expected"), want_im),
                             ("microcode.builder_sha256", mc.get("builder_sha256"), pin["builder_sha256"]),
                             ("microcode.verified_against", mc.get("verified_against"), pin_rel), ("microcode.pin_sha256", mc.get("pin_sha256"), pin_sha),
                             ("microcode.v25_microcode_bin_sha256", mc.get("v25_microcode_bin_sha256"), pin["v25_microcode_bin_sha256"]),
                             ("microcode.builder_args", mc.get("builder_args", ABSENT), microcode_pin.builder_args(pin) if "args" in pin else ABSENT)):
        if got != want:
            fails.append(f"S3 run.json {label} is {got!r}, the pin says {want!r}")
    for n in HEX_NAMES:
        if pf.get(n) != want_im[n] or not (project / n).is_file() or sha(project / n) != want_im[n]:
            fails.append(f"S3 project file {n} is not the pin's image")
    tree = git(root, "rev-parse", "HEAD:experiments/M1-functional")
    if tree != pin["source"]["m1_functional_tree"]:
        fails.append(f"S3 experiments/M1-functional at HEAD is tree {tree}, the pin is for {pin['source']['m1_functional_tree']}")
    # S4 frozen vs the timing-closed base
    src_lines = [l.rstrip() for l in Path(a.source_sha).read_text().splitlines() if l.strip()]
    frozen = []
    def froze(label, got, want):
        frozen.append(label)
        if got != want:
            fails.append(f"S4 {label} differs from the timing-closed base: {str(got)[:90]!r} vs {str(want)[:90]!r}")
    qsf_seed1 = qsf_sha_seed1(project / "ga2_d2.qsf", a.seed)   # the QSF with only its SEED assignment normalised to 1 (the base's seed)
    froze("qsf_sha256", qsf_seed1, base["qsf_sha256"])
    frag = SDC_FRAGMENTS.get(a.config)
    want_sdc = base["sdc_sha256"]
    sdc_b = (project / "core" / "core_constraints.sdc").read_bytes()
    appends = {}
    if a.config in SELF_APPENDS:
        # the base SDC byte for byte (same length as the frozen base file in the repository, which S4 also freezes as a git object),
        # then exactly the committed appendix (au3: the au appendix, then the au3 appendix; rel: those two, then the pinned A12 fragment):
        # no existing constraint can change
        fr_b = (root / frag["path"]).read_bytes() if frag is not None and (root / frag["path"]).is_file() else b""
        app = b"".join((root / path).read_bytes() for _k, _r, path in SELF_APPENDS[a.config]) + fr_b
        nb = len(sdc_b) - len(app)
        froze(f"sdc_sha256 ({a.config}: the base prefix)", hashlib.sha256(sdc_b[:max(nb, 0)]).hexdigest(), base["sdc_sha256"])
        names = " + ".join(path for _k, _r, path in SELF_APPENDS[a.config]) + (f" + {frag['path']}" if frag is not None else "")
        if nb < 0 or sdc_b[nb:] != app:
            fails.append(f"S4 {a.config}: core/core_constraints.sdc does not end with exactly {names}")
        for key, rkey, path in SELF_APPENDS[a.config]:
            if run.get(rkey) != sha(root / path):
                fails.append(f"S4 {a.config}: run.json {rkey} {run.get(rkey)!r} != sha256 of {path}")
            r = subprocess.run(["git", "-C", str(root), "diff", "HEAD", "--quiet", "--", path])
            if r.returncode:
                fails.append(f"S4 {a.config}: {path} differs from HEAD")
            appends[key] = {"path": path, "sha256": sha(root / path)}
        if frag is not None:              # rel: + the pinned fragment (as univ)
            froze(f"{frag['path']} on disk (pinned SDC_FRAGMENTS[{a.config!r}])", hashlib.sha256(fr_b).hexdigest(), frag["sha256"])
            froze(f"run_json.{SDC_FRAGMENT_RUN_KEY}", run.get(SDC_FRAGMENT_RUN_KEY, ABSENT), {"path": frag["path"], "sha256": frag["sha256"]})
        else:
            froze(f"run_json.{SDC_FRAGMENT_RUN_KEY}", run.get(SDC_FRAGMENT_RUN_KEY, ABSENT), ABSENT)
    elif frag is not None:                # univ: the base's SDC bytes + the pinned fragment, nothing else
        sh_p, fr_p = root / SHARED_SDC, root / frag["path"]
        sh_b = sh_p.read_bytes() if sh_p.is_file() else b""
        fr_b = fr_p.read_bytes() if fr_p.is_file() else b""
        froze(f"{SHARED_SDC} on disk", hashlib.sha256(sh_b).hexdigest(), base["sdc_sha256"])
        froze(f"{frag['path']} on disk (pinned SDC_FRAGMENTS[{a.config!r}])", hashlib.sha256(fr_b).hexdigest(), frag["sha256"])
        froze(f"run_json.{SDC_FRAGMENT_RUN_KEY}", run.get(SDC_FRAGMENT_RUN_KEY, ABSENT), {"path": frag["path"], "sha256": frag["sha256"]})
        want_sdc = hashlib.sha256(sh_b + fr_b).hexdigest()
        froze("sdc_sha256", sha(project / "core" / "core_constraints.sdc"), want_sdc)
    else:
        froze(f"run_json.{SDC_FRAGMENT_RUN_KEY}", run.get(SDC_FRAGMENT_RUN_KEY, ABSENT), ABSENT)
        froze("sdc_sha256", sha(project / "core" / "core_constraints.sdc"), want_sdc)
    froze("source_sha256_lines", src_lines, base["source_sha256_lines"])
    for k in FROZEN_RUN_KEYS:
        froze(f"run_json.{k}", run.get(k, ABSENT), base["run_json"].get(k, ABSENT))
    bpf = base["run_json"]["project_files"]
    keys = sorted({k for k in set(bpf) | set(pf) if FROZEN_PROJECT_FILES_RE.match(k)})
    for k in keys:
        if a.config in SELF_APPENDS and k == "core/core_constraints.sdc":
            continue                                   # au / au3 / rel: checked above as base prefix + appendices
        froze(f"project_files.{k}", qsf_seed1 if k == "ga2_d2.qsf" else pf.get(k, ABSENT),
              want_sdc if (frag is not None and k == "core/core_constraints.sdc") else bpf.get(k, ABSENT))
    froze(f"source_files.{PLL_SOURCE}", run.get("source_files", {}).get(PLL_SOURCE, ABSENT), base["run_json"]["source_files"].get(PLL_SOURCE, ABSENT))
    go = {}
    for k in GIT_OBJECTS + list(SELF_EXTRA_GIT[a.config]):
        go[k] = git(root, "rev-parse", f"HEAD:{k}")
        if k in base["git_objects"] and k not in UNIVERSE_GIT:
            froze(f"git_objects.{k}", go[k], base["git_objects"][k])
    # the new reference
    cmp_doc = json.loads(json.dumps(run))
    for path in EXCLUDED_RUN_JSON:
        d, ks = cmp_doc, path.split(".")
        for k in ks[:-1]:
            d = d[k]
        d.pop(ks[-1], None)
    ref = {
        "schema": 1,
        "what": (f"SELF-REFERENCE configuration reference for the {a.config} configuration ({SELF_WHAT[a.config]}): this commit's locally generated project "
                 "(tools/d2rbf_identity.py self-reference). NOT a delta vs run 42135281517 (the compiled source set is structurally new); "
                 "QSF / SDC / PLL / I/O / toolchain / gate scripts are frozen to it (S4). Its `result` section is the base's."),
        "source_run": {"kind": "self", "repo_commit": head, "config": a.config, "seed": run.get("seed")},
        "quartus_version": base.get("quartus_version"), "template_rev": base.get("template_rev"),
        "run_json": cmp_doc, "run_json_excluded_values": {},
        "source_sha256_lines": src_lines, "qsf_sha256": sha(project / "ga2_d2.qsf"), "sdc_sha256": sha(project / "core" / "core_constraints.sdc"),
        "git_objects": go, "result": base["result"],
        "microcode_pin": {"path": pin_rel, "sha256": pin_sha, "source": pin["source"]},
        "self_reference": {"config": a.config, "repo_commit": head,
                           "base_path": microcode_pin.pin_repo_path(base_p, root) if base_p.resolve().is_relative_to(root) else str(base_p),
                           "base_sha256": sha(base_p), "base_source_run": base["source_run"], "frozen_vs_base": frozen},
    }
    ref.update(appends)
    # S5
    cf, cn = config_check(ref, str(root), str(project), str(project / "run.json"), a.source_sha, str(project / "ga2_d2.qsf"),
                          str(project / "core" / "core_constraints.sdc"), rebase)
    fails += ["S5 config check against the self-reference: " + x for x in cf]
    for x in cn:
        print("  " + x)
    print(f"S4 frozen vs the timing-closed base: {len(frozen)} items")
    if fails:
        for f in fails:
            print("FAIL  " + f)
        print("SELF-REFERENCE FAIL (nothing written)")
        return 1
    out = Path(a.out)
    if out.exists():
        raise SystemExit(f"refusing: {out} exists")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ref, indent=1, sort_keys=True) + "\n")
    print(f"SELF-REFERENCE PASS: {out} ({len(run['source_files'])} source files, {len(pf)} project files, {len(go)} git objects)")
    return 0


def result_check(ref, fit_p, sta_p, tim_p, pll_p, tol):
    r = ref["result"]
    fails, notes = [], []
    got = {"R1 fit summary": (r["fit_summary"], fit_summary(fit_p)),
           "R2 STA summary rows": (r["sta_summary_rows"], sta_rows(sta_p)),
           "R3 timing rows (PLLT)": (r["timing_rows"], timing_rows(tim_p)),
           "R4 PLL usage summary": (r["pll_usage"], pll_usage(pll_p))}
    for name, (want, have) in got.items():
        d = diff_lists(name, want, have, tol if name.startswith(("R2", "R3")) else 0.0)
        fails += d
        if not d:
            notes.append(f"{name}: {len(have)} rows identical" + (f" (slack tolerance {tol} ns)" if tol and name.startswith(("R2", "R3")) else ""))
    if not any(l.startswith("Quartus Prime Version") and ref["quartus_version"] in l for l in got["R1 fit summary"][1]):
        fails.append(f"R1 Quartus version is not {ref['quartus_version']!r}")
    return fails, notes


def emit(out, text, kind, fails, notes):
    lines = [f"D2RBF {kind} IDENTITY vs the timing-closed run (reference commit/run in the manifest)"]
    lines += ["  " + n for n in notes]
    lines += ["  FAIL: " + f for f in fails]
    lines.append(f"D2RBF {kind} IDENTITY " + ("FAIL" if fails else "PASS"))
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps({"kind": kind, "result": "FAIL" if fails else "PASS", "failures": fails, "notes": notes}, indent=1) + "\n")
    if text:
        Path(text).parent.mkdir(parents=True, exist_ok=True)
        Path(text).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    m = sub.add_parser("make-reference")
    m.add_argument("--evidence", required=True)
    m.add_argument("--repo", default=".")
    m.add_argument("--repo-commit", required=True)
    m.add_argument("--run-id", default="42135281517")
    m.add_argument("--out", required=True)
    dr = sub.add_parser("derive-reference")
    dr.add_argument("--base", required=True)
    dr.add_argument("--pin", required=True)
    dr.add_argument("--project", required=True)
    dr.add_argument("--root", default=".")
    dr.add_argument("--source-sha", required=True)
    dr.add_argument("--rebase-root", nargs=2, metavar=("FROM", "TO"))
    dr.add_argument("--delta-spec", help="committed delta spec (default: the built-in microcode-only delta of pin mc2)")
    dr.add_argument("--out", required=True)
    s = sub.add_parser("self-reference")
    s.add_argument("--base", required=True)
    s.add_argument("--pin", required=True)
    s.add_argument("--project", required=True)
    s.add_argument("--root", default=".")
    s.add_argument("--source-sha", required=True)
    s.add_argument("--config", required=True)
    s.add_argument("--seed", type=int, default=1, choices=(1, 2, 3))
    s.add_argument("--rebase-root", nargs=2, metavar=("FROM", "TO"))
    s.add_argument("--out", required=True)
    c = sub.add_parser("config")
    c.add_argument("--reference", required=True)
    c.add_argument("--root")
    c.add_argument("--project")
    c.add_argument("--run-json")
    c.add_argument("--source-sha", required=True)
    c.add_argument("--qsf")
    c.add_argument("--sdc")
    c.add_argument("--rebase-root", nargs=2, metavar=("FROM", "TO"))
    c.add_argument("--out")
    c.add_argument("--text")
    c2 = sub.add_parser("compare")
    for o in ("--reference", "--fit-summary", "--sta-summary", "--timing-log", "--pll-usage"):
        c2.add_argument(o, required=True)
    c2.add_argument("--pll-delta", choices=sorted(PLL_DELTAS), help="a closed, pinned PLL delta (spid only; see PLL_DELTAS)")
    c2.add_argument("--out")
    c2.add_argument("--text")
    r = sub.add_parser("result")
    r.add_argument("--reference", required=True)
    r.add_argument("--fit-summary", required=True)
    r.add_argument("--sta-summary", required=True)
    r.add_argument("--timing-log", required=True)
    r.add_argument("--pll-usage", required=True)
    r.add_argument("--slack-tol", type=float, default=0.0)
    r.add_argument("--out")
    r.add_argument("--text")
    x = ap.parse_args(argv)
    if x.mode == "make-reference":
        return make_reference(x)
    if x.mode == "derive-reference":
        return derive_reference(x)
    if x.mode == "self-reference":
        return self_reference(x)
    ref = json.loads(Path(x.reference).read_text())
    if x.mode == "config":
        proj = Path(x.project) if x.project else None
        run_json = x.run_json or str(proj / "run.json")
        qsf = x.qsf or str(proj / "ga2_d2.qsf")
        sdc = x.sdc or str(proj / "core" / "core_constraints.sdc")
        fails, notes = config_check(ref, x.root, x.project, run_json, x.source_sha, qsf, sdc, tuple(x.rebase_root) if x.rebase_root else None)
        return emit(x.out, x.text, "CONFIGURATION", fails, notes)
    if x.mode == "compare":
        fails, notes = compare_check(ref, x.fit_summary, x.sta_summary, x.timing_log, x.pll_usage, x.pll_delta)
        return emit(x.out, x.text, "PLL (strict) + RESULT COMPARISON (informational)", fails, notes)
    fails, notes = result_check(ref, x.fit_summary, x.sta_summary, x.timing_log, x.pll_usage, x.slack_tol)
    return emit(x.out, x.text, "RESULT", fails, notes)


if __name__ == "__main__":
    sys.exit(main())
