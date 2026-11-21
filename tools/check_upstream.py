#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify every pinned submodule is at its UPSTREAM.lock commit and unmodified.

Exit status 0 when all pins match and work trees are clean, 1 otherwise.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pins():
    for line in (ROOT / "UPSTREAM.lock").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, path, url, commit = line.split()
        yield name, path, url, commit


def git(path, *args):
    return subprocess.run(["git", "-C", str(ROOT / path), *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def main():
    ok = True
    for name, path, _url, commit in pins():
        head = git(path, "rev-parse", "HEAD")
        # B1 tooling: CHECK_UPSTREAM_IGNORE_UNTRACKED=1 ignores untracked files (another agent's simulation may leave an output file in the tree);
        # tracked files must still be unmodified and the pin must match. Default unchanged.
        dirty = git(path, "status", "--porcelain", "--untracked-files=" + ("no" if os.environ.get("CHECK_UPSTREAM_IGNORE_UNTRACKED") == "1" else "all"))
        if head != commit:
            print(f"FAIL {name}: HEAD {head} != pinned {commit}")
            ok = False
        if dirty:
            print(f"FAIL {name}: working tree modified:\n{dirty}")
            ok = False
        if head == commit and not dirty:
            print(f"ok   {name} @ {commit[:12]} (clean)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
