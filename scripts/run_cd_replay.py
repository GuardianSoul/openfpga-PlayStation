#!/usr/bin/env python3
"""Replay a recorded FFVII sector-request list through the Pocket CD loader.

The requests (disc-requests.csv of a connected run) go through
psx_cd_stream behind the pinned APF handler; every delivered halfword is
compared with the local disc image. Private media stay in ignored build/.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

from prepare_resource_targets import digest
from psx_disc_host import DiscHost
from tool_paths import ROOT, find_tool

SOURCES = ['sim/pocket/tb_cd_replay.sv', 'sim/pocket/psx_disc_host_apf.sv', 'rtl/pocket/psx_cd_stream.sv',
           'references/MIRLO64/target/pocket/core_bridge_cmd.v', 'references/MIRLO64/platform/pocket/common.v',
           'sim/pocket/mf_datatable_model.sv']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cue', type=Path, required=True)
    parser.add_argument('--requests', type=Path, required=True)
    parser.add_argument('--gap', type=int, default=2000, help='CD clocks between requests')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/sim/cd-replay')
    args = parser.parse_args()
    directory = args.output.resolve(); directory.mkdir(parents=True, exist_ok=True)
    with DiscHost(args.cue) as host:
        disc = host.prepare(directory)
    if disc['index_sector'] != 0:
        parser.error('The loader replay covers INDEX 01 at file sector 0 only')
    with (directory / 'build.log').open('w') as log:
        subprocess.run([find_tool('verilator'), '--binary', '--timing', '-O3', '-j', '4', '-Wno-fatal',
                        '--top-module', 'tb_cd_replay', '--Mdir', str(directory / 'obj'),
                        *[str(ROOT / s) for s in SOURCES]], cwd=directory, stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=600)
    result = subprocess.run([str(directory / 'obj/Vtb_cd_replay'), f'+disc_file={disc["image"]}',
                             f'+requests={args.requests.resolve()}', f'+disc_metadata={disc["metadata"]}',
                             f'+disc_sectors={disc["sectors"]}', f'+gap={args.gap}'],
                            cwd=directory, capture_output=True, text=True, timeout=7200)
    output = result.stdout + result.stderr
    (directory / 'run.log').write_text(output)
    match = re.search(r'PASS CD REPLAY requests=(\d+) halfwords=(\d+) max_wait_clocks=(\d+) mean_wait_clocks=(\d+)', output)
    report = dict(passed=result.returncode == 0 and bool(match), requests_file=str(args.requests.resolve()),
                  requests_sha256=digest(args.requests.resolve()), disc_sectors=disc['sectors'], gap_clocks=args.gap,
                  requests=int(match[1]) if match else None, halfwords=int(match[2]) if match else None,
                  max_wait_clocks=int(match[3]) if match else None, mean_wait_clocks=int(match[4]) if match else None,
                  source_sha256={s: digest(ROOT / s) for s in SOURCES}, sd_firmware_modelled=False,
                  hardware_verified=False)
    (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(match[0] if match else output[-2000:])
    if not report['passed']:
        raise SystemExit('CD replay failed')


if __name__ == '__main__':
    main()
