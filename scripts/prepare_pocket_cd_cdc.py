#!/usr/bin/env python3
"""Source-bound CD endpoint timing for a fresh Native/live-disc-swap project.

The copied CD body is unchanged; preserve the existing synchronizer flops.
Only source-only CD constraints are replaced. No clock or local path is cut.
Physical flight, settling, reset and all-domain checks remain required.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

from diagnose_pocket_cd_cdc import replace_bounds
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest
from tool_paths import ROOT


CD_SHA = '10717e58e9c4e3d36dcfe36964d15920a899814a5ebcd09246f4ef498b0d909a'
# Reviewed host-side track/offset sharing plus the intervening LibCrypt and
# boot-selection fixes. Ports, toggle chains, source clocks and held-payload
# lifetime are unchanged; the physical scope still rejects unknown fanouts.
CD_SHARED_SHA = 'f6220fd63acdee34204b766a19069f92df40120ff66139df8ca4160c758cb7a7'
# c6fb219 adds a disc-table probe and core-side presence outputs. Admit its
# unchanged default-off profile only: the new payload must specialize away.
# Enabling DISC_STEP or a changing-slot selector needs a different scope.
CD_PRESENCE_SHA = 'eb7ae1070306e7f55f06a0474b96445d96e36825dcc62434263c0243a38953c2'
REVIEWED_CD_SOURCES = {CD_SHA: 'original', CD_SHARED_SHA: 'shared-track-offset',
                       CD_PRESENCE_SHA: 'disc-presence-default-off'}
SYNC_DECLARATIONS = (
    '    reg [2:0] h_req_s=0, h_done_s=0, h_sel_s=0, h_mdone_s=0;',
    '    reg [2:0] c_resp_s=0, c_mount_s=0, c_selack_s=0;',
    '    reg [1:0] c_pending_s=0;',
)
SYNC_ATTRIBUTE = '(* async_reg="true", preserve, dont_merge *) '
FIRST_STAGE = ('set_false_path -to [get_registers {*|disc_stream|h_req_s[0] '
               '*|disc_stream|h_done_s[0] *|disc_stream|h_sel_s[0] '
               '*|disc_stream|h_mdone_s[0] *|disc_stream|c_resp_s[0] '
               '*|disc_stream|c_mount_s[0] *|disc_stream|c_selack_s[0] '
               '*|disc_stream|c_pending_s[0]}]\n')


def preserve_sync_flops(text):
    import hashlib
    if hashlib.sha256(text.encode()).hexdigest() not in REVIEWED_CD_SOURCES:
        raise ValueError('CD CDC source identity differs')
    original = text
    for line in SYNC_DECLARATIONS:
        if text.count(line) != 1:
            raise ValueError('CD synchronizer declaration changed')
        text = text.replace(line, line.replace('    reg ', '    ' + SYNC_ATTRIBUTE + 'reg '), 1)
    if text.replace(SYNC_ATTRIBUTE, '') != original:
        raise ValueError('CD functional body changed')
    return text


def slot_value(token):
    match = re.fullmatch(r"16'([dh])([0-9a-fA-F]+)", token)
    if not match:
        raise ValueError('Expected literal 16-bit slot')
    return int(match[2], 10 if match[1] == 'd' else 16)


def top_contract(text):
    streams = re.findall(r'psx_cd_stream\s*#\((.*?)\)\s+disc_stream\s*\((.*?)\);', text, re.S)
    if len(streams) != 1:
        raise ValueError('Expected one CD stream')
    params, ports = streams[0]
    defaults = re.findall(r'\.DEFAULT_SLOT\((.*?)\)', params)
    lc = re.findall(r'\.LIBCRYPT_SLOT\((.*?)\)', params)
    step = re.findall(r'\.DISC_STEP\((.*?)\)', params)
    if (len(defaults) != 1 or slot_value(defaults[0]) != 101 or
            params.count('.MAX_TRACKS(19)') != 1 or
            params.count(".TRACK_STRIDE(16'd1)") != 1 or
            re.findall(r'\.NBUF\((.*?)\)', params) not in ([], ['2']) or
            lc not in ([], ["16'd0"], ["16'h0"], ["16'h0000"], ["16'd99"], ["16'h63"]) or
            step not in ([], ["16'd0"], ["16'h0"], ["16'h0000"])):
        raise ValueError('Unsupported CD buffer/track/slot/LibCrypt profile')
    for token in ('.host_clk(clk_74a)', '.clk(clk1x)', '.host_reset(cd_host_reset)',
                  '.reset(cd_core_reset)', '.disc_slot_id(swap_slot_id)',
                  '.disc_select_strobe(swap_select_strobe)'):
        if ports.count(token) != 1:
            raise ValueError('CD CDC requires local reset and live selector wiring')
    for module, name in (('psx_disc_swap', 'disc_swap'), ('psx_disc_select', 'disc_select')):
        matches = list(re.finditer(r'^    ' + module + r'\b', text, re.M))
        if len(matches) != 1:
            raise ValueError('Expected one live ' + module)
        start = matches[0].start(); end = text.index(');', start) + 2
        instance = text[start:end]
        if instance.count(name + ' (') != 1 or any(instance.count(t) != 1 for t in
                ('.clk(clk1x)', '.reset(cd_core_reset)')):
            raise ValueError('Disc-swap transport domain differs')
        if module == 'psx_disc_select':
            slot = re.findall(r'\.DISC_SLOT\((.*?)\)', instance)
            if len(slot) != 1 or slot_value(slot[0]) != 101:
                raise ValueError('Selection payload must be the same constant slot 101')
            tokens = ('.change_req(swap_change_req)', '.change_done(swap_change_done)',
                      '.disc_slot_id(swap_slot_id)', '.disc_select_strobe(swap_select_strobe)',
                      '.disc_change_done(cd_disc_change_done)')
        else:
            tokens = (".manual_lid_open(1'b0)", ".swap_request(1'b0)",
                      '.disc_pending(cd_disc_pending)', '.change_req(swap_change_req)',
                      '.change_done(swap_change_done)', '.lid_open(swap_lid_open)')
        if any(instance.count(token) != 1 for token in tokens):
            raise ValueError('Disc-swap handshake wiring differs')
    if '.src_reset(cd_host_reset), .dst_reset(cd_core_reset),' not in text:
        raise ValueError('Expected reviewed loader-derived domain resets')
    for token in ('.disc_change_done(cd_disc_change_done)', '.disc_pending(cd_disc_pending)',
                  '.has_disc(hasCD)', '.trackinfo_write(trackinfo_write)',
                  '.trackinfo_data(trackinfo_data)', '.trackinfo_addr(trackinfo_addr)'):
        if ports.count(token) != 1:
            raise ValueError('CD core metadata/handshake wiring differs')
    if text.count('    assign LIDopen=swap_lid_open;') != 1:
        raise ValueError('Expected live CD lid wiring')
    return bool(lc and slot_value(lc[0]) != 0)


def scope_text(text, config):
    if text.count(FIRST_STAGE) != 1:
        raise ValueError('Expected exact legacy CD first-stage scope')
    return replace_bounds(text.replace(FIRST_STAGE, '', 1), config)


def prepare_project(directory, expected_top_sha256):
    directory = Path(directory).resolve()
    if (not directory.is_relative_to(ROOT / 'build') or
            subprocess.run(['git', 'check-ignore', '--quiet', str(directory / 'cd-cdc/manifest.json')],
                           cwd=ROOT).returncode):
        raise ValueError('Fresh ignored build project required')
    if any((directory / n).exists() for n in ('db', 'incremental_db', 'output_files', 'cd-cdc')):
        raise ValueError('CD CDC must be prepared before map/fit')
    qsf, core, sdc = (directory / n for n in ('apf_top.qsf', 'core_top.sv', 'pocket.sdc'))
    q = qsf.read_text(); selected = assignments(directory, q)
    if [(k, p) for k, p in selected if p.name == 'core_top.sv'] != [('SYSTEMVERILOG_FILE', core)]:
        raise ValueError('Expected one canonical assigned top')
    candidates = [p for k, p in selected if k == 'SYSTEMVERILOG_FILE' and p.name == 'psx_cd_stream.sv']
    if len(candidates) != 1 or digest(core) != expected_top_sha256:
        raise ValueError('CD/top assignment identity differs')
    source = candidates[0]
    adapted = preserve_sync_flops(source.read_text())
    libcrypt = top_contract(core.read_text())
    work = directory / 'cd-cdc'; scope = work / 'pocket_cd_scope.sdc'; config = work / 'cd.sdc'
    new_sdc = scope_text(sdc.read_text(), config)
    token = '"' + str(source) + '"'
    if q.count(token) != 1:
        raise ValueError('Expected one absolute quoted CD source assignment')
    protected = {str(p): digest(p) for p in (core, qsf, sdc, source)}
    new_q = q.replace(token, '"' + str(work / source.name) + '"', 1)
    new_q += ('\nset_instance_assignment -name AUTO_SHIFT_REGISTER_RECOGNITION OFF '
              '-to "core_top:ic|psx_cd_stream:disc_stream"\n')
    if any(digest(Path(p)) != sha for p, sha in protected.items()):
        raise ValueError('Project changed during CD CDC validation')
    work.mkdir()
    (work / source.name).write_text(adapted)
    shutil.copyfile(ROOT / 'scripts/pocket_cd_scope.tcl', scope)
    config.write_text(f'set cd_cdc_libcrypt {int(libcrypt)}\nsource "{scope}"\n')
    qsf.write_text(new_q); sdc.write_text(new_sdc)
    report = dict(original=protected, libcrypt_enabled=libcrypt, live_disc_swap=True,
                  CD_source_sha256=protected[str(source)],
                  CD_source_contract=REVIEWED_CD_SOURCES[protected[str(source)]],
                  functional_CD_body_unchanged=True, synchronizer_flops_preserved=23,
                  constant_selection_slot=101, payload_bound_ns=13,
                  disc_presence_enabled=False, inactive_disc_presence_must_specialize=True,
                  residual_mount_slot_geometry='bit0 and replicas only; timed as held payload, never cut',
                  original_CD_source_unchanged=digest(source) == protected[str(source)],
                  preparer_sha256=digest(Path(__file__)),
                  added=[dict(path=str(p), sha256=digest(p)) for p in sorted(work.iterdir())],
                  map_fit_run=False, physical_timing_verified=False, hardware_verified=False,
                  production_selected=False)
    (work / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--expected-top-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_project(args.project, args.expected_top_sha256), indent=2))
