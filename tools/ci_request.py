#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate an explicit build request for hw-build.yml.

A Quartus build starts only when a push changes exactly one request file,
experiments/build-requests/<job>.json, to a new valid request. Commit messages
play no part. (A manual workflow_dispatch would be preferable, but GitHub only
dispatches workflows present on the default branch, which must not change.)

Request: {"job": "hwt" | "f2" | "d1" | "pll" | "d2" | "d2rbf" | "d2sp" | "d2au" | "d2au3" | "probe" | "probeb" | "spfit" | "spid" | "univ" | "rel",
          "request_id": "<new, never used before>",
          "seed": <int>, "branch": "research/v60-datapath-floor" (spid: "sm/spidman"; univ: "univ/u1b"; rel: "release/s32-v1"),
          "approval": "<who approved, when>"}
Rules:
  * the push is an ordinary fast-forward on the expected branch (no new
    branch, no force push). The expected branch is research/v60-datapath-floor for every job except spid, which is accepted ONLY on
    sm/spidman (SM1, Michael 2026-11-12: one Spider-Man fit from its own branch, no merge into the research branch), and univ, which is
    accepted ONLY on univ/u1b (U1b, Michael 2026-11-14: "GO for U1b preparation"; the universal core builds from its own branch); a job
    pushed to any branch other than its own is rejected (a GA2 job on sm/spidman or univ/u1b, spid or univ on the research branch, ...);
  * exactly one request file is added or modified; deleting one is not a request;
  * the file name matches "job"; "branch" matches the pushed ref;
  * seed: hwt 1 only; f2 1, 2 or 3; d1 1 only; pll 1 only; d2 1 only (first Pocket-top fit; more seeds need a decision); d2rbf 1 only (the D2 flow plus one assembler step, tied to the
    timing-closed seed-1 configuration; another seed would not be that configuration); d2sp 1, 2 or 3 (GA2Play2: the d2rbf flow for the sp configuration; one self-reference per seed, reference/d2_sp[_seedN]_config_reference.json); d2au 1, 2 or 3 (exp/audio1, GA2Audio1: the d2sp flow for
    the au configuration, sp + sound; one self-reference per seed, reference/d2_au_seedN_config_reference.json; seed 2 = GA2Play8's seed, 1 / 3 approved after run 43657433253); d2au3 1, 2 or 3 (exp/audio3, GA2Audio3: the d2au flow
    for the au3 configuration, the Z80 sound ROM in PSRAM chip 1; one self-reference per seed, reference/d2_au3_seedN_config_reference.json;
    seeds 1-3 pre-approved 2026-11-15); probe 1 only; probeb 1 only; spfit 1 only (standalone SP fit); spid 1 only (SpidPlay1: the d2sp flow for gen_d2.py --config spid); univ 1, 2 or 3 (U1b: the d2sp flow for
    gen_d2.py --config univ, one self-reference per seed, reference/d2_univ[_seedN]_config_reference.json, plus the U1 budget gate);
    rel 1, 2 or 3 (release/s32-v1: the d2au3 flow for gen_d2.py --config rel, one self-reference per seed,
    reference/d2_rel_seedN_config_reference.json, plus the U1 budget gate at the release limits; each seed still needs its own approval);
  * approval is a non-empty string that does not start with "PENDING" (the drafts' placeholder);
  * request_id has never appeared in any earlier version of any request file.

Usage: ci_request.py <before-sha> <after-sha> <ref>
Prints job=<hwt|f2|d1|pll|d2|d2rbf|d2sp|d2au|d2au3|probe|probeb|spfit|spid|univ|rel|none> and seed=<n> lines for $GITHUB_OUTPUT. Exit 1 on an
invalid request (job=none is printed first, so nothing downstream runs).
"""
import json
import re
import subprocess
import sys

BRANCH = "research/v60-datapath-floor"
JOB_BRANCH = {"spid": "sm/spidman", "univ": "univ/u1b", "rel": "fit/s32-dmap"}   # fit/s32-dmap (Michael 2026-11-22): rel only on the PRIVATE fit branch of the demand-mapped sprite-RAM window + video-option bit (one seed-1 fit; never a release)   # jobs with their own branch; every other job is accepted only on BRANCH
BRANCHES = (BRANCH,) + tuple(sorted(set(JOB_BRANCH.values())))
DIR = "experiments/build-requests/"
SEEDS = {"hwt": {1}, "f2": {1, 2, 3}, "d1": {1}, "pll": {1}, "d2": {1}, "d2rbf": {1}, "d2sp": {1, 2, 3}, "d2au": {1, 2, 3}, "d2au3": {1, 2, 3},
         "probe": {1}, "probeb": {1}, "spfit": {1}, "spid": {1}, "univ": {1, 2, 3}, "rel": {1, 2, 3}}


def git(*a):
    return subprocess.run(["git", *a], check=True, capture_output=True, text=True).stdout


def fail(msg):
    print("job=none")
    print("seed=0")
    print(f"REQUEST REJECTED: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    before, after, ref = sys.argv[1:4]
    if ref not in tuple(f"refs/heads/{b}" for b in BRANCHES):
        fail(f"ref {ref} is not one of {BRANCHES}")
    if re.fullmatch(r"0+", before):
        fail("new branch push: not a request")
    if subprocess.run(["git", "merge-base", "--is-ancestor", before, after]).returncode != 0:
        fail("not a fast-forward push")
    changed = [ln.split("\t") for ln in git("diff", "--name-status", before, after, "--", DIR).splitlines()]
    reqs = [(st, path) for st, path in changed if path.endswith(".json") and st[0] in "AM"]
    if not reqs:
        print("job=none")
        print("seed=0")
        print("no request in this push", file=sys.stderr)
        return 0
    if len(reqs) != 1:
        fail(f"{len(reqs)} request files changed; exactly one allowed")
    path = reqs[0][1]
    try:
        r = json.loads(git("show", f"{after}:{path}"))
    except json.JSONDecodeError as e:
        fail(f"{path}: invalid JSON ({e})")
    job = r.get("job")
    if job not in SEEDS or path != f"{DIR}{job}.json":
        fail(f"{path}: job {job!r} does not match the file")
    want = JOB_BRANCH.get(job, BRANCH)
    if ref != f"refs/heads/{want}":
        fail(f"job {job} is accepted only on {want}, not on {ref}")
    if r.get("branch") != want:
        fail(f"branch {r.get('branch')!r} is not {want}")
    seed = r.get("seed")
    if type(seed) is not int or seed not in SEEDS[job]:     # bool is an int subclass: excluded
        fail(f"seed {r.get('seed')!r} not allowed for {job} ({sorted(SEEDS[job])})")
    rid = r.get("request_id")
    if not isinstance(rid, str) or not rid.strip():
        fail("request_id missing")
    if not isinstance(r.get("approval"), str) or not r["approval"].strip():
        fail("approval missing or not a string")
    if r["approval"].strip().upper().startswith("PENDING"):       # an uncompleted draft (d2.json.draft) must not start a build
        fail("approval text is a PENDING placeholder: the owner's go has not been recorded")
    for f in (f"{DIR}{j}.json" for j in SEEDS):
        for c in git("rev-list", before, "--", f).split():
            try:
                old = json.loads(git("show", f"{c}:{f}"))
            except (subprocess.CalledProcessError, json.JSONDecodeError):
                continue
            if old.get("request_id") == rid:
                fail(f"request_id {rid!r} was already used (commit {c[:7]})")
    print(f"job={job}")
    print(f"seed={r['seed']}")
    print(f"REQUEST ACCEPTED: {job} seed {r['seed']} id {rid!r} ({r['approval']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
