#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""m1_ref_ext: extension of the pinned Python reference (ref/v60_ref.py, kept
byte-identical) with the census- and boot-priority instructions it lacks.

!!! REDUCED INDEPENDENCE !!!
Every semantic below is DERIVED FROM MAME's V60 core (mamedev/mame tag
mame0289, d0b7160e548, src/devices/cpu/v60/; cited per instruction as
file:line). Upstream s32_v60 was ALSO written from MAME. For these
instructions the two references therefore share MAME's interpretation: an
error in MAME would appear in both and lock-step agreement would not reveal
it. Results for them are "agrees with the MAME-derived references", never
"architecturally verified". The independent check is
experiments/S1-refext/independent_vectors.py (derived from NEC's V60
Programmer's Reference Manual, committed before this file), run by
experiments/S1-refext/run_vectors.py.

Known MAME/manual differences (from the vectors; recorded, not resolved here):
  * TEST1/SET1/CLR1/NOT1 with a bit offset >= 32: the manual raises an Illegal
    Data Field exception; MAME computes 1 << offset (C undefined; wraps modulo
    32 on x86). This model follows MAME (offset & 31) and marks it.

Instructions added (opcode: MAME source):
  INC.B/H/W  D8-DD        op3.hxx:3-70      (ADDB/ADDW/ADDL dst,1: flags as ADD)
  DEC.B/H/W  D0-D5        op3.hxx:72-140    (SUB dst,1: flags as SUB)
  TEST1      87           op12.hxx:2144     CY = bit, Z = !CY, OV/S unchanged
  SET1/CLR1/NOT1 97/A7/B7 op12.hxx:1575/476/1143  as TEST1, then set/clear/flip
  DBcc, TB   C6/C7 xx     op6.hxx           decrement then test (TB: no decrement,
                                             taken iff Rn == 0); disp16 at PC+2
  RSR        CA           op5.hxx:70        PC <- [SP+]
  PUSHM      EC/ED        op3.hxx:475       PSW (list bit 31) first, then R30..R0
  POPM       E4/E5        op3.hxx:449       R0..R30, then PSW (lower halfword only)
  RETIS      FA/FB        op3.hxx:326       PC <- [SP+], PSW <- [SP+], SP += count,
                                             then the PSW write with stack switch
  MOVEA      40/42/44     op12.hxx:836    R3a CORRECTION (docs/r3-movea-plan.md section 2): dst <- the EFFECTIVE
                                             ADDRESS of operand 1 (an address-type decode; manual p. 7-59), not its
                                             contents; no memory access, no flag change. Only the legal in-model
                                             forms: Format I (manual; V2 "F2") d = 1 and Format II (V2 "F1") with a
                                             memory-address source. A register / immediate source (illegal on the
                                             manual's table; MAME and upstream give different results) and d = 0
                                             raise NotImplementedError: outside this model's contract. The base
                                             v60_ref MOVEA (a value decode, retained discrepancy D1) is NOT modified.
  UPDPSW.W   13           op12.hxx:2154     mask and value limited to bits 23:0
  UPDPSW.H   4A           op12.hxx:2166     mask and value limited to bits 15:0
  LDPR       12           op12.hxx:731      PrivReg(regID) <- src, regID <= 28; src decoded
                                             as an ADDRESS operand (ReadAMAddress): a register
                                             source is read at execution (after regID's
                                             addressing side effects), a memory source gives
                                             its ADDRESS (manual: its contents; OPEN_ISSUES OI-2)
  STPR       02           op12.hxx:2051     dst <- PrivReg(regID) (for readback in S1 tests)
  PSW write  (all above)  v60.cpp:294       IS change, or EL change while IS=0:
                                             SP saved to / reloaded from ISP or LnSP
No exceptions are modelled (privileged-instruction checks, Illegal Data Field).

F12 operand order (op12.hxx:205-231, Codex S1 review #2): in format II with
the D flag set, a VALUE-type register operand 2 is sampled BEFORE operand 1
is decoded, so operand 1's autoincrement/decrement of the same register does
not affect it; an ADDRESS-type operand 2 (a destination) stays a register
reference.

Starting state: PSW 0x10000000 (as MAME device_reset), privileged registers as
MAME's reset (v60.cpp:542-551 and the V60 PIR 0x6000, v60.cpp:90): SBR 0,
SYCW 0x70, TKCW 0xE000, PSW2 0xF002, PIR 0x6000; the others 0 (MAME leaves
them uninitialized).

Departures from MAME (deliberate, tested):
  * LDPR with an IMMEDIATE source other than format I + 0xF4: MAME's
    am1Immediate/am1ImmediateQuick leave m_amflag unchanged, so whether LDPR
    reads the immediate or a register depends on the PREVIOUS decode (a MAME
    artefact). This model uses the immediate value (the manual; OI-3).
  * LDPR/STPR register IDs 10-14 (and > 28): MAME stores reserved slots (or
    stops); outside this model's contract: NotImplementedError.
Supported operand forms: those of the pinned decoder (v60_ref.decode_am);
indexed modes ([Rn](Rx) etc.) raise NotImplementedError (outside the contract).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "ref"))
import v60_ref as R                                   # noqa: E402  (pinned copy, unmodified)
from v60_ref import M32, sx, dimext                  # noqa: E402

# privileged register IDs (manual Figure 3-2; MAME m_reg[36 + id])
PRIV = {0: "ISP", 1: "L0SP", 2: "L1SP", 3: "L2SP", 4: "L3SP", 5: "SBR", 6: "TR", 7: "SYCW", 8: "TKCW",
        9: "PIR", 15: "PSW2", 16: "ATBR0", 17: "ATLR0", 18: "ATBR1", 19: "ATLR1", 20: "ATBR2", 21: "ATLR2",
        22: "ATBR3", 23: "ATLR3", 24: "TRMOD", 25: "ADTR0", 26: "ADTR1", 27: "ADTMR0", 28: "ADTMR1"}

# DBcc sub-tables (op6.hxx:219-240): index = byte >> 5, register = byte & 0x1f
C6 = ["dbv", "dbl", "dbe", "dbnh", "dbn", "dbr", "dblt", "dble"]
C7 = ["dbnv", "dbnl", "dbne", "dbh", "dbp", "tb", "dbge", "dbgt"]
COND = {"dbv": lambda c: c.ov, "dbnv": lambda c: not c.ov, "dbl": lambda c: c.cy, "dbnl": lambda c: not c.cy,
        "dbe": lambda c: c.z, "dbne": lambda c: not c.z, "dbnh": lambda c: c.cy or c.z,
        "dbh": lambda c: not (c.cy or c.z), "dbn": lambda c: c.s, "dbp": lambda c: not c.s,
        "dbr": lambda c: True, "dblt": lambda c: c.s ^ c.ov, "dbge": lambda c: not (c.s ^ c.ov),
        "dble": lambda c: (c.s ^ c.ov) or c.z, "dbgt": lambda c: not ((c.s ^ c.ov) or c.z)}

# bit operations: name -> (opcode, operand-2 is an address (store), new bit value fn)
BITOPS = {0x87: ("test1", False, None), 0x97: ("set1", True, lambda b: 1),
          0xA7: ("clr1", True, lambda b: 0), 0xB7: ("not1", True, lambda b: b ^ 1)}


class V60X(R.V60):
    def __init__(self, mem, start_pc=0):
        super().__init__(mem, start_pc)
        self.priv = {n: 0 for n in PRIV.values()}
        self.priv.update(SYCW=0x70, TKCW=0xE000, PSW2=0xF002, PIR=0x6000)   # MAME reset (see header)

    # ---- PSW write with MAME's stack switch (v60.cpp:271-320) ----
    def _save_stack(self):
        if self.psw_hi & 0x10000000:
            self.priv["ISP"] = self.r[31]
        else:
            self.priv[f"L{(self.psw_hi >> 24) & 3}SP"] = self.r[31]

    def _reload_stack(self):
        if self.psw_hi & 0x10000000:
            self.r[31] = self.priv["ISP"]
        else:
            self.r[31] = self.priv[f"L{(self.psw_hi >> 24) & 3}SP"]

    def write_psw(self, new):
        old = self.psw()
        upd = bool((new ^ old) & 0x10000000) or (not (old & 0x10000000) and bool((new ^ old) & 0x03000000))
        if upd:
            self._save_stack()
        self.psw_hi = new & ~0xF & M32
        self.z, self.s, self.ov, self.cy = new & 1, (new >> 1) & 1, (new >> 2) & 1, (new >> 3) & 1
        if upd:
            self._reload_stack()

    # ---- F1/F2 operand decode for the added F12 instructions (as v60_ref._exec_f12) ----
    def _f12_operands(self, d1, d2, op2_addr):
        iflags = self._b(1)
        if iflags & 0x80:                                # F1
            self._cur_modm = (iflags >> 6) & 1
            o1 = self.decode_am(2, d1, False); self._d2 = d1
            v1 = self.load(o1)
            self._cur_modm = (iflags >> 5) & 1
            o2 = self.decode_am(2 + o1[2], d2, op2_addr)
            ln = o1[2] + o2[2] + 2
        elif iflags & 0x20:                              # F2 D=1: op2 = register
            # a value operand is sampled now, before op1's side effects (op12.hxx:205-231)
            o2 = ('reg', iflags & 0x1F, 0) if op2_addr else ('imm', dimext(self.r[iflags & 0x1F], d2), 0)
            self._cur_modm = (iflags >> 6) & 1
            o1 = self.decode_am(2, d1, False); v1 = self.load(o1)
            ln = o1[2] + 2
        else:                                            # F2 D=0: op1 = register
            v1 = dimext(self.r[iflags & 0x1F], d1)
            self._cur_modm = (iflags >> 6) & 1
            o2 = self.decode_am(2, d2, op2_addr)
            ln = o2[2] + 2
        self._d2 = d2
        return v1, o2, ln

    def _val(self, o, d):
        if o[0] == 'reg':
            return dimext(self.r[o[1]], d)
        if o[0] == 'mem':
            return self.mem.r(o[1], d)
        return o[1]

    def step(self):
        op = self._b(0)
        self._cur_modm = 0
        # INC / DEC (op3.hxx): one operand, modm = opcode bit 0, size from the opcode pair
        if 0xD0 <= op <= 0xD5 or 0xD8 <= op <= 0xDD:
            d = ((op & 7) >> 1) if op >= 0xD8 else ((op - 0xD0) >> 1)
            self._cur_modm = op & 1; self._d2 = d
            o = self.decode_am(1, d, True)
            v = self._val(o, d)
            (R._add if op >= 0xD8 else R._sub)(self, 1, v, o, d)
            self.pc += 1 + o[2]
            return
        # bit operations (op12.hxx)
        if op in BITOPS:
            name, store, fn = BITOPS[op]
            v1, o2, ln = self._f12_operands(2, 2, store)
            word = self._val(o2, 2)
            bit = v1 & 31                     # MAME: 1 << offset (see the module header)
            old = (word >> bit) & 1
            self.cy, self.z = old, 1 - old
            if store:
                new = (word & ~(1 << bit)) | (fn(old) << bit)
                self.store(o2, new & M32, 2)
            self.pc += ln
            return
        # DBcc / TB (op6.hxx)
        if op in (0xC6, 0xC7):
            sub = self._b(1)
            name = (C6 if op == 0xC6 else C7)[sub >> 5]
            reg = sub & 0x1F
            disp = sx(self._w(2), 16)
            if name == "tb":
                taken = self.r[reg] == 0
            else:
                self.r[reg] = (self.r[reg] - 1) & M32
                taken = self.r[reg] != 0 and bool(COND[name](self))
            self.pc = (self.pc + disp) & M32 if taken else self.pc + 4
            return
        if op == 0xCA:                         # RSR (op5.hxx:70)
            self.pc = self.mem.r(self.r[31], 2)
            self.r[31] = (self.r[31] + 4) & M32
            return
        if op in (0xEC, 0xED, 0xE4, 0xE5):     # PUSHM / POPM (op3.hxx:449-497)
            self._cur_modm = op & 1; self._d2 = 2
            o = self.decode_am(1, 2, False)
            lst = self.load(o) & M32
            if op in (0xEC, 0xED):
                if lst & (1 << 31):
                    self.r[31] = (self.r[31] - 4) & M32
                    self.mem.w(self.r[31], 2, self.psw())
                for i in range(30, -1, -1):
                    if lst & (1 << i):
                        self.r[31] = (self.r[31] - 4) & M32
                        self.mem.w(self.r[31], 2, self.r[i])
            else:
                for i in range(31):
                    if lst & (1 << i):
                        self.r[i] = self.mem.r(self.r[31], 2)
                        self.r[31] = (self.r[31] + 4) & M32
                if lst & (1 << 31):
                    self.write_psw((self.psw() & 0xFFFF0000) | self.mem.r(self.r[31], 1))
                    self.r[31] = (self.r[31] + 4) & M32
            self.pc += 1 + o[2]
            return
        if op in (0xFA, 0xFB):                 # RETIS (op3.hxx:326)
            self._cur_modm = op & 1; self._d2 = 1
            o = self.decode_am(1, 1, False)
            count = self.load(o) & 0xFFFF
            self.pc = self.mem.r(self.r[31], 2)
            self.r[31] = (self.r[31] + 4) & M32
            new = self.mem.r(self.r[31], 2)
            self.r[31] = (self.r[31] + 4) & M32
            self.r[31] = (self.r[31] + count) & M32
            self.write_psw(new)
            return
        if op in (0x40, 0x42, 0x44):           # MOVEA (op12.hxx:836; R3a correction, see the header)
            d1 = (op - 0x40) >> 1                       # B / H / W: scale or step of auto / indexed modes only
            iflags = self._b(1)
            if iflags & 0x80:                            # Format II (V2 F1): op1 address operand, op2 destination
                self._cur_modm = (iflags >> 6) & 1
                o1 = self.decode_am(2, d1, True)
                self._cur_modm = (iflags >> 5) & 1
                o2 = self.decode_am(2 + o1[2], 2, True)
                ln = o1[2] + o2[2] + 2
            elif iflags & 0x20:                          # Format I d = 1: op2 = register
                o2 = ('reg', iflags & 0x1F, 0)
                self._cur_modm = (iflags >> 6) & 1
                o1 = self.decode_am(2, d1, True)
                ln = o1[2] + 2
            else:
                raise NotImplementedError("MOVEA Format I d = 0 (register source): illegal, outside the contract")
            if o1[0] != 'mem':
                raise NotImplementedError("MOVEA register / immediate source: illegal (manual p. 7-59), outside the contract")
            self.store(o2, o1[1] & M32, 2)               # the full 32-bit address: no extension, no byte mask
            self.pc += ln
            return
        if op in (0x13, 0x4A):                 # UPDPSW.W / .H (op12.hxx:2154-2177)
            v1, o2, ln = self._f12_operands(2, 2, False)
            mask = self._val(o2, 2) & (0xFFFFFF if op == 0x13 else 0xFFFF)
            v1 &= 0xFFFFFF if op == 0x13 else 0xFFFF
            self.write_psw((self.psw() & ~mask & M32) | (v1 & mask))
            self.pc += ln
            return
        if op == 0x12:                         # LDPR (op12.hxx:731): src (address operand), regID
            iflags = self._b(1)
            if iflags & 0x80:                                # F1: src, then regID
                self._cur_modm = (iflags >> 6) & 1
                o1 = self.decode_am(2, 2, True)
                self._cur_modm = (iflags >> 5) & 1
                o2 = self.decode_am(2 + o1[2], 2, False)
                ln = o1[2] + o2[2] + 2
            elif iflags & 0x20:                              # F2 D=1: regID = register (value, sampled first)
                o2 = ('imm', self.r[iflags & 0x1F], 0)
                self._cur_modm = (iflags >> 6) & 1
                o1 = self.decode_am(2, 2, True)
                ln = o1[2] + 2
            else:                                            # F2 D=0: src = register, read at execution
                o1 = ('reg', iflags & 0x1F, 0)
                self._cur_modm = (iflags >> 6) & 1
                o2 = self.decode_am(2, 2, False)
                ln = o2[2] + 2
            rid = self._val(o2, 2)
            if rid > 28 or rid not in PRIV:
                raise NotImplementedError(f"LDPR register ID {rid}: outside the contract (MAME: reserved slot or fatal)")
            # register: its value now; memory: the ADDRESS (MAME; the manual reads the contents: OI-2);
            # immediate: the value (manual; MAME's result depends on a stale flag: OI-3)
            src = self.r[o1[1]] if o1[0] == 'reg' else o1[1]
            self.priv[PRIV[rid]] = src & M32
            self.pc += ln
            return
        if op == 0x02:                         # STPR (op12.hxx:2051): regID (value), dst
            v1, o2, ln = self._f12_operands(2, 2, True)
            if v1 > 28 or v1 not in PRIV:
                raise NotImplementedError(f"STPR register ID {v1}: outside the contract")
            self.store(o2, self.priv[PRIV[v1]], 2)
            self.pc += ln
            return
        return super().step()


def run(img: bytes, max_instrs=200000, start_pc=0, base=0, mirror24=False, rom_mirror=False):
    """As m1_ref.run, on the extended model. R1a options (defaults unchanged):
    base = load address of the image; mirror24 = 16 MB memory addressed mod 2^24
    (the 24-bit physical bus); rom_mirror = also S0's 0xF00000-0xFFFFFF ->
    0x000000-0x0FFFFF mirror (see m1_ref.MaskedBuf)."""
    m = R.Mem()
    if mirror24 or rom_mirror:
        from m1_ref import MaskedBuf
        m.b = MaskedBuf(rom_mirror)
    if mirror24 or rom_mirror or base:
        for i, b in enumerate(img):
            m.b[base + i] = b
    else:
        m.b[:len(img)] = img
    cpu = V60X(m, start_pc)
    n = 0
    while not cpu.halted and n < max_instrs:
        cpu.step()
        n += 1
    return cpu, m, n
