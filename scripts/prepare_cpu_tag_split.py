#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""cpu: instruction-cache tag RAM read at both next-fetch candidates (clk1x half-cycle timing).

The tag RAM captures FetchAddr(11 downto 4) on the falling clk1x edge, so
everything in front of FetchAddr has half a period (14.762 ns). Candidate D2
seed 6 missed it by 0.591 ns (Slow 85C) and 0.482 ns (Slow 0C) from
writebackTarget / lateReadTarget / decodeSource1: forwarding compare, value
multiplexer, the 32-bit branch compare, `branch`, the FetchAddr multiplexer,
then the M10K address register.

FetchAddr is  exception vector | PCbranch when branch = '1' | PC.  This step
reads two tag RAMs with the same write port:

   itagram     at  FetchAddrSeq(11 downto 4)   the same chain without the branch arm
   itagram_br  at  PCbranch(11 downto 4)

and captures the chain's choice (tagBranchNow: no exception arm taken and
branch = '1') in one flip-flop on the same falling edge. tag_q_b is
itagram_br's word when that flip-flop is set, else itagram's.

Exact for any input timing: let a, b, s be FetchAddrSeq(11:4), PCbranch(11:4)
and tagBranchNow at a falling edge. The old RAM captured (b when s else a)
and showed mem[that]. The new RAMs capture a and b, the flip-flop captures s,
and the output is (mem[b] when s else mem[a]): the same word. Both RAMs get
every write of the old one (same clock, address, data, enable), so their
contents are equal at all times, from the same power-up zeros. No read and
write share an edge (write rising, read falling), as before. Nothing else
reads the RAMs. No cycle is added.

What gets shorter: `branch` (the late term) ends in a flip-flop the fitter
can place anywhere instead of the fixed M10K, and loses the FetchAddr
multiplexer level; PCbranch reaches its RAM without the branch compare.
Cost: +1 M10K (256 x 24), 1 register, a 24-bit 2:1 multiplexer.

Check: scripts/run_cpu_tag_split.py.
Usage: prepare_cpu_tag_split.py SOURCE --output OUTPUT [--seed N] [--baseline]
       prepare_cpu_tag_split.py SOURCE --text-only OUT      (one cpu.vhd)
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TAG = 'cpu-tag-split'
# The reviewed input: build/ss/d2/cpu-div-shared/cpu.vhd of the D2 project (2026-11-11);
# the C and M projects compile the same file.
SOURCE_SHA = 'da6808b1b098f56e344b3a0079b1b0a5baceac9eea8637819d1129b5f2be17fc'

DECL_OLD = '   signal tag_q_b                      : std_logic_vector(23 downto 0);\n'
DECL_NEW = (DECL_OLD +
            f'   -- {TAG}: the tag RAM read at both next-fetch candidates\n'
            '   signal tag_address_br               : std_logic_vector(7 downto 0);\n'
            '   signal tag_q_seq                    : std_logic_vector(23 downto 0);\n'
            '   signal tag_q_br                     : std_logic_vector(23 downto 0);\n'
            "   signal FetchAddrSeq                 : unsigned(31 downto 0);\n"
            "   signal tagBranchNow                 : std_logic;\n"
            "   signal tagBranch                    : std_logic := '0';\n")

RAM_OLD = ('''   itagram : entity work.pocket_dpram_sdp
   generic map (addr_width => 8, data_width => 24)
   port map (
      clock_a => clk1x, address_a => tag_address_a,
      data_a => tag_data_a, wren_a => tag_wren_a, q_a => open,
      clock_b => not clk1x, address_b => tag_address_b,
      data_b => (others=>'0'), wren_b => '0', q_b => tag_q_b
   );
''')
RAM_BR = ('''   itagram_br : entity work.pocket_dpram_sdp
   generic map (addr_width => 8, data_width => 24)
   port map (
      clock_a => clk1x, address_a => tag_address_a,
      data_a => tag_data_a, wren_a => tag_wren_a, q_a => open,
      clock_b => not clk1x, address_b => tag_address_br,
      data_b => (others=>'0'), wren_b => '0', q_b => tag_q_br
   );
''')
SELECT_EDGE = '      if (falling_edge(clk1x)) then\n'
SELECT = ('   process (clk1x)\n'
          '   begin\n'
          + SELECT_EDGE +
          '         tagBranch <= tagBranchNow;\n'
          '      end if;\n'
          '   end process;\n')
OUTPUT = "   tag_q_b <= tag_q_br when (tagBranch = '1') else tag_q_seq;\n"
RAM_NEW = (RAM_OLD.replace('q_b => tag_q_b\n', 'q_b => tag_q_seq\n')
           + f'   -- {TAG}: the same writes; read at the branch target\n' + RAM_BR
           + f'   -- {TAG}: which of the two FetchAddr was, on the edge both RAMs captured their address\n'
           + SELECT + OUTPUT)

ADDR_OLD = '   tag_address_b <= std_logic_vector(FetchAddr(11 downto 4));\n'
ADDR_BR = '   tag_address_br <= std_logic_vector(PCbranch(11 downto 4));\n'
ADDR_NEW = ('   tag_address_b <= std_logic_vector(FetchAddrSeq(11 downto 4));\n' + ADDR_BR)

FETCH_OLD = ('''   FetchAddr       <= x"80000040" when (exceptionBreakpoint = '1') else
                      x"BF000000" when (exception > 0 and exceptionTrap = '1') else
                      x"BFC00180" when (exception > 0 and cop0_SR(22) = '1') else
                      x"80000080" when (exception > 0 and cop0_SR(22) = '0') else
                      PCbranch when branch = '1' else
                      PC;
''')
SEQ = ('''   FetchAddrSeq    <= x"80000040" when (exceptionBreakpoint = '1') else
                      x"BF000000" when (exception > 0 and exceptionTrap = '1') else
                      x"BFC00180" when (exception > 0 and cop0_SR(22) = '1') else
                      x"80000080" when (exception > 0 and cop0_SR(22) = '0') else
                      PC;
''')
NOW = ('''   tagBranchNow    <= '0' when (exceptionBreakpoint = '1') else
                      '0' when (exception > 0 and exceptionTrap = '1') else
                      '0' when (exception > 0 and cop0_SR(22) = '1') else
                      '0' when (exception > 0 and cop0_SR(22) = '0') else
                      '1' when branch = '1' else
                      '0';
''')
FETCH_NEW = (FETCH_OLD + f'   -- {TAG}: the same chain without the branch arm, and whether the branch arm is the one taken\n' + SEQ + NOW)

# Planted faults for the lockstep bench: (old, new) on the transformed text.
FAULTS = {
    # the branch-target word is never chosen
    'select_stuck': [(OUTPUT, '   tag_q_b <= tag_q_seq;\n')],
    # the choice is captured half a cycle late
    'select_rising': [(SELECT_EDGE, '      if (rising_edge(clk1x)) then\n')],
    # the second RAM reads at the sequential address
    'branch_address': [(ADDR_BR, '   tag_address_br <= std_logic_vector(PC(11 downto 4));\n')],
    # the second RAM misses the writes
    'branch_ram_not_written': [(RAM_BR, RAM_BR.replace('wren_a => tag_wren_a', "wren_a => '0'"))],
    # an exception vector does not override a taken branch in the choice
    'exception_ignored': [(NOW, "   tagBranchNow    <= '1' when branch = '1' else '0';\n")],
}
# Behaviourally equivalent in this CPU, so the bench must PASS with it: branch is '0' whenever an
# exception arm is taken. exceptionNew1 is never set, so exception > 0 means exception(4 downto 2) /= 0,
# which gates the whole execute case; exceptionBreakpoint is EXEBreakpoint one accepted edge later, and
# EXEBreakpoint sets exceptionNew3 (exception(2)) at that same edge. The chain is kept anyway: the step
# then does not depend on that property.
EQUIVALENT = {'exception_ignored'}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected exactly one match of: {old.strip()[:70]}')
    return text.replace(old, new)


def transform(text):
    """The reviewed cpu.vhd with the split tag read. Refuses any other input."""
    if TAG in text:
        raise ValueError(f'{TAG}: already applied')
    if sha(text) != SOURCE_SHA:
        raise ValueError(f'{TAG}: cpu.vhd is not the reviewed input (sha256 {sha(text)[:16]}, want {SOURCE_SHA[:16]})')
    # The inventory the argument rests on: one tag RAM, read only through tag_q_b,
    # addressed only by FetchAddr; every clocked process of the file is rising clk1x.
    if (len(re.findall(r'\btag_address_b\b', text)) != 3 or len(re.findall(r'\bitagram\b', text)) != 1
            or len(re.findall(r'falling_edge', text)) != 0 or text.count('not clk1x') != 1):
        raise ValueError(f'{TAG}: tag RAM use changed')
    text = _once(text, DECL_OLD, DECL_NEW)
    text = _once(text, RAM_OLD, RAM_NEW)
    text = _once(text, ADDR_OLD, ADDR_NEW)
    text = _once(text, FETCH_OLD, FETCH_NEW)
    for name, count in (('tag_q_seq', 3), ('tag_q_br', 3), ('tagBranch', 3), ('tagBranchNow', 3),
                        ('FetchAddrSeq', 3), ('tag_address_br', 3), ('tag_wren_a', 6)):
        if len(re.findall(rf'\b{name}\b', text)) != count:
            raise ValueError(f'{TAG}: unexpected use count of {name}: {len(re.findall(rf"{chr(92)}b{name}{chr(92)}b", text))}')
    return text


def fault_text(text, fault):
    for old, new in FAULTS[fault]:
        text = _once(text, old, new)
    return text


def prepare(source, output, seed=None, baseline=False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    manifest = load_source(source, source / 'fit-inputs.json')
    rows = [r for r in manifest['quartus_sources'] if Path(r['path']).name == 'cpu.vhd']
    if len(rows) != 1 or rows[0]['sha256'] != SOURCE_SHA:
        raise SystemExit(f'{TAG}: {source} does not compile the reviewed cpu.vhd')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    cpu = next(Path(r['path']) for r in before if Path(r['path']).name == 'cpu.vhd')
    if baseline:
        if sha(cpu.read_text()) != SOURCE_SHA:
            raise SystemExit(f'{TAG}: baseline snapshot differs from the reviewed input')
        return cpu, seed
    new = transform(cpu.read_text())
    cpu.write_text(new)
    after = inputs(output)
    a, b = ({r['path']: r for r in rs} for rs in (after, before))
    if a.keys() != b.keys() or {p for p in b if a[p] != b[p]} != {str(cpu)}:
        raise SystemExit(f'{TAG}: expected only cpu.vhd to change')
    hold_guard(output, after)
    manifest['quartus_sources'] = after
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    if digest(source / rows[0]['path']) != SOURCE_SHA:
        raise SystemExit(f'{TAG}: the source project changed')
    return cpu, seed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--baseline', action='store_true', help='snapshot only, cpu.vhd unchanged')
    parser.add_argument('--text-only', type=Path, metavar='OUT', help='SOURCE is one cpu.vhd; write the transformed text to OUT')
    args = parser.parse_args()
    try:
        if args.text_only:
            if args.text_only.resolve() == args.source.resolve():
                raise SystemExit(f'{TAG}: refusing to overwrite the input')
            args.text_only.write_text(transform(args.source.read_text()))
            print(f'{TAG}: wrote {args.text_only} (sha256 {sha(args.text_only.read_text())})')
        else:
            if not args.output:
                parser.error('--output required')
            cpu, seed = prepare(args.source, args.output, args.seed, args.baseline)
            print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
            print(f'{TAG}: {"baseline, unchanged" if args.baseline else "patched"} {cpu} (sha256 {sha(cpu.read_text())})')
            if not args.baseline:
                print(f'{TAG}: +1 M10K (itagram_br, 256 x 24), 1 register (tagBranch, falling clk1x), 24-bit 2:1 multiplexer')
    except ValueError as error:
        raise SystemExit(str(error))
