#!/usr/bin/env python3
"""The release MDEC chain (prepare_mdec_beetle.py, dpram-sdp included) on the MDEC bench.

Simulates a release-chain mdec.vhd with the real pocket_dpram_sdp primitives
(prepare_dpram_sdp.primitive of the pinned dpram.vhd, on altera_mf) in
sim/reference/tb_mdec_reference.vhd and counts, per seed, the output words
that differ from run_mdec_reference's "mednafen" model (= Beetle's mdec.c,
scripts/run_mdec_beetle.py). The default is the new chain; --mdec FILE runs a
given file (e.g. an old release's dpram-sdp/sources/2/mdec.vhd); --fault NAME
plants one of FAULTS in the new chain. Out files stay in the run
directory for run_mdec_beetle.py --bench.
Usage: run_mdec_release_chain.py [--mdec FILE | --fault NAME] [--name NAME]
"""
import argparse
import os
import subprocess
from pathlib import Path

import run_mdec_reference as reference
from prepare_dpram_sdp import primitive
from prepare_mdec_beetle import chain_text, UPSTREAM_MDEC
from prepare_mdec_mednafen import mednafen_text
from tool_paths import ROOT, find_tool

UPSTREAM = ROOT/'references/PSX_MiSTer'


FAULTS = {   # planted mutations of the new logic, each must be caught
    'idct-round': ('to_signed(16#4000#, 31)', 'to_signed(0, 31)'),
    'colour-carry': (" + resize(signed('0' & color_conv_R(0 downto 0)),10)", ''),
    'dequant-dec': ('(dqProduct(11 downto 0) - 1) & "1000"', 'dqProduct(11 downto 0) & "1000"'),
}


def fault_mednafen(name):
    def transform(text):
        out = mednafen_text(text); old, new = FAULTS[name]
        assert out.count(old) == 1, name
        return out.replace(old, new)
    return transform


def build(directory, mdec):
    ghdl = find_tool('ghdl'); lib = Path(find_tool('quartus_sh')).parents[1]/'eda/sim_lib'
    directory.mkdir(parents=True, exist_ok=True)
    for f in directory.glob('*.cf'): f.unlink()
    log = (directory/'build.log').open('w'); opts = ['--std=08', '-frelaxed', '-fsynopsys']
    def call(*a): subprocess.run([ghdl, *a], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
    call('-a', *opts, '--work=altera_mf', str(lib/'altera_mf_components.vhd'), str(lib/'altera_mf.vhd'))
    mem = UPSTREAM/'sim/system/src/mem'
    for library in ('mem', 'work'):
        call('-a', *opts, f'--work={library}', str(mem/'dpram.vhd'))
    for name in ('RamMLAB.vhd', 'SyncFifo.vhd', 'SyncFifoFallThrough.vhd'):
        source = mem/name
        call('-a', *opts, '--work=mem', str(source if source.exists() else UPSTREAM/'rtl'/name))
    pinned = (UPSTREAM/'rtl/dpram.vhd').read_text()
    for name, reverse in (('pocket_dpram_sdp', False), ('pocket_dpram_sdp_reverse', True)):
        path = directory/f'{name}.vhd'; path.write_text(primitive(pinned, reverse))
        call('-a', *opts, str(path))   # mdec instantiates work.pocket_dpram_sdp only
    (directory/'mdec.vhd').write_text(mdec)
    call('-a', *opts, str(directory/'mdec.vhd'))
    call('-a', *opts, str(ROOT/'sim/reference/tb_mdec_reference.vhd'))
    (directory/'mdec-reference.txt').write_text('R 00 00000000\n')
    call('-e', *opts, 'tb_mdec_reference')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mdec', type=Path); ap.add_argument('--fault', choices=sorted(FAULTS)); ap.add_argument('--name', default='new')
    args = ap.parse_args()
    upstream = (ROOT/UPSTREAM_MDEC).read_text()
    mdec = args.mdec.read_text() if args.mdec else chain_text(upstream, fault_mednafen(args.fault) if args.fault else mednafen_text)[0]
    directory = ROOT/'build/sim/mdec-release-chain'/args.name
    build(directory, mdec)
    for seed in range(int(os.environ.get('PSX_MDEC_SEEDS', '3'))):
        ok, got, text = reference.run(directory, seed)
        _, expected, labels = reference.vectors(seed, 'mednafen')
        n = sum(a != b for a, b in zip(got, expected)) + abs(len(got) - len(expected))
        state = 'bench FAIL ' + ' '.join(text.strip().splitlines()[-1:]) if not ok else ('EQUAL' if n == 0 else 'DIFF')
        print(f'{args.name} seed {seed}: {state}: {n} of {len(expected)} words differ from the mednafen model (Beetle)', flush=True)


if __name__ == '__main__':
    main()
