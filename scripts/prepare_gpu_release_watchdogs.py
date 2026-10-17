#!/usr/bin/env python3
"""Prepare the exact GPU watchdog cuts used in the SPU-cache capacity trial.

Remove MiSTer's line abort and global diagnostic timer. Keep FIFO errors,
pixel64timeout, rendering arithmetic and all pipeline edges. Normal rendering
is cycle-identical in the bounded oracle; behavior after the removed timeout
bounds deliberately differs. No release default is changed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

from measure_pocket_seeds import inputs
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import UPSTREAM, digest

GUARDS = {
    'gpu_line.vhd': 'edd9565dde067f1157d363426db67bdaba8eab5a73e0bfa469f48465eae53235',
    'gpu.vhd': 'f455d28df9a3abaa99545d8c4d1b30cf570772684c05ce3516ae3e151db98f69',
}
BLOCKS = {
    'gpu_line.vhd': """            if (recstate = REQUESTIDLE) then
               timeout <= 0;
            elsif (timeout < 1048575) then
               timeout  <= timeout + 1;
            else
               error    <= '1';
               recstate <= REQUESTIDLE;
               done     <= '1';
            end if;
""",
    'gpu.vhd': """         if (proc_idle = '1') then
            timeout <= 0;
         elsif (timeout < 67108863) then
            timeout  <= timeout + 1;
         else
            errorGPU <= '1';
         end if;
""",
}


def watchdog_text(name, text):
    if name not in GUARDS or hashlib.sha256(text.encode()).hexdigest() != GUARDS[name]:
        raise ValueError('Unreviewed GPU watchdog source: ' + name)
    if text.count(BLOCKS[name]) != 1:
        raise ValueError('GPU watchdog branch changed: ' + name)
    text = text.replace(BLOCKS[name], '')
    text, count = re.subn(r'(?m)^   signal timeout\s*: integer range 0 to \d+ := 0;\n', '', text)
    if count != 1 or re.search(r'\btimeout\b', text):
        raise ValueError('GPU watchdog declaration/consumers changed: ' + name)
    return text


def prepare_project(directory):
    directory = Path(directory).resolve()
    if any((directory / name).exists() for name in
           ('db', 'incremental_db', 'output_files', 'gpu-release-watchdogs')):
        raise ValueError('GPU watchdog preparation must precede map/fit')
    if not json.loads((directory / 'manifest.json').read_text()).get('reset_only'):
        raise ValueError('GPU watchdog trial requires reset-only build')
    qsf = directory / 'apf_top.qsf'
    qtext = qsf.read_text()
    selected = assignments(directory, qtext)
    sources = {}
    for name in GUARDS:
        paths = {path for _, path in selected if path.name == name}
        if len(paths) != 1:
            raise ValueError('Expected one selected GPU source: ' + name)
        path = paths.pop()
        if qtext.count('"' + str(path) + '"') != 1:
            raise ValueError('Expected one quoted GPU assignment: ' + name)
        sources[name] = (path, watchdog_text(name, path.read_text()))
    before = inputs(directory)
    work = directory / 'gpu-release-watchdogs'
    work.mkdir()
    shutil.copyfile(UPSTREAM / 'LICENSE', work / 'COPYING.GPL2')
    rows = []
    for name, (source, text) in sources.items():
        output = work / name
        output.write_text(text)
        qtext = qtext.replace('"' + str(source) + '"', '"' + str(output) + '"')
        rows.append(dict(source=str(source), source_sha256=GUARDS[name],
                         path=str(output), sha256=digest(output)))
    if any(digest(Path(row['path'])) != row['sha256'] for row in before):
        raise ValueError('GPU watchdog inputs changed during preparation')
    qsf.write_text(qtext)
    report = dict(sources=rows, original_inputs=before,
                  preparer_sha256=digest(Path(__file__)),
                  scope='Line 2^20 abort/global 2^26 diagnostic timers only; pixel64timeout retained',
                  timeout_behavior_changed=True, clocks_or_constraints_changed=False,
                  production_selected=False, hardware_verified=False)
    (work / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    print(json.dumps(prepare_project(parser.parse_args().project), indent=2))
