#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""memorymux CPU write FIFO in M10K block RAM (memorymux-fifo-ram).

MiSTer's memorymux buffers CPU stores in an 8 x 70-bit fall-through FIFO
(SyncFifoFallThroughMLAB, asynchronous-read MLAB). This patch copies the
pinned memorymux.vhd and swaps that one instance for our
rtl/memory/psx_wfifo_m10k.vhd (block RAM storage through
rtl/memory/psx_m10k_sdp.vhd). Nothing else in memorymux and nothing in the
CPU changes; the FIFO keeps the MLAB FIFO's pointer, flag and Dout timing,
so every memorymux output is cycle-identical (scripts/run_memorymux_fifo_ram.py
checks this in lockstep against the unmodified sources).

READ_MODE "BYPASS" (default) keeps full-cycle clk1x paths with a 70-bit
bypass register for the same-edge write/read case; "CLK2X" reads the block
on clk2x without a bypass register and gives the FIFO consumers half a
clk1x cycle. See the header of rtl/memory/psx_wfifo_m10k.vhd.

Seventy bits exceed the 40-bit maximum width of a Cyclone V M10K in simple
dual-port mode, so the storage is two M10K blocks (8 of 256 words used),
not one. The references are never edited; patched copies go to a build
directory. The FIFO and RAM files must be compiled into the same library as
the original SyncFifoFallThroughMLAB.vhd (MiSTer's `mem`).
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / 'references/PSX_MiSTer'
PIN = 'cd17b5a0ad261005c321d9d5ba41b8dbe313a7d3'
FIFO_RTL = ROOT / 'rtl/memory/psx_wfifo_m10k.vhd'
RAM_RTL = ROOT / 'rtl/memory/psx_m10k_sdp.vhd'
RAM_SIM = ROOT / 'sim/memory/psx_m10k_sdp_model.vhd'
MODES = ('BYPASS', 'CLK2X')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_pin():
    actual = subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != PIN:
        raise ValueError(f'PSX_MiSTer is at {actual}, expected pin {PIN}')
    if subprocess.run(['git', '-C', str(UPSTREAM), 'diff', '--quiet', 'HEAD', '--', 'rtl/memorymux.vhd',
                       'rtl/SyncFifoFallThroughMLAB.vhd']).returncode:
        raise ValueError('PSX_MiSTer memorymux/FIFO sources differ from the pinned commit')


def _replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Pinned memorymux changed near: ' + old.strip().splitlines()[0])
    return text.replace(old, new)


def fifo_ram_text(text, mode='BYPASS'):
    """Return memorymux.vhd text with the write FIFO instance retargeted."""
    if mode not in MODES:
        raise ValueError(f'mode must be one of {MODES}')
    # The instance and its sizes must be exactly the pinned ones.
    block = re.search(r'   iwritefifo: entity mem\.SyncFifoFallThroughMLAB\n.*?\n   \);\n', text, re.S)
    if not block or text.count('SyncFifoFallThroughMLAB') != 1:
        raise ValueError('Pinned memorymux write FIFO instance not found')
    for line in ('SIZE              => 8,', 'DATAWIDTH         => 70,', 'NEARFULLDISTANCE  => 4,',
                 'NEAREMPTYDISTANCE => 2', 'clk         => clk1x,', 'Din         => writeFifo_Din,',
                 'Wr          => writeFifo_Wr,', 'Dout        => writeFifo_Dout,',
                 'Rd          => writeFifo_Rd,', 'Empty       => writeFifo_Empty,'):
        if line not in block[0]:
            raise ValueError('Pinned memorymux write FIFO instance changed: ' + line)
    new = block[0]
    new = _replace(new, '   iwritefifo: entity mem.SyncFifoFallThroughMLAB\n',
                   '   -- memorymux-fifo-ram: block RAM storage, MLAB FIFO timing (PSX Pocket, MIT).\n'
                   '   iwritefifo: entity mem.psx_wfifo_m10k\n')
    new = _replace(new, '      NEAREMPTYDISTANCE => 2\n',
                   f'      NEAREMPTYDISTANCE => 2,\n      READ_MODE         => "{mode}"\n')
    new = _replace(new, '      clk         => clk1x,\n',
                   '      clk         => clk1x,\n      clk2x       => clk2x,\n')
    text = text.replace(block[0], new)
    if 'SyncFifoFallThroughMLAB' in text:
        raise ValueError('Original FIFO reference remains')
    return text


def memorymux_fifo_ram_source(directory, mode='BYPASS', original=None):
    """Write the patched memorymux and copies of our FIFO/RAM RTL."""
    if original is None:
        check_pin()
        original = UPSTREAM / 'rtl/memorymux.vhd'
    original = Path(original)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'memorymux.vhd'
    path.write_text('-- GPL-2.0 MiSTer memorymux; write FIFO instance retargeted to psx_wfifo_m10k '
                    f'(READ_MODE {mode}), behavior unchanged.\n' + fifo_ram_text(original.read_text(), mode))
    shutil.copy2(UPSTREAM / 'LICENSE', directory / 'COPYING.GPL2')
    copies = []
    for rtl in (RAM_RTL, FIFO_RTL):
        copied = directory / rtl.name
        copied.write_bytes(rtl.read_bytes())
        copies.append({'path': str(copied), 'library': 'mem', 'sha256': digest(copied),
                       'original': str(rtl), 'original_sha256': digest(rtl)})
    result = {'path': str(path), 'library': 'work', 'sha256': digest(path),
              'original': str(original), 'original_sha256': digest(original),
              'fifo_sources': copies, 'simulation_ram_model': str(RAM_SIM),
              'read_mode': mode,
              'transform': 'memorymux write FIFO storage moved from MLAB to M10K; FIFO timing unchanged',
              'm10k_blocks': 2, 'cycle_equivalence': 'scripts/run_memorymux_fifo_ram.py',
              'fit_verified': False, 'game_verified': False, 'hardware_verified': False}
    (directory / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def prepare_project(qsf, directory, mode='BYPASS'):
    """Point an existing Quartus project at the patched memorymux."""
    qsf = Path(qsf)
    lines = qsf.read_text().splitlines(True)
    mux = [i for i, l in enumerate(lines) if 'VHDL_FILE' in l and re.search(r'[/"]memorymux\.vhd"', l)]
    fifo = [l for l in lines if 'VHDL_FILE' in l and 'SyncFifoFallThroughMLAB.vhd' in l]
    if len(mux) != 1 or len(fifo) != 1:
        raise ValueError('Expected one memorymux.vhd and one SyncFifoFallThroughMLAB.vhd assignment')
    # Patch the project's own memorymux (it may carry earlier build patches);
    # fifo_ram_text still requires the pinned write FIFO instance.
    selected = Path(re.search(r'"([^"]*memorymux\.vhd)"', lines[mux[0]])[1])
    if not selected.is_absolute():
        selected = qsf.parent / selected
    result = memorymux_fifo_ram_source(directory, mode, original=selected)
    library = re.search(r'-library\s+(\S+)', fifo[0])
    library = library[1] if library else 'mem'
    old = re.search(r'"([^"]*memorymux\.vhd)"', lines[mux[0]])[1]
    added = ''.join(f'set_global_assignment -name VHDL_FILE "{source["path"]}" -library {library}\n'
                    for source in result['fifo_sources'])
    lines[mux[0]] = added + lines[mux[0]].replace(old, result['path'])
    qsf.write_text(''.join(lines))
    result['qsf'] = str(qsf)
    result['fifo_library'] = library
    (Path(directory) / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('directory', type=Path, help='output directory for the patched copies')
    parser.add_argument('--mode', choices=MODES, default='BYPASS')
    parser.add_argument('--qsf', type=Path, help='optionally retarget this Quartus project')
    args = parser.parse_args()
    if args.qsf:
        result = prepare_project(args.qsf, args.directory, args.mode)
    else:
        result = memorymux_fifo_ram_source(args.directory, args.mode)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
