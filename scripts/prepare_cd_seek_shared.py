#!/usr/bin/env python3
"""Share the selected CD seek delay multiplier without changing event edges."""
import argparse
import hashlib
import json
from pathlib import Path

from measure_pocket_seeds import inputs
from prepare_spu_ring_paired import once
from run_frozen_pocket_fit import hold_guard, load_source, snapshot
from prepare_cd_drive_tables import CD_SHA

NEW = '''               if diffLBA >= 250000 and currentLBA = 0 then
                  seekCycles <= 137 * 451584;
               else
                  if diffLBA < 4500 then
                     seek_units := diffLBA / 256;
                     if modeReg(7) /= '0' then seek_units := seek_units + 12; end if;
                  else
                     seek_units := 29 + diffLBA / 1024;
                  end if;
                  seekCycles <= seek_units * 28224;
               end if;'''


def seek_block(text):
    start = text.index("               if (diffLBA < 4500 and modeReg(7) = '0') then")
    end = text.index("\n\n            end if;\n\n            if (addSeekTime", start)
    return text[start:end]


def transform(text):
    if hashlib.sha256(text.encode()).hexdigest() != CD_SHA:
        raise ValueError('Exact selected CD source required')
    text = once(text, seek_block(text), NEW)
    anchor = '      variable physicalLBANew  : integer range 0 to 524287;'
    return once(text, anchor, anchor + '\n      variable seek_units : natural range 0 to 540;')


def prepare(source, output):
    source, output = source.resolve(), output.resolve()
    original = load_source(source, source / 'fit-inputs.json')
    rows = [r for r in original['quartus_sources'] if Path(r['path']).name == 'cd_top.vhd']
    if len(rows) != 1 or rows[0]['sha256'] != CD_SHA:
        raise ValueError('Selected CD source changed')
    manifest = snapshot(source, source / 'fit-inputs.json', output, 2)
    before = inputs(output)
    cd = next(Path(r['path']) for r in before if Path(r['path']).name == 'cd_top.vhd')
    cd.write_text(transform(cd.read_text()))
    after = inputs(output)
    a, b = ({r['path']: r for r in rs} for rs in (after, before))
    if a.keys() != b.keys() or {p for p in b if a[p] != b[p]} != {str(cd)}:
        raise ValueError('Expected one changed CD source')
    hold_guard(output, after)
    manifest['quartus_sources'] = after
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    return cd


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(prepare(a.source, a.output))
