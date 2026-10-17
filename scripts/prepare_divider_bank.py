#!/usr/bin/env python3
"""Opt-in packed divider banks for the reviewed reset-only CPU/GPU consumers.

The signed restoring operation and completion edges are retained, but partial
quotient values while busy differ. Therefore bind every architectural consumer
to its exercised source, rather than substituting this as a generic divider.
No feature, clock, pipeline or timing-constraint change is made here.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

from measure_pocket_seeds import inputs
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest, UPSTREAM
from tool_paths import ROOT

CONSUMERS = {
    'cpu.vhd': 'e238aa73e7ae5617e5686bf636b68eb7f453336d63f844eafdde34c12d38efba',
    'gpu.vhd': 'f455d28df9a3abaa99545d8c4d1b30cf570772684c05ce3516ae3e151db98f69',
    'gpu_line.vhd': 'edd9565dde067f1157d363426db67bdaba8eab5a73e0bfa469f48465eae53235',
    'gpu_poly.vhd': 'f169f05c98a8d714c881568a44cdc0b7c2d6eb864808510f6c9c9f2af8cf5d90',
    'pGPU.vhd': '0ac89f49231daeef448d66f4458f76ad41ebf5c683a06695a4738b1b77b1f577',
}
DIVIDER_SHA = '871222d1f880e867361817af7b08866dc26ced60abf9dbd7f39b23a2b4978f42'
PACKED_SHA = '13edea47cc1d5460bf4b155e7304c0806ac096c741a5cd493ed5f285e4f173b4'
# 0.5.3 with --memorymux-fifo-ram CLK2X keeps the same CPU body and
# divider consumer. Only itagram/icache name the pinned bidirectional
# dpram instead of the strict graph's one-writer/one-reader SDP wrapper.
CPU_DPRAM_SHA = '62b767a81c52776cfafd7476c730bdcf6a7a498222d439a6b85b526f6b61e293'
DPRAM_SHA = '14d2d8f630f9903981ed2d1d235151b56c1a9477ef461b2dbd929537a22a482d'


def cpu_profile(path):
    sha = digest(path)
    if sha == CONSUMERS['cpu.vhd']:
        return 'strict-sdp-cache'
    if sha == CPU_DPRAM_SHA:
        text = Path(path).read_text()
        for instance, count in (('itagram', 1), ('icache', 2)):
            pattern = r'\b' + instance + r'\s*:\s*entity work\.dpram\b'
            if len(re.findall(pattern, text)) != count:
                raise ValueError('Release CPU cache primitive shape differs')
        if text.count('entity work.dpram') != 3:
            raise ValueError('Release CPU cache primitive census differs')
        # The third instance is the two-read-port scratchpad and stays as is.
        text = text.replace('entity work.dpram', 'entity work.pocket_dpram_sdp', 2)
        import hashlib
        if hashlib.sha256(text.encode()).hexdigest() != CONSUMERS['cpu.vhd']:
            raise ValueError('Release CPU differs beyond cache primitive names')
        return 'release-dpram-cache'
    raise ValueError('Unreviewed divider dependency: cpu.vhd')


def prepare_project(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT / 'build') or subprocess.run(
            ['git', 'check-ignore', '--quiet', str(directory / 'divider-bank/manifest.json')], cwd=ROOT).returncode:
        raise ValueError('Fresh ignored build project required')
    if any((directory / n).exists() for n in ('db', 'incremental_db', 'output_files', 'divider-bank')):
        raise ValueError('Divider bank must be prepared before map/fit')
    if not json.loads((directory / 'manifest.json').read_text()).get('reset_only'):
        raise ValueError('Reviewed divider consumers require reset-only configuration')
    qsf = directory / 'apf_top.qsf'; initial_qsf = digest(qsf)
    q = qsf.read_text(); assigned = assignments(directory, q)
    selected = {}
    for name, expected in (CONSUMERS | {'divider.vhd': DIVIDER_SHA}).items():
        found = [p for kind, p in assigned if kind == 'VHDL_FILE' and p.name == name]
        if len(found) != 1:
            raise ValueError('Unreviewed divider dependency: ' + name)
        if name == 'cpu.vhd':
            cpu_contract = cpu_profile(found[0])
        elif digest(found[0]) != expected:
            raise ValueError('Unreviewed divider dependency: ' + name)
        selected[name] = found[0]
    cache_ram = None
    if cpu_contract == 'release-dpram-cache':
        memories = {p for kind,p in assigned if kind=='VHDL_FILE' and p.name=='dpram.vhd'}
        if len(memories)!=1 or digest(next(iter(memories)))!=DPRAM_SHA:
            raise ValueError('Release CPU requires the pinned DPRAM primitive')
        cache_ram = memories.pop()
    instantiated = set()
    for kind, path in assigned:
        if kind == 'VHDL_FILE':
            text = re.sub(r'--[^\n]*', '', path.read_text())
            if re.search(r':\s*(?:entity\s+(?:work\.)?)?divider\s+(?:port|generic)\s+map', text, re.I):
                instantiated.add(path)
    if instantiated != {selected['cpu.vhd'], selected['gpu.vhd']}:
        raise ValueError('Unreviewed divider instance or consumer')
    old = selected['divider.vhd']; token = '"' + str(old) + '"'
    if q.count(token) != 1:
        raise ValueError('Expected one absolute quoted divider assignment')
    template = ROOT / 'rtl/pocket/psx_divider_bank.vhd'
    if digest(template) != PACKED_SHA:
        raise ValueError('Unreviewed packed divider template')
    before = inputs(directory)
    if digest(qsf) != initial_qsf:
        raise ValueError('Divider project changed during validation')
    work = directory / 'divider-bank'; output = work / 'divider.vhd'
    protected = {str(p): digest(p) for p in (*selected.values(), template)}
    if cache_ram:
        protected[str(cache_ram)] = digest(cache_ram)
    replacement = template.read_bytes()
    if any(digest(Path(p)) != sha for p, sha in protected.items()):
        raise ValueError('Divider sources changed during preparation')
    work.mkdir(); output.write_bytes(replacement)
    shutil.copyfile(UPSTREAM / 'LICENSE', work / 'COPYING.GPL2')
    if any(digest(Path(row['path'])) != row['sha256'] for row in before):
        raise ValueError('Divider project changed during preparation')
    qsf.write_text(q.replace(token, '"' + str(output) + '"', 1))
    report = dict(original_inputs=before, original=dict(path=str(old), sha256=digest(old)),
                  candidate=dict(path=str(output), sha256=digest(output)),
                  consumers={name: dict(path=str(selected[name]), sha256=digest(selected[name])) for name in CONSUMERS},
                  cpu_cache_profile=cpu_contract,
                  release_cache_primitive=(dict(path=str(cache_ram), sha256=digest(cache_ram)) if cache_ram else None),
                  preparer_sha256=digest(Path(__file__)), signed_restoring_algorithm_retained=True,
                  completion_cycle_unchanged=True, intermediate_busy_quotient_unchanged=False,
                  additional_guest_cycles=0, clocks_features_and_constraints_unchanged=True,
                  fit_verified=False, timing_verified=False, hardware_verified=False, production_selected=False)
    (work / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('project', type=Path)
    print(json.dumps(prepare_project(p.parse_args().project), indent=2))
