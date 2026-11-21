#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare lock-step records from two executions.

Each retired instruction is one record, printed in order by m1_ref.py
--lockstep, tb_m1_up +lockstep and tb_m1_v2 +lockstep:

    L <n> pc=<instruction-start pc> op=<opcode>[ Rnn=<value> ...]

listing every general register the instruction changed. Comparing the
sequences checks the architectural instruction boundaries (each start PC,
which also fixes every length and branch target) and the register state
after every instruction, so a divergence is reported at the instruction
where it happens.

Usage: m1_lockstep.py <a> <b> [--prefix N] [--label-a A --label-b B]
  --prefix N  compare only the first N records (each side must have them)
Exit 0 only if the compared records are identical and, without --prefix,
both sides have the same number of records.
"""
import sys


def records(path):
    return [line.split(None, 1)[1].strip() for line in open(path) if line.startswith("L ")]


def main():
    argv = sys.argv[1:]

    def opt(name, default=None):
        if name in argv:
            i = argv.index(name)
            v = argv[i + 1]
            del argv[i:i + 2]
            return v
        return default
    la, lb = opt("--label-a", "a"), opt("--label-b", "b")
    prefix = opt("--prefix")
    a, b = records(argv[0]), records(argv[1])
    n = int(prefix) if prefix is not None else max(len(a), len(b))
    for i in range(n):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        if x != y:
            print(f"lockstep: DIFF at record {i}")
            print(f"  {la}: {x if x is not None else '(missing)'}")
            print(f"  {lb}: {y if y is not None else '(missing)'}")
            return 1
    print(f"lockstep: match ({n} records)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
