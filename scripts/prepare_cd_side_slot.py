#!/usr/bin/env python3
"""Specialize the CD side-file selector when only a track table is enabled.

The table/SBI selector is observed only during a side-file command. With
TABLE_OFFSET!=0 and LIBCRYPT_SLOT==0 every such command is a track-table
read; its selector need not be stored or multiplexed. Both-enabled profiles
retain the original selector. No file, buffer, handshake or cycle is removed.
Idle command payloads may differ, and are not valid APF requests.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest
from tool_paths import ROOT

SOURCE_SHA = '0933aff18d77b0e05fbfb7b3899d162759c09b091ea667a5b704283dc5912cd3'


def side_slot_text(text):
    if hashlib.sha256(text.encode()).hexdigest() != SOURCE_SHA:
        raise ValueError('Unreviewed CD side-slot source')
    anchor = '    wire [15:0] side_slot=side_tb ? cur_slot+TABLE_OFFSET : LIBCRYPT_SLOT;'
    if text.count(anchor) != 1:
        raise ValueError('CD side-slot selector changed')
    # State updates stay untouched. Only consumers of the side-file type
    # use the constant in the one-file/no-SBI specialization.
    for old in ('lc_cmd && side_tb', 'side_tb ?', 'if(side_tb)'):
        text = text.replace(old, old.replace('side_tb', 'side_table'))
    anchor = anchor.replace('side_tb', 'side_table')
    return text.replace(anchor,
        '    wire side_table=(TABLE_OFFSET!=0 && LIBCRYPT_SLOT==0) || side_tb;\n'+anchor, 1)


def prepare_project(directory):
    directory = Path(directory).resolve()
    if (not directory.is_relative_to(ROOT/'build') or subprocess.run(
            ['git', 'check-ignore', '--quiet', str(directory/'cd-side-slot/manifest.json')],
            cwd=ROOT).returncode):
        raise ValueError('Fresh ignored build project required')
    if any((directory/n).exists() for n in ('db', 'incremental_db', 'output_files', 'cd-side-slot')):
        raise ValueError('CD side-slot must be prepared before map/fit')
    qsf = directory/'apf_top.qsf'; q = qsf.read_text()
    found = [p for k,p in assignments(directory,q)
             if k=='SYSTEMVERILOG_FILE' and p.name=='psx_cd_stream.sv']
    if len(found)!=1 or q.count('"'+str(found[0])+'"')!=1:
        raise ValueError('Expected one quoted CD source assignment')
    source=found[0]; original_sha=digest(source); text=side_slot_text(source.read_text())
    work=directory/'cd-side-slot'; output=work/source.name
    if digest(source)!=original_sha or qsf.read_text()!=q:
        raise ValueError('CD project changed during preparation')
    work.mkdir(); output.write_text(text)
    qsf.write_text(q.replace('"'+str(source)+'"','"'+str(output)+'"',1))
    report=dict(original=dict(path=str(source),sha256=original_sha),
                candidate=dict(path=str(output),sha256=digest(output)),
                preparer_sha256=digest(Path(__file__)),
                features_clocks_and_constraints_unchanged=True,
                idle_command_payloads_may_differ=True,
                additional_guest_cycles=0, fit_verified=False,
                timing_verified=False,hardware_verified=False,production_selected=False)
    (work/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project',type=Path)
    print(json.dumps(prepare_project(parser.parse_args().project),indent=2))
