#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pre-flight elaboration of an E1 project with Verilator (no Quartus needed).

Reads a generated ga2_e1.qsf, expands its QIP files, and elaborates the
selected top with Verilator --lint-only. This only proves that the harness
connects to the pinned core and that the source list is complete for the
Verilog/SystemVerilog parts. It is not a synthesis result:

  * SIMULATION is defined, so upstream substitutes its own behavioural Z80
    stub for the VHDL T80 and behavioural models for altsyncram RAMs;
  * Quartus elaborates the real T80 (VHDL) and the altsyncram primitives.

Usage: lint.py <project-dir>
"""
import re
import subprocess
import sys
from pathlib import Path

ASSIGN = re.compile(r'^\s*set_global_assignment\s+-name\s+(\S+)\s+(.*?)\s*$')
JOIN = re.compile(r'\[file join (\$[:\w()]+|\S+)((?:\s+"?[^\]"]+"?)*)\s*\]')


def resolve(value, qip_dir, variables):
    value = value.strip()
    m = JOIN.fullmatch(value)
    if m:
        base, rest = m.group(1), m.group(2).split()
        base_dir = variables.get(base, qip_dir) if base.startswith("$") else Path(base)
        return Path(base_dir, *[p.strip('"') for p in rest])
    return Path(value.strip('"'))


def expand(qsf_or_qip, base, files, incdirs, variables=None):
    variables = dict(variables or {})
    for raw in Path(qsf_or_qip).read_text().splitlines():
        line = raw.strip()
        sv = re.match(r'^set\s+(\w+)\s+\$::quartus\(qip_path\)\s*$', line)
        if sv:
            variables["$" + sv.group(1)] = base
            continue
        m = ASSIGN.match(line)
        if not m:
            continue
        name, value = m.group(1), m.group(2)
        variables["$::quartus(qip_path)"] = base
        path = resolve(value, base, variables)
        if not path.is_absolute():
            path = (base / path).resolve()
        if name == "QIP_FILE":
            expand(path, path.parent, files, incdirs, variables)
        elif name in ("SYSTEMVERILOG_FILE", "VERILOG_FILE"):
            files.append(path)
        elif name == "SEARCH_PATH":
            incdirs.append(path)


def macros(qsf):
    out = []
    for line in Path(qsf).read_text().splitlines():
        m = ASSIGN.match(line)
        if m and m.group(1) == "VERILOG_MACRO":
            mac = m.group(2).strip('"')
            if not mac.startswith("SYNTHESIS"):
                out.append(mac)
    return out


def main():
    proj = Path(sys.argv[1]).resolve()
    qsf = proj / "ga2_e1.qsf"
    top = re.search(r"TOP_LEVEL_ENTITY (\S+)", qsf.read_text()).group(1)
    files, incdirs = [], []
    expand(qsf, proj, files, incdirs)
    missing = [f for f in files if not f.exists()]
    if missing:
        sys.exit("missing sources:\n" + "\n".join(map(str, missing)))
    cmd = ["verilator", "--lint-only", "-sv", "--timing", "--top-module", top,
           "-Wno-fatal", "-Wno-UNOPTFLAT",
           "-Wno-BLKANDNBLK", "-Wno-CASEINCOMPLETE", "-Wno-MULTIDRIVEN",
           "-Wno-INITIALDLY", "-Wno-DECLFILENAME", "-Wno-UNUSEDSIGNAL",
           "-Wno-UNUSEDPARAM", "-Wno-UNDRIVEN", "-Wno-PINCONNECTEMPTY",
           "-Wno-SYNCASYNCNET", "-Wno-GENUNNAMED", "-Wno-VARHIDDEN",
           "-Wno-IMPORTSTAR", "-Wno-PINMISSING", "-Wno-LATCH",
           "+define+SIMULATION"]
    cmd += [f"+define+{m}" for m in macros(qsf)]
    cmd += [f"-I{d}" for d in incdirs]
    cmd += [str(f) for f in files]
    print(f"verilator: top={top}, {len(files)} SV/V files, {len(incdirs)} include dirs")
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = r.stdout + r.stderr
    errors = [l for l in out.splitlines() if l.startswith("%Error")]
    width = [l for l in out.splitlines() if "%Warning-WIDTH" in l and "E1-baseline/rtl" in l]
    sys.stdout.write(out[-6000:])
    print(f"\nresult: rc={r.returncode}, {len(errors)} errors, "
          f"{len(width)} width warnings in E1 harness files")
    return 1 if (r.returncode or errors or width) else 0


if __name__ == "__main__":
    sys.exit(main())
