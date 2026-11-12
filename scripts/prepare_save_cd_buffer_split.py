#!/usr/bin/env python3
"""Apply the existing four-sector RAM split to the frozen full-audio graph."""

import argparse
import json
from pathlib import Path

from measure_pocket_seeds import inputs
from prepare_cd_drive_tables import CD_SHA, SPU_SHA
from prepare_cd_split_buffers import split_text
from run_frozen_pocket_fit import hold_guard, load_source, snapshot

STREAM_SHA = 'fba50c640276dd73a7d096a28fecdae3b6ec6200d530ae493f1d79eb357d1a84'


def prepare(source, output):
    source, output = source.resolve(), output.resolve()
    original = load_source(source, source / 'fit-inputs.json')
    for name, sha in (('spu.vhd', SPU_SHA), ('cd_top.vhd', CD_SHA),
                      ('psx_cd_stream.sv', STREAM_SHA)):
        found = [row['sha256'] for row in original['quartus_sources']
                 if Path(row['path']).name == name]
        if found != [sha]:
            raise ValueError(f'Expected the selected full-audio input {name}')

    manifest = snapshot(source, source / 'fit-inputs.json', output, 2)
    previous = {row['path']: row for row in manifest['quartus_sources']}
    top = output / 'save-states' / 'core_top.sv'
    if str(top) not in previous or '.NBUF(4)) disc_stream' not in top.read_text():
        raise ValueError('Expected the selected four-sector stream instance')
    stream = next(Path(path) for path in previous if Path(path).name == 'psx_cd_stream.sv')
    stream.write_text(split_text(stream.read_text()))
    current = inputs(output)
    updated = {row['path']: row for row in current}
    if previous.keys() != updated.keys() or {
        path for path in previous if previous[path] != updated[path]
    } != {str(stream)}:
        raise ValueError('Expected only the selected CD stream source to change')
    hold_guard(output, current)
    manifest['quartus_sources'] = current
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    return stream


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.source, args.output))
