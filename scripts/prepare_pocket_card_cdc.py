#!/usr/bin/env python3
"""Prepare source-bound Native card CDC constraints before map/fit.

Preserve existing synchronizers without changing their logic. Replace only
the known source-only card bounds and first-stage cuts. Actual fanouts and
every physical synchronizer stage must match the reviewed scope after map.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest
from tool_paths import ROOT
from prepare_pocket_disc_swap import BOOT_WAIT


CARD_SHA = 'f565318fee5257394f99b3235b1b0c1999ad590c4fa6505722dedac4604e6f86'
VRAM_SHA = '3dfd5dbd926d69f930248b54129517905118b360d0cc799752b24930fd6ffe7d'
PATCH_SHA = 'dd1df6f2afcdcbf584fac78577f22c51c55f822d178c8271bb953c4a045083c2'
SYNC = ('rd_tog_s', 'rd_ack_s', 'done_s')
ATTRIBUTES = '''   -- Timing-only preservation of the existing card CDC stages.
   attribute preserve : boolean;
   attribute dont_merge : boolean;
   attribute async_reg : string;
   attribute altera_attribute : string;
''' + ''.join(
    f'   attribute preserve of {name} : signal is true;\n'
    f'   attribute dont_merge of {name} : signal is true;\n'
    f'   attribute async_reg of {name} : signal is "true";\n'
    f'   attribute altera_attribute of {name} : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";\n'
    for name in SYNC)
TOP_OLD = '    (* async_reg="true" *) reg [1:0] mc_idle74=0;'
TOP_NEW = TOP_OLD.replace('async_reg="true"', 'async_reg="true", preserve, dont_merge')
HELD = '*|pocket_memcard_format:*|rd_word[*] *|pocket_memcard_format:*|rd_data[*]'
MAX = f'set_max_delay 13 -from [get_registers {{{HELD}}}]\n'
MIN = f'set_min_delay 0 -from [get_registers {{{HELD}}}]\n'
FIRST = ('set_false_path -to [get_registers {*|pocket_memcard_format:*|rd_tog_s[0] '
         '*|pocket_memcard_format:*|rd_ack_s[0] *|mc_idle74[0]}]\n')
DONE = 'set_false_path -to [get_registers {*|pocket_memcard_format:*|done_s[0]}]\n'


def preserve_sync_flops(text):
    if hashlib.sha256(text.encode()).hexdigest() != CARD_SHA:
        raise ValueError('Card formatter source identity differs')
    for name in SYNC:
        if text.count(f"   signal {name}") != 1 or not re.search(
                rf'signal {name}\s+: std_logic_vector\(2 downto 0\)', text):
            raise ValueError('Card synchronizer declaration differs')
    marker = '\nbegin\n\n   ---------------------------------------------------------------- clk_74a'
    if text.count(marker) != 1:
        raise ValueError('Card architecture boundary differs')
    out = text.replace(marker, '\n' + ATTRIBUTES + marker, 1)
    if out.replace('\n' + ATTRIBUTES, '', 1) != text:
        raise ValueError('Card functional body changed')
    return out


def ready_source(text):
    if re.search(r'\bboot_disc_gate\b', text):
        if text.count(BOOT_WAIT.replace('READY_SIGNAL', 'bios_go')) != 1:
            raise ValueError('Native card disc-ready launch contract differs')
        if text.count('.disc_settled(cd_disc_settled)') != 1:
            raise ValueError('Native card disc-ready source differs')
        return 'boot_disc_gate'
    return 'bios_go'


def top_contract(text):
    def instance(module, name):
        found = re.findall(r'^    ' + module + r'\s+' + name + r'\s*\((.*?)\);', text, re.M | re.S)
        if len(found) != 1:
            raise ValueError('Expected one Native ' + module)
        pairs = re.findall(r'\.(\w+)\(([^()]*(?:\([^()]*\)[^()]*)?)\)', found[0])
        if len(dict(pairs)) != len(pairs):
            raise ValueError('Duplicate Native port')
        return dict(pairs)

    card = instance('pocket_memcard_format', 'memcard_slot')
    vram = instance('vram_psram', 'vram')
    expected = dict(clk_74a='clk_74a', clk2x='clk2x', reset_2x='mc_reset2x[1]',
                    busy='mc_busy', load_done='memcard_load_done', bridge_addr='bridge_addr',
                    bridge_wr='bridge_wr', bridge_wr_data='bridge_wr_data', bridge_rd='bridge_rd',
                    bridge_rd_data='memcard_rd_data', overflow='')
    shared = dict(aux_req='mc_aux_req', aux_we='mc_aux_we', aux_addr='mc_aux_addr',
                  aux_din='mc_aux_din', aux_dout='mc_aux_dout', aux_done='mc_aux_done',
                  card_write='mc_card_write')
    if card != expected | shared or any(vram.get(k) != v for k, v in shared.items()):
        raise ValueError('Native card/AUX port wiring differs')
    if vram.get('clk') != 'clk2x' or vram.get('reset') != '!cpu_locked':
        raise ValueError('Native AUX domain differs')
    for port, signal in shared.items():
        # Only declarations and these two instance ports may reference the AUX
        # wires. An extra consumer/driver invalidates the held-output contract.
        if len(re.findall(r'\b' + signal + r'\b', text)) != 3:
            raise ValueError('Native AUX net is not exclusive: ' + signal)
    ready = ready_source(text)
    for anchor in (TOP_OLD,
                   '    always @(posedge clk_74a) begin mc_idle74<={mc_idle74[0],!mc_busy}; boot_ready_card<=(' + ready + ') && mc_idle74[1]; end',
                   '    assign memcard_load_done=(' + ready + ');',
                   '    psx_bios_patch bios_patch (.clk(clk_74a), .reset(!power_reset_n), .loader_ready(passed), .host_run(reset_n),',
                   '.skip(logo_skip), .bios_ready(bios_go), .patched(bios_patched),'):
        if text.count(anchor) != 1:
            raise ValueError('Native card launch/domain contract differs')
    return text.replace(TOP_OLD, TOP_NEW, 1)


def scope_text(text, scope):
    for line in (FIRST, DONE, MAX, MIN):
        if text.count(line) != 1:
            raise ValueError('Expected exact legacy card constraints')
        text = text.replace(line, '', 1)
    if any(re.match(r'\s*set_(?:false_path|max_delay|min_delay|net_delay|max_skew)\b', line)
           and ('pocket_memcard_format:' in line or 'mc_idle74' in line)
           for line in text.splitlines()):
        raise ValueError('Unknown additional card constraint')
    if any(c in str(scope) for c in ('"', '$', '[', ']', '\n', '\\')):
        raise ValueError('Unsupported card scope path')
    return text + f'\nsource "{scope}"\n'


def prepare_project(directory, expected_top_sha256):
    directory = Path(directory).resolve()
    if (not directory.is_relative_to(ROOT / 'build') or
            subprocess.run(['git', 'check-ignore', '--quiet', str(directory / 'card-cdc/manifest.json')],
                           cwd=ROOT).returncode):
        raise ValueError('Fresh ignored build project required')
    if any((directory / n).exists() for n in ('db', 'incremental_db', 'output_files', 'card-cdc')):
        raise ValueError('Card CDC must be prepared before map/fit')
    qsf, core, sdc = (directory / n for n in ('apf_top.qsf', 'core_top.sv', 'pocket.sdc'))
    q = qsf.read_text(); selected = assignments(directory, q)
    if [(k, p) for k, p in selected if p.name == 'core_top.sv'] != [('SYSTEMVERILOG_FILE', core)]:
        raise ValueError('Expected one canonical assigned top')
    if digest(core) != expected_top_sha256:
        raise ValueError('Card top identity differs')
    sources = {}
    for name, kind, sha in (('pocket_memcard_format.vhd', 'VHDL_FILE', CARD_SHA),
                            ('vram_psram.vhd', 'VHDL_FILE', VRAM_SHA),
                            ('psx_bios_patch.sv', 'SYSTEMVERILOG_FILE', PATCH_SHA)):
        found = [p for k, p in selected if p.name == name and k == kind]
        if len(found) != 1 or digest(found[0]) != sha:
            raise ValueError('Card dependency identity differs: ' + name)
        sources[name] = found[0]
    source = sources['pocket_memcard_format.vhd']
    adapted = preserve_sync_flops(source.read_text()); top = top_contract(core.read_text())
    disc_ready = ready_source(core.read_text()) == 'boot_disc_gate'
    work = directory / 'card-cdc'; scope = work / 'card.sdc'
    new_sdc = scope_text(sdc.read_text(), scope)
    token = '"' + str(source) + '"'
    if q.count(token) != 1:
        raise ValueError('Expected one quoted absolute card source assignment')
    protected = {str(p): digest(p) for p in (qsf, core, sdc, *sources.values())}
    new_q = q.replace(token, '"' + str(work / source.name) + '"', 1)
    for owner in ('core_top:ic|pocket_memcard_format:memcard_slot', 'core_top:ic|mc_idle74*'):
        new_q += f'\nset_instance_assignment -name AUTO_SHIFT_REGISTER_RECOGNITION OFF -to "{owner}"\n'
    if any(digest(Path(p)) != sha for p, sha in protected.items()):
        raise ValueError('Card project changed during preparation')
    work.mkdir()
    (work / source.name).write_text(adapted)
    scope.write_text(('set card_cdc_boot_disc_gate 1\n' if disc_ready else '') +
                     (ROOT / 'scripts/pocket_card_scope.tcl').read_text())
    qsf.write_text(new_q); core.write_text(top); sdc.write_text(new_sdc)
    report = dict(original=protected, card_functional_body_unchanged=True,
                  top_changes_only_existing_sync_attributes=True, preserved_sync_flops=11,
                  payload_max_ns=13, payload_min_ns=0, source_only_bounds_removed=True,
                  load_done_launch='core_top:ic|boot_disc_gate' if disc_ready else 'core_top:ic|psx_bios_patch:bios_patch|bios_ready',
                  ordinary_local_clock_relationships_retained=True,
                  added=[dict(path=str(p), sha256=digest(p)) for p in sorted(work.iterdir())],
                  preparer_sha256=digest(Path(__file__)), physical_timing_verified=False,
                  actual_APF_pacing_verified=False, hardware_verified=False, production_selected=False)
    (work / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--expected-top-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_project(args.project, args.expected_top_sha256), indent=2))
