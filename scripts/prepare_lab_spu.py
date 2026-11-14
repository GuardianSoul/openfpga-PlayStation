#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""0.6.0 LAB-room step "lab-spu" on a frozen snapshot's save-states/spu.vhd (docs/integration-060.md).

Part rv-addr: the reverb RAM address was
    reqAddr2 = (reqAddr & "00") - 1   (mSAME / mDIFF reads, 18-bit wrap) else reqAddr & "00"
    reverbAddAddr = ('0' & reqAddr2) + ('0' & reverbCurrentAddress)        (19 bit)
an 18-bit decrementer and a mux in front of the adder. Now one three-operand add:
    reverbAddAddr = A + ('0' & reverbCurrentAddress) + (19 x dec)
    A = (dec and reqAddr = 0) & reqAddr & "00"
Adding 19 ones is -1 mod 2^19. The 18-bit wrap of the old decrement (reqAddr = 0 gives 2^18 - 1)
is the extra 2^18 in A's bit 18. reverb_calcAddr (the mBASE fold) is unchanged.

Part rv-cse: Quartus builds one adder / comparator set per written expression (no resource sharing
across the states of the reverb sequence). The same expressions on the same signals become one
concurrent signal each:
    clamp16(shift_right(soundmulresult14 + soundmulresult14_1, 1))  4 copies (IIR_INPUT_A/B, IIR_A/B)
    shift_right(soundmulresult14 + soundmulresult14_1, 1)          inside MDB
    clamp16(resize(signed(rv_work_q), 25) + soundmulresult15)       2 copies (IVB left / right)
    output saturation of soundmulresult15                          2 copies (left / right)
and the reverb input clamp16(sum + cd) / clamp16(sum) pair becomes clamp16(sum + (cd or 0)).
All operands are signals (registers, a RAM output), so each shared signal has the value the inline
expression had in the same clock.

Check: scripts/run_lab_spu.py (directed reverb lockstep, RC-g's own spu.vhd as reference; planted
faults).
Usage: prepare_lab_spu.py SOURCE --output OUTPUT [--parts rv-addr,rv-cse] [--seed N]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'lab-spu'
PARTS = ('rv-addr', 'rv-cse', 'rv-cse2', 'rv-ram')
EXTRA_PARTS = ('rv-gs',)   # rv-ram without the engine copy move (K3 s6: its half-cycle read failed timing)

ADDR = [
    ('   reverb_reqAddr2 <= ((reverb_reqAddr & "00") - 1) when (state = REVERB_READ1 and (reverb_count = 2 or reverb_count = 3)) else\n'
     '                      (reverb_reqAddr & "00");                    \n'
     '                 \n'
     "   reverbAddAddr   <= ('0' & reverb_reqAddr2) + ('0' & reverbCurrentAddress);\n",
     f'   -- {TAG} rv-addr: the mSAME / mDIFF decrement (18-bit wrap) folded into the address add\n'
     "   reverb_dec      <= '1' when (state = REVERB_READ1 and (reverb_count = 2 or reverb_count = 3)) else '0';\n"
     "   reverb_addA     <= ('1' & reverb_reqAddr & \"00\") when (reverb_dec = '1' and reverb_reqAddr = 0) else ('0' & reverb_reqAddr & \"00\");\n"
     "   reverb_decMask  <= (others => reverb_dec);\n"
     "   reverbAddAddr   <= reverb_addA + ('0' & reverbCurrentAddress) + reverb_decMask;\n", 1),
    ('   signal reverb_reqAddr2        : unsigned(17 downto 0);\n',
     f'   signal reverb_dec             : std_logic;              -- {TAG}\n'
     '   signal reverb_addA            : unsigned(18 downto 0);\n'
     '   signal reverb_decMask         : unsigned(18 downto 0);\n', 1),
]

PAIR = 'clamp16(shift_right(soundmulresult14 + soundmulresult14_1, 1))'
OUT_SAT = ('                  if (soundmulresult15 < -32768) then sound_out_{s} <= x"8000";\n'
           '                  elsif (soundmulresult15 > 32767) then sound_out_{s} <= x"7FFF";\n'
           '                  else sound_out_{s} <= std_logic_vector(soundmulresult15(15 downto 0));\n'
           '                  end if;\n')
CD_OLD = ("                        if (cnt(0) = '1' and cnt(2) = '1') then\n"
          "                           reverbInputLeft_v  := clamp16(reverbsumleft  + resize(cd_next_left, 24));\n"
          "                           reverbInputRight_v := clamp16(reverbsumright + resize(cd_next_right, 24));\n"
          "                        else\n"
          "                           reverbInputLeft_v  := clamp16(reverbsumleft);\n"
          "                           reverbInputRight_v := clamp16(reverbsumright);\n"
          "                        end if;\n")
CD_NEW = ("                        if (cnt(0) = '1' and cnt(2) = '1') then\n"
          "                           rv_cdl_v := resize(cd_next_left, 24); rv_cdr_v := resize(cd_next_right, 24);\n"
          "                        else\n"
          "                           rv_cdl_v := (others => '0'); rv_cdr_v := (others => '0');\n"
          "                        end if;\n"
          "                        reverbInputLeft_v  := clamp16(reverbsumleft  + rv_cdl_v);\n"
          "                        reverbInputRight_v := clamp16(reverbsumright + rv_cdr_v);\n")
CSE = [
    ('   signal soundmulresult15       : signed(24 downto 0);\n',
     '   signal soundmulresult15       : signed(24 downto 0);\n'
     f'   signal rv_pair26              : signed(25 downto 0);   -- {TAG} rv-cse: shared reverb sums / clamps\n'
     '   signal rv_pairc               : signed(15 downto 0);\n'
     '   signal rv_ivc                 : signed(15 downto 0);\n'
     '   signal rv_out15c              : signed(15 downto 0);\n', 1),
    ('clamp16(resize(signed(rv_work_q), 26) + shift_right(soundmulresult14 + soundmulresult14_1, 1))',
     'clamp16(resize(signed(rv_work_q), 26) + rv_pair26)', 1),
    (PAIR, 'rv_pairc', 4),
    ('clamp16(resize(signed(rv_work_q), 25) + soundmulresult15)', 'rv_ivc', 2),
    (OUT_SAT.format(s='left'), '                  sound_out_left <= std_logic_vector(rv_out15c);\n', 1),
    (OUT_SAT.format(s='right'), '                  sound_out_right <= std_logic_vector(rv_out15c);\n', 1),
    ('      variable reverbInputRight_v  : signed(15 downto 0);\n',
     '      variable reverbInputRight_v  : signed(15 downto 0);\n'
     f'      variable rv_cdl_v, rv_cdr_v  : signed(23 downto 0);   -- {TAG} rv-cse\n', 1),
    (CD_OLD, CD_NEW, 1),
    ('   SS_RAM_dataRead   <= ram_dataRead;\n',
     f'   -- {TAG} rv-cse: one adder / clamp each, read by every state that wrote its own copy\n'
     '   rv_pair26 <= shift_right(soundmulresult14 + soundmulresult14_1, 1);\n'
     '   rv_pairc  <= clamp16(rv_pair26);\n'
     '   rv_ivc    <= clamp16(resize(signed(rv_work_q), 25) + soundmulresult15);\n'
     '   rv_out15c <= clamp16(soundmulresult15);\n\n'
     '   SS_RAM_dataRead   <= ram_dataRead;\n', 1),
]

# rv-cse2: the rest of the repeats in the reverb sequence (scan of RC-g's spu.vhd)
CSE2 = [
    ('reverb_acc <= reverb_acc + resize(soundmulresult14, 20);', 'reverb_acc <= rv_accnext;', 3),
    ('soundmul2 <= to_signed(32768, 18) - resize(signed(rv_engine_q), 18);', 'soundmul2 <= rv_iiasm;', 2),
    ('   apf2neg <= x"7FFF" when (rv_engine_q = x"8000") else (to_signed(0, 16) - signed(rv_engine_q));\n',
     '   apf2neg <= apf1neg;   -- lab-spu rv-cse2: the same expression as apf1neg\n', 1),
    ('   signal reverb_acc             : signed(19 downto 0);\n',
     '   signal reverb_acc             : signed(19 downto 0);\n'
     f'   signal rv_accnext             : signed(19 downto 0);   -- {TAG} rv-cse2\n'
     '   signal rv_iiasm               : signed(17 downto 0);\n', 1),
    ('   SS_RAM_dataRead   <= ram_dataRead;\n',
     f'   rv_accnext <= reverb_acc + resize(soundmulresult14, 20);   -- {TAG} rv-cse2\n'
     '   rv_iiasm   <= to_signed(32768, 18) - resize(signed(rv_engine_q), 18);\n\n'
     '   SS_RAM_dataRead   <= ram_dataRead;\n', 1),
]

# rv-ram (room list, 2026-11-13): staticRb and the guest reverb copy share one 64x16 M10K, indexed
# {bus_addr(6), register} (their writes are exclusive by bus_addr(6) and a bus read selects the half the
# same way), which frees the block that takes the engine copy out of MLAB. The engine copy is read on the
# falling edge of clk1x, as the guest copy already is: rv_engine_addr comes from rising-edge state, writes
# are on the rising edge, so the value seen at the next rising edge is the same. M10K count unchanged,
# one MLAB LAB freed.
RVRAM = [
    ('   signal rvTable_guest  : trvTable;\n', '', 1),
    ('   attribute ramstyle of rvTable_guest  : signal is "M10K, no_rw_check";\n', '', 1),
    ('   attribute ramstyle of rvTable_engine : signal is "MLAB, no_rw_check";\n',
     '   attribute ramstyle of rvTable_engine : signal is "M10K, no_rw_check";   -- lab-spu rv-ram\n', 1),
    ('   signal staticRb       : trvTable;\n   attribute ramstyle of staticRb : signal is "M10K, no_rw_check";\n',
     '   -- lab-spu rv-ram: static readback (0-31) and guest reverb copy (32-63) in one block, index bus_addr(6 downto 1)\n'
     '   type trvGuestStatic is array(0 to 63) of std_logic_vector(15 downto 0);\n'
     '   signal rvGuestStatic  : trvGuestStatic;\n'
     '   attribute ramstyle of rvGuestStatic : signal is "M10K, no_rw_check";\n'
     '   signal rv_gs_ram_q    : std_logic_vector(15 downto 0);\n'
     '   signal rv_engine_ram_q : std_logic_vector(15 downto 0);\n', 1),
    ('                     staticRb(rv_guest_addr)       <= bus_dataWrite;\n', '', 1),
    ('                     rvTable_guest(rv_guest_addr)  <= bus_dataWrite;\n', '', 1),
    ('                  if (bus_addr(9 downto 6) = "0111" and bus_addr(0) = \'0\') then\n                     rvTable_engine(rv_guest_addr) <= bus_dataWrite;\n',
     '                  if (((bus_addr(9 downto 6) = "0110" and STATIC_RB(rv_guest_addr) = \'1\') or bus_addr(9 downto 6) = "0111") and bus_addr(0) = \'0\') then\n'
     '                     rvGuestStatic(to_integer(bus_addr(6 downto 1))) <= bus_dataWrite;   -- lab-spu rv-ram: one write port\n'
     '                  end if;\n'
     '                  if (bus_addr(9 downto 6) = "0111" and bus_addr(0) = \'0\') then\n'
     '                     rvTable_engine(rv_guest_addr) <= bus_dataWrite;\n', 1),
    ('         rv_guest_ram_q <= rvTable_guest(rv_guest_addr);\n         static_ram_q   <= staticRb(rv_guest_addr);\n      end if;\n   end process;\n',
     '         rv_gs_ram_q    <= rvGuestStatic(to_integer(bus_addr(6 downto 1)));   -- lab-spu rv-ram\n'
     '      end if;\n   end process;\n'
     '   rv_guest_ram_q <= rv_gs_ram_q;   -- read only when bus_addr(9 downto 6) = "0111"\n'
     '   static_ram_q   <= rv_gs_ram_q;   -- read only when bus_addr(9 downto 6) = "0110"\n'
     '   -- lab-spu rv-ram: engine copy in block RAM, read on the falling edge like the guest copy\n'
     '   process (clk1x)\n   begin\n      if falling_edge(clk1x) then\n'
     '         rv_engine_ram_q <= rvTable_engine(rv_engine_addr);\n      end if;\n   end process;\n', 1),
    ("   rv_engine_q    <= rvTable_engine(rv_engine_addr) when rvValid(rv_engine_addr) = '1' else (others => '0');\n",
     "   rv_engine_q    <= rv_engine_ram_q when rvValid(rv_engine_addr) = '1' else (others => '0');\n", 1),
]

# rv-gs: only the staticRb + guest merge of rv-ram (frees one M10K, no LAB). The engine copy stays an MLAB
# read asynchronously: K3 s6 (#1474) failed clk1x setup -0.946 ns from the falling-edge engine read into the
# reverb address add (spu|ram_Adr).
RVGS = [r for r in RVRAM if 'rvTable_engine : signal is' not in r[0] and 'rv_engine_q    <=' not in r[0]]
RVGS = [(o, n.replace('   signal rv_engine_ram_q : std_logic_vector(15 downto 0);\n', ''), c) for o, n, c in RVGS]
RVGS = [(o, n.split('   -- lab-spu rv-ram: engine copy in block RAM')[0], c) if 'engine copy in block RAM' in n else (o, n, c)
        for o, n, c in RVGS]
RVGS = [(o, n.replace('lab-spu rv-ram', 'lab-spu rv-gs'), c) for o, n, c in RVGS]

FAULTS = {
    'no-wrap': ('rv-addr', "('1' & reverb_reqAddr & \"00\") when (reverb_dec = '1' and reverb_reqAddr = 0)",
                "('0' & reverb_reqAddr & \"00\") when (reverb_dec = '1' and reverb_reqAddr = 0)"),
    'dec-all-reads': ('rv-addr', "reverb_dec      <= '1' when (state = REVERB_READ1 and (reverb_count = 2 or reverb_count = 3)) else '0';",
                      "reverb_dec      <= '1' when (state = REVERB_READ1 and (reverb_count >= 2)) else '0';"),
    'pair-no-shift': ('rv-cse', '   rv_pair26 <= shift_right(soundmulresult14 + soundmulresult14_1, 1);',
                      '   rv_pair26 <= soundmulresult14 + soundmulresult14_1;'),
    'iv-sign': ('rv-cse', '   rv_ivc    <= clamp16(resize(signed(rv_work_q), 25) + soundmulresult15);',
                '   rv_ivc    <= clamp16(resize(signed(rv_work_q), 25) - soundmulresult15);'),
    'out-half': ('rv-cse', '   rv_out15c <= clamp16(soundmulresult15);', '   rv_out15c <= clamp16(shift_right(soundmulresult15, 1));'),
    'acc-sub': ('rv-cse2', '   rv_accnext <= reverb_acc + resize(soundmulresult14, 20);',
                '   rv_accnext <= reverb_acc - resize(soundmulresult14, 20);'),
    'iiasm-off': ('rv-cse2', '   rv_iiasm   <= to_signed(32768, 18) - resize(signed(rv_engine_q), 18);',
                  '   rv_iiasm   <= to_signed(32767, 18) - resize(signed(rv_engine_q), 18);'),
    'cd-always': ('rv-cse', "                           rv_cdl_v := (others => '0'); rv_cdr_v := (others => '0');",
                  "                           rv_cdl_v := resize(cd_next_left, 24); rv_cdr_v := (others => '0');"),
    'gs-half': ('rv-ram', '         rv_gs_ram_q    <= rvGuestStatic(to_integer(bus_addr(6 downto 1)));',
                "         rv_gs_ram_q    <= rvGuestStatic(to_integer('0' & bus_addr(5 downto 1)));"),
    'gs-half2': ('rv-gs', '         rv_gs_ram_q    <= rvGuestStatic(to_integer(bus_addr(6 downto 1)));',
                 "         rv_gs_ram_q    <= rvGuestStatic(to_integer('0' & bus_addr(5 downto 1)));"),
    'engine-late': ('rv-ram', '      if falling_edge(clk1x) then\n         rv_engine_ram_q',
                    '      if rising_edge(clk1x) then\n         rv_engine_ram_q'),
}


def _n(text, old, new, n):
    if text.count(old) != n:
        raise ValueError(f'{TAG}: expected {n} matches, found {text.count(old)}: {old.strip()[:90]}')
    return text.replace(old, new)


def spu_text(text, parts=PARTS):
    # rv-ram may go on a snapshot that already carries the other parts (candidate chain after s2b-spu)
    if any(p not in ('rv-ram', 'rv-gs') for p in parts) and TAG in text:
        raise ValueError(f'{TAG}: already applied')
    if 'rv-ram' in parts and 'rv-gs' in parts:
        raise ValueError(f'{TAG}: rv-gs is part of rv-ram')
    for marker in ('rv-ram', 'rv-gs'):
        if marker in parts and f'{TAG} {marker}' in text:
            raise ValueError(f'{TAG}: {marker} already applied')
    for part, rows in (('rv-addr', ADDR), ('rv-cse', CSE), ('rv-cse2', CSE2), ('rv-ram', RVRAM), ('rv-gs', RVGS)):
        if part in parts:
            for old, new, n in rows:
                text = _n(text, old, new, n)
    return text


def fault_text(text, fault):
    _, old, new = FAULTS[fault]
    return _n(text, old, new, 1)


def prepare(source, output, parts, seed=None):
    import prepare_save_checksum as cs
    return cs.prepare(source, output, seed, {'spu.vhd': lambda t: spu_text(t, parts)})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--parts', default=','.join(PARTS))
    parser.add_argument('--seed', type=int)
    args = parser.parse_args()
    parts = [p for p in args.parts.split(',') if p]
    if any(p not in PARTS + EXTRA_PARTS for p in parts):
        parser.error(f'parts: {PARTS}')
    try:
        seed, changed = prepare(args.source, args.output, parts, args.seed)
    except ValueError as error:
        raise SystemExit(str(error))
    print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed}), parts {",".join(parts)}')
    for path, (old, new) in changed.items():
        print(f'{TAG}: patched {path} (sha256 {old[:16]} -> {new[:16]})')
