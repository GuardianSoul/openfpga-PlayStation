#!/usr/bin/env python3
"""Bind the clean memory/card handshakes to endpoint-scoped timing checks.

Integration only: copy the reviewed functional sources without changing their
bodies, preserve their existing synchronizers, and replace the exact inherited
CDC statements. External PSRAM pins still need their separate edge contract.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from prepare_pocket_card_cdc import TOP_NEW, TOP_OLD, ready_source
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest
from tool_paths import ROOT

CARD_SHA = '9eda6aa00f8223ac1285e20e84a48137e13276b55d7fb4e57315be064cf92712'
VRAM_SHA = 'f71e8e6a85ae3e979b067fc795cb9d66ccb176daf0f85756c77d32abe53dae84'
COMMAND_SHA = '4f558ed91203ce733187bc9c3d72cf6450102e08f25da83a927a7a59e001fcc5'
SYNC_ATTRIBUTE = '(* preserve, dont_merge, async_reg="true" *) '
CARD_SYNC = ('s74_rst', 's74_take', 's2x_req', 's2x_ready', 's2x_rack')


def preserved(text, sha, *, card):
    if hashlib.sha256(text.encode()).hexdigest() != sha:
        raise ValueError('Clean memory dependency identity differs')
    if card:
        for name in CARD_SYNC:
            anchor = '    reg [1:0] ' + name + ' = '
            if text.count(anchor) != 1:
                raise ValueError('Clean card synchronizer declaration differs')
            text = text.replace(anchor, '    ' + SYNC_ATTRIBUTE + anchor.lstrip(), 1)
    else:
        anchor = "reg park_m = 1'b0, park_s = 1'b0;"
        if text.count(anchor) != 1:
            raise ValueError('Clean VRAM park synchronizer differs')
        text = text.replace(anchor, SYNC_ATTRIBUTE + anchor, 1)
    raw = text.replace(SYNC_ATTRIBUTE, '')
    if hashlib.sha256(raw.encode()).hexdigest() != sha:
        raise ValueError('Clean memory functional source changed')
    return text


def ports(text, module, name):
    found = re.findall(r'^    ' + module + r'\s+' + name + r'\s*\((.*?)\);', text, re.M | re.S)
    if len(found) != 1:
        raise ValueError('Expected one unparameterized ' + module + ':' + name)
    pairs = re.findall(r'\.(\w+)\(([^()]*(?:\([^()]*\)[^()]*)?)\)', found[0])
    if len(pairs) != len(dict(pairs)):
        raise ValueError('Duplicate clean memory instance port')
    return dict(pairs)


def top_contract(text):
    card = ports(text, 'psx_memcard_slot', 'memcard_slot')
    vram = ports(text, 'psx_vram_psram', 'vram')
    commands = ports(text, 'core_bridge_cmd', 'commands')
    shared = dict(aux_req='mc_aux_req', aux_we='mc_aux_we', aux_addr='mc_aux_addr',
                  aux_din='mc_aux_din', aux_dout='mc_aux_dout', aux_done='mc_aux_done',
                  card_write='mc_card_write')
    expected = dict(clk_74a='clk_74a', clk2x='clk2x', reset_2x='mc_reset2x[1]', busy='mc_busy',
                    load_done='memcard_load_done', bridge_addr='bridge_addr', bridge_wr='bridge_wr',
                    bridge_wr_data='bridge_wr_data', bridge_rd='bridge_rd',
                    bridge_rd_data='memcard_rd_data', overflow='')
    if card != expected | shared or any(vram.get(k) != v for k, v in shared.items()):
        raise ValueError('Clean card/AUX ownership differs')
    if (vram.get('clk'), vram.get('reset'), vram.get('park')) != ('clk2x', '!cpu_locked', 'vram_park'):
        raise ValueError('Clean VRAM domain/park wiring differs')
    if commands.get('clk') != 'clk_74a' or commands.get('reset_n') != 'reset_n':
        raise ValueError('Clean park launch domain differs')
    for signal in shared.values():
        if len(re.findall(r'\b' + signal + r'\b', text)) != 3:
            raise ValueError('Clean AUX net is not exclusive: ' + signal)
    ready = ready_source(text)
    for anchor in (TOP_OLD, '    wire vram_park=!reset_n;   // synchronised inside psx_vram_psram',
                   '    assign bridge_endian_little=1;',
                   '    always @(posedge clk_74a) begin mc_idle74<={mc_idle74[0],!mc_busy}; boot_ready_card<=(' + ready + ') && mc_idle74[1]; end',
                   '    assign memcard_load_done=(' + ready + ');'):
        if text.count(anchor) != 1:
            raise ValueError('Clean memory host launch contract differs: ' + anchor)
    return text.replace(TOP_OLD, TOP_NEW, 1)


def legacy_lines():
    card = '*|psx_memcard_slot:memcard_slot|'; vram = '*|psx_vram_psram:vram|*'
    bundle = ' '.join(card + r for r in ('req_we_74', 'req_addr_74[*]', 'req_data_74[*]', 'ready_tgl_74*'))
    dest = ' '.join([card + r for r in ('aux_req', 'busy', 'checked', 'fmt_active', 'fmt_idx[*]', 'own',
                                       'ready_seen', 'req_seen_2x', 'take_tgl_2x', 'w0_magic', 'w0_seen')] + [vram])
    return [
        'set_false_path -to [get_registers {*|psx_vram_psram:vram|park_m}]\n',
        'set_false_path -to [get_registers {' + ' '.join(card + r for r in
            ('s2x_req[0]', 's2x_ready[0]', 's2x_rack[0]', 's74_rst[0]', 's74_take[0]')) + ' *|mc_idle74[0]}]\n',
        f'set_max_delay 13 -from [get_registers {{{bundle}}}] -to [get_registers {{{dest}}}]\n',
        f'set_min_delay -13 -from [get_registers {{{bundle}}}] -to [get_registers {{{dest}}}]\n',
        f'set_max_delay 13 -from [get_registers {{{vram}}}] -to [get_registers {{{card}bridge_rd_data[*]}}]\n',
        f'set_min_delay -13 -from [get_registers {{{vram}}}] -to [get_registers {{{card}bridge_rd_data[*]}}]\n',
    ]


def scope_text(text, scope):
    for line in legacy_lines():
        if text.count(line) != 1:
            raise ValueError('Expected exact legacy clean memory constraints')
        text = text.replace(line, '', 1)
    for line in text.splitlines():
        if re.match(r'\s*set_(?:false_path|max_delay|min_delay|net_delay|max_skew)\b', line) and any(
                word in line for word in ('psx_memcard_slot:', 'psx_vram_psram:', 'mc_idle74')):
            raise ValueError('Unknown additional clean memory constraint')
    if any(c in str(scope) for c in ('"', '$', '[', ']', '\n', '\\')):
        raise ValueError('Unsupported clean memory scope path')
    return text + f'\nsource "{scope}"\n'


def prepare_project(directory, expected_top_sha256):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT / 'build') or subprocess.run(
            ['git', 'check-ignore', '--quiet', str(directory / 'clean-memory-cdc/manifest.json')], cwd=ROOT).returncode:
        raise ValueError('Fresh ignored build project required')
    if any((directory / n).exists() for n in ('db', 'incremental_db', 'output_files', 'clean-memory-cdc')):
        raise ValueError('Clean memory CDC must be prepared before map/fit')
    qsf, core, sdc = (directory / n for n in ('apf_top.qsf', 'core_top.sv', 'pocket.sdc'))
    q = qsf.read_text(); selected = assignments(directory, q)
    if [(k, p) for k, p in selected if p.name == 'core_top.sv'] != [('SYSTEMVERILOG_FILE', core)]:
        raise ValueError('Expected canonical assigned top')
    if digest(core) != expected_top_sha256:
        raise ValueError('Clean memory top identity differs')
    sources = {}
    for name, kind, sha in (('psx_memcard_slot.sv', 'SYSTEMVERILOG_FILE', CARD_SHA),
                            ('psx_vram_psram.sv', 'SYSTEMVERILOG_FILE', VRAM_SHA),
                            ('core_bridge_cmd.v', 'VERILOG_FILE', COMMAND_SHA)):
        found = [p for k, p in selected if p.name == name and k == kind]
        if len(found) != 1 or digest(found[0]) != sha:
            raise ValueError('Clean dependency identity differs: ' + name)
        sources[name] = found[0]
    work = directory / 'clean-memory-cdc'
    new_sdc = scope_text(sdc.read_text(), work / 'memory.sdc')
    new_top = top_contract(core.read_text())
    adapted = {name: preserved(sources[name].read_text(), sha, card=card) for name, sha, card in (
        ('psx_memcard_slot.sv', CARD_SHA, True), ('psx_vram_psram.sv', VRAM_SHA, False))}
    new_q = q
    for name in adapted:
        token = '"' + str(sources[name]) + '"'
        if q.count(token) != 1:
            raise ValueError('Expected one absolute quoted clean source assignment')
        new_q = new_q.replace(token, '"' + str(work / name) + '"', 1)
    for owner in ('core_top:ic|psx_memcard_slot:memcard_slot', 'core_top:ic|psx_vram_psram:vram', 'core_top:ic|mc_idle74*'):
        new_q += f'\nset_instance_assignment -name AUTO_SHIFT_REGISTER_RECOGNITION OFF -to "{owner}"\n'
    protected = {str(p): digest(p) for p in (qsf, core, sdc, *sources.values())}
    scope = (ROOT / 'scripts/pocket_clean_memory_scope.tcl').read_text()
    if any(digest(Path(p)) != sha for p, sha in protected.items()):
        raise ValueError('Clean memory project changed during preparation')
    work.mkdir()
    for name, text in adapted.items():
        (work / name).write_text(text)
    (work / 'memory.sdc').write_text(scope)
    qsf.write_text(new_q); core.write_text(new_top); sdc.write_text(new_sdc)
    result = dict(original=protected, functional_sources_unchanged=True,
                  preserved_sync_flops=14, payload_max_ns=13, payload_min_ns=-13,
                  physical_net_max_ns=13, physical_net_min_ns=0,
                  ordinary_local_and_settling_paths_timed=True,
                  endpoint_scope_sha256=hashlib.sha256(scope.encode()).hexdigest(),
                  added=[dict(path=str(p), sha256=digest(p)) for p in sorted(work.iterdir())],
                  preparer_sha256=digest(Path(__file__)), physical_timing_verified=False,
                  external_pins_qualified=False, hardware_verified=False, production_selected=False)
    (work / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('project', type=Path)
    p.add_argument('--expected-top-sha256', required=True)
    a = p.parse_args()
    print(json.dumps(prepare_project(a.project, a.expected_top_sha256), indent=2))
