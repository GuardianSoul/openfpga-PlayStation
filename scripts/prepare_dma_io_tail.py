#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""DMA start after a CPU I/O read (dma-io-tail).

RobertPeip's DMACPUStall test (real PAL PS1) starts a 1-word MDECin DMA and
then reads an I/O register. The core is 3 cycles short for a timer read and
7 for an SPU read; RAM reads, writes and plain NOPs already match. A trace
(f4 build, round 5) shows the DMA request rising while the probe read is on
the bus: MiSTer starts the DMA one cycle after the read ends, and a PS1 fits
both rows if it starts GATE (7) cycles later. The CPU keeps running in that
gap, and its next read of the DMA's CHCR then waits on the DMA.

The patch:
- memorymux gets an input dmaRequest (default '0') and holds isIdle low
  (canDMA in psx_top) for GATE cycles after an internal or external bus read
  during which dmaRequest rose. One request arms the gate at most once, and
  reads of the DMA controller's own registers (CHCR/DICR polls) never arm
  it: with them, DMAPARALLEL, DMATimingToDevice and chopping drift off the
  PS1 (sim round 5).
- psx_top connects its dmaRequest to that input.
RAM/BIOS accesses, writes and requests that rise while memorymux is idle are
unchanged. GATE is fitted to the two DMACPUStall rows only; the DMA test
suites check the rest. The references are never edited; patched copies go to
a build directory.
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
GATE = 7
MARK = '-- dma-io-tail'
IO_READ = '(state = BUSREADREQUEST or state = BUSREAD or state = BUSREADEXTERNAL)'
# A read of the DMA controller's own registers (0x1F801080-0x1F8010FF, e.g. a CHCR poll) does not delay the DMA:
# PS1 DMAPARALLEL (1 NOP), DMATimingToDevice SPU16BLOCK16 and dma/chopping 1-word windows.
NOT_DMA_REG = 'addressData_buf(12 downto 7) /= "100001"'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_pin():
    actual = subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != PIN:
        raise ValueError(f'PSX_MiSTer is at {actual}, expected pin {PIN}')
    if subprocess.run(['git', '-C', str(UPSTREAM), 'diff', '--quiet', 'HEAD', '--', 'rtl/memorymux.vhd',
                       'rtl/psx_top.vhd']).returncode:
        raise ValueError('PSX_MiSTer memorymux/psx_top differ from the pinned commit')


def _replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Pinned source changed near: ' + old.strip().splitlines()[0])
    return text.replace(old, new)


def memorymux_text(text, gate=GATE):
    """Return memorymux.vhd text with the DMA start gate after I/O reads."""
    if MARK in text:
        raise ValueError('memorymux already carries dma-io-tail')
    if not 1 <= gate <= 15:
        raise ValueError('gate must be 1..15 cycles')
    text = _replace(text, '      pauseNext            : in  std_logic;\n',
                    '      pauseNext            : in  std_logic;\n'
                    f"      dmaRequest           : in  std_logic := '0'; {MARK}\n")
    text = _replace(text, '   signal state                  : tState := IDLE;\n',
                    '   signal state                  : tState := IDLE;\n'
                    f'   {MARK}: a DMA requested during a CPU I/O read starts {gate} cycles after it\n'
                    "   signal dmaio_req_1            : std_logic := '0';\n"
                    "   signal dmaio_seen             : std_logic := '0';\n"
                    '   signal dmaio_gate             : integer range 0 to 15 := 0;\n')
    text = _replace(text, "   isIdle <= '1' when (state = IDLE and readram = '0' and writeram = '0' and "
                          "writeFifo_busy = '0' and mem_save_request = '0') else '0';\n",
                    "   isIdle <= '1' when (state = IDLE and readram = '0' and writeram = '0' and "
                    "writeFifo_busy = '0' and mem_save_request = '0'\n"
                    f"                       and dmaio_seen = '0' and dmaio_gate = 0) else '0'; {MARK}\n")
    # Reset branch; anchored on its last pinned line only (prepare_fast_bios adds one after it).
    text = _replace(text, "            ext_lastactive   <= '0';\n",
                    "            ext_lastactive   <= '0';\n"
                    f"            dmaio_seen       <= '0'; {MARK}\n"
                    '            dmaio_gate       <= 0;\n')
    text = _replace(text, '            writeFifo_Wr_1 <= writeFifo_Wr;\n',
                    f'            {MARK}\n'
                    '            dmaio_req_1 <= dmaRequest;\n'
                    '            if (dmaio_gate > 0) then\n'
                    '               dmaio_gate <= dmaio_gate - 1;\n'
                    '            end if;\n'
                    f"            if (dmaRequest = '1' and dmaio_req_1 = '0' and {IO_READ} and {NOT_DMA_REG}) then\n"
                    "               dmaio_seen <= '1';\n"
                    "            elsif (dmaio_seen = '1' and state = IDLE) then\n"
                    "               dmaio_seen <= '0';\n"
                    f'               dmaio_gate <= {gate - 1};\n'
                    '            end if;\n\n'
                    '            writeFifo_Wr_1 <= writeFifo_Wr;\n')
    return text


def psx_top_text(text):
    """Return psx_top.vhd text with dmaRequest connected to memorymux."""
    if MARK in text:
        raise ValueError('psx_top already carries dma-io-tail')
    return _replace(text, '      pauseNext            => cpuPaused or (dmaRequest and canDMA),\n',
                    '      pauseNext            => cpuPaused or (dmaRequest and canDMA),\n'
                    f'      dmaRequest           => dmaRequest, {MARK}\n')


def io_tail_source(directory, gate=GATE, memorymux=None, psx_top=None):
    """Write patched memorymux/psx_top copies, the license and a manifest."""
    if memorymux is None or psx_top is None:
        check_pin()
    memorymux = Path(memorymux or UPSTREAM / 'rtl/memorymux.vhd')
    psx_top = Path(psx_top or UPSTREAM / 'rtl/psx_top.vhd')
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    head = f'-- GPL-2.0 MiSTer {{}}; dma-io-tail: a DMA requested during a CPU I/O read starts {gate} cycles after it.\n'
    sources = []
    for name, original, patch in (('memorymux.vhd', memorymux, lambda t: memorymux_text(t, gate)),
                                  ('psx_top.vhd', psx_top, psx_top_text)):
        path = directory / name
        path.write_text(head.format(name) + patch(original.read_text()))
        sources.append({'path': str(path), 'library': 'work', 'sha256': digest(path),
                        'original': str(original), 'original_sha256': digest(original)})
    shutil.copy2(UPSTREAM / 'LICENSE', directory / 'COPYING.GPL2')
    result = {'sources': sources, 'gate': gate,
              'transform': 'canDMA held low for gate cycles after a CPU I/O read during which dmaRequest rose',
              'evidence': 'RobertPeip DMACPUStall DMA READ TIMER (-3) / DMA READ SPU (-7), trace round 5',
              'sim_verified': False, 'fit_verified': False, 'hardware_verified': False}
    (directory / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def _assignment(lines, name):
    found = [i for i, l in enumerate(lines) if 'VHDL_FILE' in l and re.search(rf'[/"]{name}"', l)]
    if len(found) != 1:
        raise ValueError(f'Expected one {name} assignment')
    return found[0], re.search(rf'"([^"]*{name})"', lines[found[0]])[1]


def prepare_project(qsf, directory, gate=GATE):
    """Point an existing Quartus project at patched copies of its memorymux and psx_top."""
    qsf = Path(qsf)
    lines = qsf.read_text().splitlines(True)
    picks = {}
    for name in ('memorymux.vhd', 'psx_top.vhd'):
        index, old = _assignment(lines, re.escape(name))
        selected = Path(old) if Path(old).is_absolute() else qsf.parent / old
        picks[name] = (index, old, selected)
    directory = Path(directory).resolve()
    if any(p[2].resolve().parent == directory for p in picks.values()):
        raise ValueError('Output directory holds a selected source; use a new directory')
    # Patch the project's own copies (they may carry earlier build patches).
    result = io_tail_source(directory, gate, picks['memorymux.vhd'][2], picks['psx_top.vhd'][2])
    for (index, old, _), source in zip(picks.values(), result['sources']):
        lines[index] = lines[index].replace(old, source['path'])
    qsf.write_text(''.join(lines))
    result['qsf'] = str(qsf)
    (directory / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('directory', type=Path, help='output directory for the patched copies')
    parser.add_argument('--gate', type=int, default=GATE, help='cycles the DMA start waits after the I/O read')
    parser.add_argument('--memorymux', type=Path, help='memorymux.vhd to patch (default: pinned reference)')
    parser.add_argument('--psx-top', type=Path, help='psx_top.vhd to patch (default: pinned reference)')
    parser.add_argument('--qsf', type=Path, help='optionally retarget this Quartus project')
    args = parser.parse_args()
    if args.qsf:
        result = prepare_project(args.qsf, args.directory, args.gate)
    else:
        result = io_tail_source(args.directory, args.gate, args.memorymux, args.psx_top)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
