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

from diagnose_pocket_cd_cdc import HELD, MAX, MINS, replace_bounds
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest
from tool_paths import ROOT

MENU_SHA = 'd5115fc7f0e6e73b4e1820271744e8d2a668ce273884405cddd91826c0140b22'
SWAP_SHA = 'a81899cb61fde35da7077daf9deb296f9066fc3edda8fe8d6d3a469a0f80c56d'
MENU_FIRST_STAGE = 'set_false_path -to [get_registers {*|disc_menu|disc_meta[*]}]\n'
MENU_HELD = HELD + ' *|disc_stream|m_present[*]'
MENU_MAX = f'set_max_delay 13 -from [get_registers {{{MENU_HELD}}}]\n'
MENU_MINS = tuple(f'set_min_delay {n} -from [get_registers {{{MENU_HELD}}}]\n' for n in ('0', '-13'))


def legacy_held_lists(*, disc_menu=False, table_offset=False, libcrypt=False):
    """Exact released source lists only; physical constraints still use endpoints.

    Earlier generators omitted m_tb/m_lc_bytes. Admit those known lists for
    replacement, but include the complete enabled payload in the new scope.
    """
    bases = [MENU_HELD] if disc_menu else [HELD, MENU_HELD]
    suffixes = ['']
    if libcrypt:
        suffixes += [' *|disc_stream|m_lc_bytes[*]']
    if table_offset:
        suffixes += [s+' *|disc_stream|m_tb' for s in list(suffixes)]
    return tuple(base+s for base in bases for s in suffixes)


CD_SHA = '10717e58e9c4e3d36dcfe36964d15920a899814a5ebcd09246f4ef498b0d909a'
# Reviewed host-side track/offset sharing plus the intervening LibCrypt and
# boot-selection fixes. Ports, toggle chains, source clocks and held-payload
# lifetime are unchanged; the physical scope still rejects unknown fanouts.
CD_SHARED_SHA = 'f6220fd63acdee34204b766a19069f92df40120ff66139df8ca4160c758cb7a7'
# c6fb219 adds a disc-table probe and core-side presence outputs. Admit its
# unchanged default-off profile only: the new payload must specialize away.
# Enabling DISC_STEP or a changing-slot selector needs a different scope.
CD_PRESENCE_SHA = 'eb7ae1070306e7f55f06a0474b96445d96e36825dcc62434263c0243a38953c2'
# 80efd31 changes only the probe_hit declaration and M_PROBE's file-size
# check. M_PROBE's sole entry still requires DISC_STEP!=0; the supported
# zero-step profile cannot reach it. Keep the same topology/payload guards.
CD_FILE_PRESENCE_SHA = 'b3b0c90df40da21c305f899f5377a840788a09ef484dd0dbdd77a102c67d5751'
# 7f3d8a9: optional 400-word table in sector buffer NBUF-1. m_tb is
# published with mount_t and held until mount_done_t returns. The buffer
# lease uses the same acknowledgement; its read address/data stay core-local.
CD_TABLE_SHA = '0933aff18d77b0e05fbfb7b3899d162759c09b091ea667a5b704283dc5912cd3'
# Optional side-file selector specialization; held mount data and both
# clock-domain handshakes are byte-identical to CD_TABLE_SHA.
CD_SIDE_SLOT_SHA = 'fba50c640276dd73a7d096a28fecdae3b6ec6200d530ae493f1d79eb357d1a84'
REVIEWED_CD_SOURCES = {CD_SHA: 'original', CD_SHARED_SHA: 'shared-track-offset',
                       CD_PRESENCE_SHA: 'disc-presence-default-off',
                       CD_FILE_PRESENCE_SHA: 'disc-file-presence-default-off',
                       CD_TABLE_SHA: 'optional-sector-buffer-track-table',
                       CD_SIDE_SLOT_SHA: 'constant-single-side-file-selector'}
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


def top_profile(text):
    streams = re.findall(r'psx_cd_stream\s*#\((.*?)\)\s+disc_stream\s*\((.*?)\);', text, re.S)
    if len(streams) != 1:
        raise ValueError('Expected one CD stream')
    params, ports = streams[0]
    named = re.findall(r'\.(\w+)\(([^()]+)\)', params)
    if (len(named) != len(dict(named)) or
            set(dict(named)) - {'DEFAULT_SLOT', 'MAX_TRACKS', 'TRACK_STRIDE', 'NBUF', 'LIBCRYPT_SLOT', 'DISC_STEP', 'TABLE_OFFSET'} or
            re.sub(r'\.(\w+)\(([^()]+)\)|[\s,]', '', params)):
        raise ValueError('Unknown or duplicate CD parameter')
    defaults = re.findall(r'\.DEFAULT_SLOT\((.*?)\)', params)
    lc = re.findall(r'\.LIBCRYPT_SLOT\((.*?)\)', params)
    step = re.findall(r'\.DISC_STEP\((.*?)\)', params)
    table = re.findall(r'\.TABLE_OFFSET\((.*?)\)', params)
    if table not in ([], ["16'd0"], ["16'h0"], ["16'h0000"], ["16'd1"], ["16'h1"], ["16'h0001"]):
        raise ValueError('Unsupported CD table offset')
    table_offset = slot_value(table[0]) if table else 0
    tracks = 1 if table_offset else 19
    menu = bool(re.search(r'^\s*psx_disc_menu\b', text, re.M))
    if (len(defaults) != 1 or slot_value(defaults[0]) != 101 or
            params.count(f'.MAX_TRACKS({tracks})') != 1 or
            params.count(".TRACK_STRIDE(16'd1)") != 1 or
            re.findall(r'\.NBUF\((.*?)\)', params) not in ([], ['2']) or
            lc not in ([], ["16'd0"], ["16'h0"], ["16'h0000"], ["16'd99"], ["16'h63"]) or
            (step != ["16'd100"] if menu else
             step not in ([], ["16'd0"], ["16'h0"], ["16'h0000"]))):
        raise ValueError('Unsupported CD buffer/track/slot/LibCrypt profile')
    if table_offset and lc not in ([], ["16'd0"], ["16'h0"], ["16'h0000"]):
        raise ValueError('One-file table profile requires LibCrypt slot disabled')
    for token in ('.host_clk(clk_74a)', '.clk(clk1x)', '.host_reset(cd_host_reset)',
                  '.reset(cd_core_reset)', '.disc_slot_id(swap_slot_id)',
                  '.disc_select_strobe(swap_select_strobe)'):
        if ports.count(token) != 1:
            raise ValueError('CD CDC requires local reset and live selector wiring')
    selector = ('psx_disc_menu', 'disc_menu') if menu else ('psx_disc_select', 'disc_select')
    if menu and re.search(r'^\s*psx_disc_select\b', text, re.M):
        raise ValueError('Disc menu and fixed selector cannot coexist')
    for module, name in (('psx_disc_swap', 'disc_swap'), selector):
        matches = list(re.finditer(r'^    ' + module + r'\b', text, re.M))
        if len(matches) != 1:
            raise ValueError('Expected one live ' + module)
        start = matches[0].start(); end = text.index(');', start) + 2
        instance = text[start:end]
        if instance.count(name + ' (') != 1 or any(instance.count(t) != 1 for t in
                ('.clk(clk1x)', '.reset(cd_core_reset)')):
            raise ValueError('Disc-swap transport domain differs')
        if module in ('psx_disc_select', 'psx_disc_menu'):
            if menu:
                header = "psx_disc_menu #(.FIRST_SLOT(16'd101), .STEP(16'd100), .DISCS(4)) disc_menu ("
                if instance.count(header) != 1:
                    raise ValueError('Expected the released four-disc menu profile')
            else:
                slot = re.findall(r'\.DISC_SLOT\((.*?)\)', instance)
                if len(slot) != 1 or slot_value(slot[0]) != 101:
                    raise ValueError('Selection payload must be the same constant slot 101')
            tokens = ('.change_req(swap_change_req)', '.change_done(swap_change_done)',
                      '.disc_slot_id(swap_slot_id)', '.disc_select_strobe(swap_select_strobe)',
                      '.disc_change_done(cd_disc_change_done)')
            if menu:
                tokens += ('.disc_setting(disc_setting)', '.discs_present(cd_discs_present)',
                           '.swap_request(disc_menu_request)')
        else:
            tokens = (".manual_lid_open(1'b0)",
                      '.swap_request(disc_menu_request)' if menu else ".swap_request(1'b0)",
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
    if menu:
        if ports.count('.discs_present(cd_discs_present)') != 1:
            raise ValueError('Disc presence must feed the menu')
        for net in ('swap_slot_id', 'swap_select_strobe', 'cd_discs_present', 'disc_menu_request'):
            if len(re.findall(r'\b' + net + r'\b', text)) != 3:
                raise ValueError('Disc menu handshake net is not exclusive: ' + net)
    return dict(libcrypt=bool(lc and slot_value(lc[0]) != 0), disc_menu=menu,
                table_offset=table_offset)


def top_contract(text):
    return top_profile(text)['libcrypt']


def preserve_menu_flops(text):
    import hashlib
    if hashlib.sha256(text.encode()).hexdigest() != MENU_SHA:
        raise ValueError('Disc menu source identity differs')
    old = '    reg [1:0] disc_sync=0, disc_last=0, wanted=0, selected=0;'
    new = ('    (* async_reg="true", preserve, dont_merge *) reg [1:0] disc_sync=0;\n'
           '    (* preserve, dont_merge *) reg [1:0] disc_last=0;\n'
           '    reg [1:0] wanted=0, selected=0;')
    if text.count(old) != 1:
        raise ValueError('Disc menu synchronizer declaration differs')
    return text.replace(old, new, 1)


def scope_text(text, config, *, disc_menu=False, table_offset=False, libcrypt=False):
    if text.count(FIRST_STAGE) != 1:
        raise ValueError('Expected exact legacy CD first-stage scope')
    text = text.replace(FIRST_STAGE, '', 1)
    if not disc_menu and MENU_FIRST_STAGE in text:
        raise ValueError('Disc menu constraints require the menu profile')
    choices = []
    for held in legacy_held_lists(disc_menu=disc_menu, table_offset=table_offset, libcrypt=libcrypt):
        maximum = f'set_max_delay 13 -from [get_registers {{{held}}}]\n'
        for i, bound in enumerate(('0', '-13')):
            minimum = f'set_min_delay {bound} -from [get_registers {{{held}}}]\n'
            if text.count(maximum)==1 and text.count(minimum)==1:
                choices.append((maximum,minimum,i))
    if len(choices)!=1 or (disc_menu and text.count(MENU_FIRST_STAGE)!=1):
        raise ValueError('Expected exact released CD/menu constraints')
    maximum, minimum, i = choices[0]
    text = text.replace(maximum, MAX, 1).replace(minimum, MINS[i], 1)
    if disc_menu:
        text = text.replace(MENU_FIRST_STAGE, '', 1)
    return replace_bounds(text, config)


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
    profile = top_profile(core.read_text())
    libcrypt, menu = profile['libcrypt'], profile['disc_menu']
    table_offset = profile['table_offset']
    if '.TABLE_OFFSET(' in core.read_text() and digest(source) not in (CD_TABLE_SHA, CD_SIDE_SLOT_SHA):
        raise ValueError('TABLE_OFFSET requires the reviewed table-capable source')
    menu_source = None
    dependencies = {}
    if menu:
        if digest(source) not in (CD_FILE_PRESENCE_SHA, CD_TABLE_SHA, CD_SIDE_SLOT_SHA):
            raise ValueError('Disc menu requires the reviewed file-presence stream')
        for name, sha in (('psx_disc_menu.sv', MENU_SHA), ('psx_disc_swap.sv', SWAP_SHA)):
            found = [p for k, p in selected if k == 'SYSTEMVERILOG_FILE' and p.name == name]
            if len(found) != 1 or digest(found[0]) != sha:
                raise ValueError('Disc menu dependency identity differs: ' + name)
            dependencies[str(found[0])] = sha
            if name == 'psx_disc_menu.sv':
                menu_source = found[0]
    work = directory / 'cd-cdc'; scope = work / 'pocket_cd_scope.sdc'; config = work / 'cd.sdc'
    new_sdc = scope_text(sdc.read_text(), config, disc_menu=menu,
                         table_offset=bool(table_offset), libcrypt=libcrypt)
    token = '"' + str(source) + '"'
    if q.count(token) != 1:
        raise ValueError('Expected one absolute quoted CD source assignment')
    protected = {str(p): digest(p) for p in (core, qsf, sdc, source)} | dependencies
    new_q = q.replace(token, '"' + str(work / source.name) + '"', 1)
    adapted_menu = None
    if menu:
        protected[str(menu_source)] = digest(menu_source)
        adapted_menu = preserve_menu_flops(menu_source.read_text())
        token = '"' + str(menu_source) + '"'
        if q.count(token) != 1:
            raise ValueError('Expected one quoted menu source assignment')
        new_q = new_q.replace(token, '"' + str(work / menu_source.name) + '"', 1)
        new_q += ('\nset_instance_assignment -name AUTO_SHIFT_REGISTER_RECOGNITION OFF '
                  '-to "core_top:ic|psx_disc_menu:disc_menu"\n')
    new_q += ('\nset_instance_assignment -name AUTO_SHIFT_REGISTER_RECOGNITION OFF '
              '-to "core_top:ic|psx_cd_stream:disc_stream"\n')
    if any(digest(Path(p)) != sha for p, sha in protected.items()):
        raise ValueError('Project changed during CD CDC validation')
    work.mkdir()
    (work / source.name).write_text(adapted)
    if menu:
        (work / menu_source.name).write_text(adapted_menu)
    shutil.copyfile(ROOT / 'scripts/pocket_cd_scope.tcl', scope)
    config.write_text(f'set cd_cdc_libcrypt {int(libcrypt)}\nset cd_cdc_disc_menu {int(menu)}\n'
                      f'set cd_cdc_table_offset {table_offset}\nsource "{scope}"\n')
    qsf.write_text(new_q); sdc.write_text(new_sdc)
    report = dict(original=protected, libcrypt_enabled=libcrypt, live_disc_swap=True,
                  CD_source_sha256=protected[str(source)],
                  CD_source_contract=REVIEWED_CD_SOURCES[protected[str(source)]],
                  functional_CD_body_unchanged=True, synchronizer_flops_preserved=23,
                  table_offset=table_offset, maximum_track_files=1 if table_offset else 19,
                  table_payload=('m_tb held until mount acknowledgement; all physical copies timed'
                                 if table_offset else 'm_tb must specialize away'),
                  table_RAM_read_path='core-clock address and data remain timed',
                  constant_selection_slot=None if menu else 101, payload_bound_ns=13,
                  disc_presence_enabled=menu, inactive_disc_presence_must_specialize=not menu,
                  selection_slots=[101, 201, 301, 401] if menu else [101],
                  residual_mount_slot_geometry=('four-disc bit-equivalence classes; all physical copies timed' if menu else
                      'bit0 and replicas only; timed as held payload, never cut'),
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
