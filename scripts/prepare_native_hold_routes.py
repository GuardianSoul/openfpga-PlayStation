#!/usr/bin/env python3
"""Opt-in held-card payload model and stronger whole-DMA-bank routing.

Before map only. Card mode requires the reproducible delayed-payload proof,
reviewed APF/bridge bodies and the existing source-bound card scope. DMA mode
adds only a routing minimum, retaining ordinary timing and the150ps reserve.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from prepare_pocket_card_cdc import (ATTRIBUTES, CARD_SHA, TOP_NEW, TOP_OLD,
                                    VRAM_SHA, top_contract)
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest
from run_card_payload_timing import (SELECTED_CARD_SHA, instrument_formatter)
from tool_paths import ROOT

DMA_SHA = '8326e23112bfa837b339ff90bbaf7c6890beac0e492a094e110b7e5f16f77d40'
CARD_SCOPE_SHA = 'c88d9430d74d2b5225d5eca4efca85caf4c40e16dbe5cfab914c41bd8d6c8eb6'
BRIDGE_SHA = '35cc7d0464b2d0130955155eab442aa462c9e80c6982d101dbc3318c6aabb98d'
APF_SHAS = {
    'f3528d3dcf31e25ccd40ed1506436abb416170e70124821048982c556d9fe835',
    '2f59cbb21973b9820f9e25ff95e521fc63334b489c9ec0df2383149dc2a85842',
}
CARD_MIN = '    set_min_delay -from $from -to $to 0\n'


def held_card_scope_text(text):
    if hashlib.sha256(text.encode()).hexdigest() != CARD_SCOPE_SHA or text.count(CARD_MIN) != 1:
        raise ValueError('Unreviewed card scope/numeric bound')
    return text.replace(CARD_MIN, CARD_MIN.replace(' 0\n', ' -13\n'), 1)


def verify_payload_proof(path, selected):
    path = Path(path).resolve(); proof = json.loads(path.read_text())
    if (not proof.get('passed') or not proof.get('positive_body_reverses_exactly') or
            proof.get('runner_sha256') != digest(ROOT / 'scripts/run_card_payload_timing.py') or
            proof.get('Native_sha256') != VRAM_SHA or
            proof.get('host_read_write_interval_cycles') != 88 or
            proof.get('payload_flight_bound_ns') != 13 or
            not proof.get('actual_Native_and_formatter_connected')):
        raise ValueError('Unreviewed card payload proof')
    for name, sha in proof['protected'].items():
        if digest(Path(name)) != sha:
            raise ValueError('Card proof source changed: ' + name)
    prepared = proof['selected_formatter']
    if digest(Path(prepared['path'])) != SELECTED_CARD_SHA or prepared['sha256'] != digest(selected):
        raise ValueError('Card proof and selected formatter differ')
    if digest(selected) != SELECTED_CARD_SHA:
        raise ValueError('Selected card source differs')
    runs = {r['name']: r for r in proof['runs']}
    if set(runs) != {'positive', 'early-capture', 'late-payload-flight'}:
        raise ValueError('Incomplete card payload controls')
    positive = runs['positive']
    expected_body = instrument_formatter(selected.read_text())
    expected_sha = hashlib.sha256(expected_body.encode()).hexdigest()
    for row in runs.values():
        if digest(Path(row['log']['path'])) != row['log']['sha256']:
            raise ValueError('Card proof log changed')
        if digest(Path(row['formatter']['path'])) != row['formatter']['sha256']:
            raise ValueError('Card proof compiled source changed')
    if (positive['exit_code'] != 0 or positive['flight_ps'] != 13000 or
            positive['formatter']['sha256'] != expected_sha or
            positive['payload']['captures'] != 32768 or
            positive['payload']['min_capture_age_fs'] < 26936000 or
            positive['payload']['min_request_age_fs'] < 29525100 or
            positive['Native']['max_AUX_cycles'] > 30 or
            positive['Native']['GPU_words'] < 1000):
        raise ValueError('Card payload interval/deadline proof failed')
    readback = positive['readback']; read = Path(readback['path'])
    if digest(read) != readback['sha256']:
        raise ValueError('Card readback changed')
    expected = [0x434d] + [0x5a000000 | i for i in range(1, 32768)]
    if [int(v, 16) for v in read.read_text().split()] != expected:
        raise ValueError('Card readback values differ')
    markers = {'early-capture': 'CARD_EARLY_CAPTURE', 'late-payload-flight': 'CARD_PAYLOAD_NOT_SETTLED'}
    for name, marker in markers.items():
        row = runs[name]
        if row['exit_code'] == 0 or marker not in Path(row['log']['path']).read_text():
            raise ValueError('Card actual fault not rejected: ' + name)
    wrong = 'if (rd_ack_s(1) /= rd_ack_s(0)) then'
    right = 'if (rd_ack_s(2) /= rd_ack_s(1)) then'
    early = Path(runs['early-capture']['formatter']['path']).read_text()
    if early.count(wrong) != 1 or early.replace(wrong, right, 1) != expected_body:
        raise ValueError('Card capture fault is not the reviewed wiring mutation')
    if (runs['late-payload-flight']['formatter']['sha256'] != expected_sha or
            runs['late-payload-flight']['flight_ps'] != 41000):
        raise ValueError('Card late-flight control differs')
    return dict(path=str(path), sha256=digest(path), source_bound=True,
                ACK_capture_min_fs=positive['payload']['min_capture_age_fs'],
                request_capture_min_fs=positive['payload']['min_request_age_fs'],
                max_Native_AUX_cycles=positive['Native']['max_AUX_cycles'],
                inherited_chip_model=True, hardware_verified=False)


def prepare_project(directory, expected_top_sha256, *, card=False, dma=False, proof=None):
    directory = Path(directory).resolve()
    if not card and not dma:
        raise ValueError('Select held-card model and/or DMA bank routing')
    if (not directory.is_relative_to(ROOT / 'build') or
            subprocess.run(['git', 'check-ignore', '--quiet', str(directory / 'hold-contracts/manifest.json')],
                           cwd=ROOT).returncode):
        raise ValueError('Fresh ignored build project required')
    if any((directory / n).exists() for n in ('db', 'incremental_db', 'output_files', 'hold-contracts')):
        raise ValueError('Hold contracts must be prepared before map/fit')
    core, qsf, pocket = (directory / n for n in ('core_top.sv', 'apf_top.qsf', 'pocket.sdc'))
    q = qsf.read_text(); selected = assignments(directory, q)
    if digest(core) != expected_top_sha256 or [(k, p) for k, p in selected if p.name == core.name] != [('SYSTEMVERILOG_FILE', core)]:
        raise ValueError('Canonical hold-contract top differs')

    def one(name, kind=None, shas=None):
        found = [p for k, p in selected if p.name == name and (kind is None or k == kind)]
        if len(found) != 1 or (shas and digest(found[0]) not in shas):
            raise ValueError('Hold-contract dependency differs: ' + name)
        return found[0]

    hold = one('pocket_cpu_hold.sdc', 'SDC_FILE')
    if not hold.read_text().startswith('set pocket_cpu_hold_ns 0.15\n'):
        raise ValueError('The required150ps CPU hold reserve must remain')
    protected_paths = [core, qsf, pocket, hold]
    work = directory / 'hold-contracts'; new_q = q; new_pocket = pocket.read_text()
    outputs = {}; evidence = None
    if card:
        if proof is None:
            raise ValueError('Card mode needs its payload timing proof')
        formatter = one('pocket_memcard_format.vhd', 'VHDL_FILE', {SELECTED_CARD_SHA})
        vram = one('vram_psram.vhd', 'VHDL_FILE', {VRAM_SHA})
        bridge = one('io_bridge_peripheral.v', 'VERILOG_FILE', {BRIDGE_SHA})
        apf = one('apf_top.v', 'VERILOG_FILE', APF_SHAS)
        text = core.read_text()
        if text.count(TOP_NEW) != 1 or top_contract(text.replace(TOP_NEW, TOP_OLD, 1)) != text:
            raise ValueError('Card source-bound top contract differs')
        raw = formatter.read_text().replace('\n' + ATTRIBUTES, '', 1)
        if hashlib.sha256(raw.encode()).hexdigest() != CARD_SHA:
            raise ValueError('Card functional body differs')
        matches = re.findall(r'^source "([^"\n]*/card-cdc/card\.sdc)"$', new_pocket, re.M)
        if len(matches) != 1:
            raise ValueError('Expected one existing endpoint-scoped card constraint')
        scope = Path(matches[0])
        if not scope.is_relative_to(directory) or digest(scope) != CARD_SCOPE_SHA:
            raise ValueError('Unreviewed card scope')
        before = scope.read_text()
        # Same bounded-flight/ACK model as the source-bound CD channel.
        # Scope is unchanged: only actual crossings, never local consumers.
        outputs[work / 'card.sdc'] = held_card_scope_text(before)
        new_pocket = new_pocket.replace('source "' + str(scope) + '"',
                                        'source "' + str(work / 'card.sdc') + '"', 1)
        evidence = verify_payload_proof(proof, formatter)
        protected_paths += [formatter, vram, bridge, apf, scope]
    if dma:
        source = one('dma.vhd', 'VHDL_FILE', {DMA_SHA})
        route = ROOT / 'scripts/pocket_dma_address_route.tcl'
        outputs[work / 'dma-address.sdc'] = route.read_text()
        new_q += f'\nset_global_assignment -name SDC_FILE "{work / "dma-address.sdc"}"\n'
        protected_paths += [source, route]
    protected = {str(p): digest(p) for p in protected_paths}
    if any(digest(Path(p)) != sha for p, sha in protected.items()):
        raise ValueError('Project changed during hold-contract preparation')
    work.mkdir()
    for path, text in outputs.items():
        path.write_text(text)
    qsf.write_text(new_q); pocket.write_text(new_pocket)
    report = dict(original=protected, card_enabled=card, dma_enabled=dma,
                  card_payload_min_ns=-13 if card else None, payload_net_min_ns=0 if card else None,
                  payload_max_and_skew_ns=13 if card else None, DMA_bank_net_min_ns=0.5 if dma else None,
                  card_protocol_proof=evidence, required_CPU_hold_ps=150,
                  RTL_and_guest_cycles_unchanged=True, clocks_and_phases_unchanged=True,
                  ordinary_local_setup_and_hold_retained=True, no_per_bit_RTL_padding=True,
                  files=[dict(path=str(p), sha256=digest(p)) for p in outputs],
                  preparer_sha256=digest(Path(__file__)), physical_timing_verified=False,
                  hardware_verified=False, production_selected=False)
    (work / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--expected-top-sha256', required=True)
    parser.add_argument('--card-held-payload', action='store_true')
    parser.add_argument('--dma-address-bank', action='store_true')
    parser.add_argument('--card-payload-proof', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare_project(args.project, args.expected_top_sha256,
          card=args.card_held_payload, dma=args.dma_address_bank, proof=args.card_payload_proof), indent=2))
