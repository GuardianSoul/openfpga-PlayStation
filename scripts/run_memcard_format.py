#!/usr/bin/env python3
"""Check pocket_memcard_format (prepare_pocket_early.py --memcard-format).

A card whose first word lacks the "MC" magic (no file, zeroed, erased
0xFF as APF creates it, or a 0.5.0 card with junk) gets a factory-formatted
header area (frames 0-63, compared with run_memcard_blank.reference(), the
independent psx-spx model) and keeps its data blocks; a card with the
header is never changed. The PSRAM model
starts nonzero, as after power-on, and APF's save readback must return the
same words. The unchanged pocket_sotn original is also run on the no-file
case, which must leave the card unformatted (the 0.5.0 behaviour).
"""
import subprocess
from pathlib import Path

from tool_paths import ROOT, find_tool
from run_memcard_blank import reference

WORDS = 32768


def run(ghdl, directory, kind, original=False):
    dump = directory / f'card{kind}{"-orig" if original else ""}.txt'
    unit = 'tb_memcard_format_orig' if original else 'tb_memcard_format'
    subprocess.run([ghdl, '-r', '--std=08', '--workdir=' + str(directory), unit, f'-gLOAD_KIND={kind}',
                    f'-gDUMP={dump}', '--ieee-asserts=disable'], cwd=directory, check=True, capture_output=True)
    words = [int(l, 16) for l in dump.read_text().split()]
    read = [int(l, 16) for l in Path(str(dump) + '.read').read_text().split()]
    assert len(words) == WORDS, len(words)
    return words, read


def main():
    ghdl = find_tool('ghdl')
    if not ghdl: raise SystemExit('ghdl not found')
    directory = ROOT / 'build/sim/memcard-format'; directory.mkdir(parents=True, exist_ok=True)
    work = ['--std=08', '--workdir=' + str(directory)]
    subprocess.run([ghdl, '-a', *work, '--work=altera_mf', str(ROOT / 'sim/pocket/altera_mf_dcfifo_model.vhd')], check=True)
    orig = (ROOT / 'references/pocket_sotn/pocket/src/fpga/core/pocket_memcard.vhd').read_text()
    tb = (ROOT / 'sim/pocket/tb_memcard_format.vhd').read_text()
    # The original has no load_done port: same bench, port removed.
    (directory / 'tb_orig.vhd').write_text(tb.replace('entity tb_memcard_format is', 'entity tb_memcard_format_orig is')
        .replace('architecture sim of tb_memcard_format is', 'architecture sim of tb_memcard_format_orig is')
        .replace('entity work.pocket_memcard_format', 'entity work.pocket_memcard')
        .replace(", load_done => load_done);", ');')
        .replace('assert aux_we = \'0\' or busy = \'1\'', 'assert true or busy = \'1\'')
        .replace('assert busy = \'1\' report "busy low before load_done"', 'assert true report "busy low before load_done"'))
    for f in (ROOT / 'references/pocket_sotn/pocket/src/fpga/core/pocket_memcard.vhd', ROOT / 'rtl/pocket/pocket_memcard_format.vhd',
              ROOT / 'sim/pocket/tb_memcard_format.vhd', directory / 'tb_orig.vhd'):
        subprocess.run([ghdl, '-a', *work, '-P' + str(directory), str(f)], check=True)
    for unit in ('tb_memcard_format', 'tb_memcard_format_orig'):
        subprocess.run([ghdl, '-e', *work, '-P' + str(directory), unit], cwd=directory, check=True)

    ref = reference()
    blank = [int.from_bytes(ref[4 * i:4 * i + 4], 'little') for i in range(2048)]
    initial = [0xDEAD0000 | i for i in range(WORDS)]
    loads = {1: [0] * WORDS, 2: [0x434D] + [0x5A000000 | i for i in range(1, WORDS)], 3: [0xFFFFFFFF] * WORDS,
             4: [0x275A0C80 if 2016 <= i < 2049 else 0xFFFFFFFF for i in range(WORDS)], 5: [0x434D] + [0] * (WORDS - 1)}

    for kind in (0, 1, 2, 3, 4, 5):
        words, read = run(ghdl, directory, kind)
        before = initial if kind == 0 else loads[kind]
        expect = blank + before[2048:] if kind in (0, 1, 3, 4) else before
        bad = [i for i in range(WORDS) if words[i] != expect[i]]
        if bad: raise SystemExit(f'case {kind}: card words differ at {bad[:6]}: got {words[bad[0]]:08x}, want {expect[bad[0]]:08x}')
        if read != expect[:65]:
            i = next(i for i in range(65) if read[i] != expect[i])
            raise SystemExit(f'case {kind}: APF readback word {i} {read[i]:08x}, card holds {expect[i]:08x}')
    words, _ = run(ghdl, directory, 0, original=True)
    if words[:2048] != initial[:2048]:
        raise SystemExit('control: the unmodified pocket_memcard changed a card nobody loaded')
    print('PASS memory card format: cards without "MC" (none, zero, erased, 0.5.0 junk) are formatted before the CPU '
          'starts with data blocks kept; formatted cards and the readback are unchanged; the original leaves a new card unformatted')


if __name__ == '__main__': main()
