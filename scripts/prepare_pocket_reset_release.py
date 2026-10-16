#!/usr/bin/env python3
"""Replace a broad power-reset exception with per-domain reset release.

Only copied, uncompiled Native projects are accepted. The original RTL stays
unchanged. Guest warm reset remains separate from the common transport reset.
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


BOOT_SHA = '729f90b2ea18d31f14fc00e46174a8f0c3dff1a34b51c63b73571b6f487e8ee7'
CDC_SHA = '798ea6be1222499119686e3f27fc749ddb5afaac16d7440de61665d0ae3de8d6'
CDC_EARLY_SHA = '92948dfb6db7802e09d1313c8bf9658815b030113fe38053cb314507e1bcab62'
POWER_CUT = 'set_false_path -from [get_registers {*|power_reset_n}]\n'

# The loader already provides the exact common transport reset, independently
# released by each of these same two clocks. Reuse its outputs instead of
# duplicating reset pipelines with identical inputs and behaviour.
CD_RESET_OLD = '    wire cd_stream_reset=!power_reset_n || !cpu_locked;'
CD_RESET_NEW = '    wire cd_host_reset, cd_core_reset;'
LOADER_CLOCKS = '        .src_clk(clk_74a), .dst_clk(clk1x), .reset_async(!power_reset_n || !cpu_locked),'
LOADER_OUTPUTS = '        .src_reset(cd_host_reset), .dst_reset(cd_core_reset),'
LOADER_OLD = '    psx_mem_cdc loader_cdc (\n' + LOADER_CLOCKS + '\n'
LOADER_NEW = CD_RESET_NEW + '\n' + LOADER_OLD + LOADER_OUTPUTS + '\n'
TOP_PATCHES = [
    (LOADER_OLD, LOADER_NEW),
    (CD_RESET_OLD, '    // CD transport uses the loader mailbox domain reset outputs.'),
    ('.host_reset(cd_stream_reset)', '.host_reset(cd_host_reset)'),
    ('.reset(cd_stream_reset)', '.reset(cd_core_reset)'),
]

BOOT_PIPES = '''    // Transport acknowledgement must work while guest reset is asserted.
    // Do not use cpu_reset here: the loader waits for this acknowledgement.
    (* async_reg="true", preserve, dont_merge *) reg [2:0] cpu_transport_reset_pipe=3'b111;
    (* async_reg="true", preserve, dont_merge *) reg [2:0] host_transport_reset_pipe=3'b111;
    always @(posedge cpu_clk or posedge reset_async)
        if(reset_async) cpu_transport_reset_pipe<=3'b111;
        else cpu_transport_reset_pipe<={cpu_transport_reset_pipe[1:0],1'b0};
    always @(posedge host_clk or posedge reset_async)
        if(reset_async) host_transport_reset_pipe<=3'b111;
        else host_transport_reset_pipe<={host_transport_reset_pipe[1:0],1'b0};
    wire cpu_transport_reset=cpu_transport_reset_pipe[2];
    wire host_transport_reset=host_transport_reset_pipe[2];

'''


def patches(text, changes):
    original = text
    for before, after in changes:
        if text.count(before) != 1:
            raise ValueError('Reset-release source anchor changed: ' + before)
        text = text.replace(before, after, 1)
    restored = text
    for before, after in reversed(changes):
        if restored.count(after) != 1:
            raise ValueError('Ambiguous reset-release restoration')
        restored = restored.replace(after, before, 1)
    if restored != original:
        raise ValueError('Unrestorable reset-release source')
    return text


def checked(text, sha):
    if hashlib.sha256(text.encode()).hexdigest() != sha:
        raise ValueError('Reset-release source identity differs')


def adapt_top(text):
    if text.count('psx_cd_stream #(') != 1 or text.count(' disc_stream (') != 1:
        raise ValueError('Expected one connected CD stream')
    if text.count(LOADER_OLD) != 1:
        raise ValueError('Expected exact common loader clock/reset wiring')
    start = text.index(LOADER_OLD)
    loader = text[start:text.index(');', start)]
    if '.src_reset(' in loader or '.dst_reset(' in loader:
        raise ValueError('Expected unused loader domain reset outputs')
    start = text.index(' disc_stream (')
    cd = text[start:text.index(');', start) + 2]
    if any(cd.count(token) != 1 for token in (
            '.host_clk(clk_74a)', '.clk(clk1x)',
            '.host_reset(cd_stream_reset)', '.reset(cd_stream_reset)')):
        raise ValueError('CD and loader transport clock/reset domains differ')
    # --disc-swap adds two clk1x consumers of the same transport reset.
    # Require the complete known pair; never guess a domain for another
    # consumer or replace arbitrary reset ports by their signal name alone.
    optional = []
    for module, pattern in (
            ('psx_disc_swap', r'^    psx_disc_swap disc_swap \('),
            ('psx_disc_select', r"^    psx_disc_select #\(\.DISC_SLOT\(16'[dh][0-9a-fA-F]+\)\) disc_select \(")):
        declarations = re.findall(r'^\s*' + module + r'\b', text, re.M)
        found = list(re.finditer(pattern, text, re.M))
        if not declarations:
            continue
        if len(declarations) != 1 or len(found) != 1:
            raise ValueError('Unknown disc-swap reset consumer: ' + module)
        at = found[0].start()
        instance = text[at:text.index(');', at) + 2]
        if instance.count('.clk(clk1x)') != 1 or instance.count('.reset(cd_stream_reset)') != 1:
            raise ValueError('Disc-swap and loader transport clock/reset domains differ')
        optional.append(instance)
    if len(optional) not in (0, 2):
        raise ValueError('Expected the complete disc-swap reset consumer pair')
    changes = [*TOP_PATCHES[:2],
               (cd, cd.replace('.host_reset(cd_stream_reset)', '.host_reset(cd_host_reset)').replace(
                   '.reset(cd_stream_reset)', '.reset(cd_core_reset)')),
               *((p, p.replace('.reset(cd_stream_reset)', '.reset(cd_core_reset)')) for p in optional)]
    changed = patches(text, changes)
    if re.search(r'\bcd_stream_reset\b', changed):
        raise ValueError('Unrecognized consumer of the raw CD transport reset')
    return changed


def adapt_boot(text):
    checked(text, BOOT_SHA)
    anchor = '    (* async_reg="true", preserve, dont_merge *) reg freeze_meta=0, freeze_sync=0;'
    return patches(text, [
        (anchor, BOOT_PIPES + anchor),
        ('always @(posedge cpu_clk or posedge reset_async) begin\n        if(reset_async)',
         'always @(posedge cpu_clk or posedge cpu_transport_reset) begin\n        if(cpu_transport_reset)'),
        ('always @(posedge host_clk or posedge reset_async) begin\n        if(reset_async)',
         'always @(posedge host_clk or posedge host_transport_reset) begin\n        if(host_transport_reset)'),
    ])


def adapt_cdc(text):
    if hashlib.sha256(text.encode()).hexdigest() == CDC_EARLY_SHA:
        # build_pocket already selects local state resets. Accept only that
        # exact reviewed adaptation; collapse its unreachable duplicate arms.
        for domain in ('src', 'dst'):
            text = patches(text, [(
                f'    always @(posedge {domain}_clk or posedge {domain}_reset) begin\n        if ({domain}_reset) begin',
                f'    always @(posedge {domain}_clk or posedge reset_async) begin\n        if (reset_async) begin')])
    checked(text, CDC_SHA)
    changes = [('(* async_reg = "true" *) reg [2:0] src_reset_pipe, dst_reset_pipe;',
                '(* async_reg = "true", preserve, dont_merge *) reg [2:0] src_reset_pipe, dst_reset_pipe;')]
    for domain in ('src', 'dst'):
        # Check that the original asynchronous and local-reset bodies agree.
        start = f'    always @(posedge {domain}_clk or posedge reset_async) begin\n'
        at = text.index(start) + len(start)
        before_local = f'        end else if ({domain}_reset) begin\n'
        end = text.index('        end else begin\n', at)
        raw_body, local_body = text[at:end].split(before_local)
        raw_body = raw_body.removeprefix('        if (reset_async) begin\n')
        if raw_body != local_body:
            raise ValueError('Memory CDC reset bodies differ')
        before = start + text[at:end]
        after = (f'    always @(posedge {domain}_clk or posedge {domain}_reset) begin\n'
                 f'        if ({domain}_reset) begin\n' + local_body)
        changes.append((before, after))
    return patches(text, changes)


def prepare_project(directory, expected_top_sha256):
    directory = Path(directory).resolve()
    if (not directory.is_relative_to(ROOT / 'build') or
            subprocess.run(['git', 'check-ignore', '--quiet', str(directory / 'reset-release/manifest.json')],
                           cwd=ROOT).returncode):
        raise ValueError('Use a fresh ignored build project')
    if any((directory / n).exists() for n in ('db', 'incremental_db', 'output_files', 'reset-release')):
        raise ValueError('Reset release must be prepared before map/fit')
    core, qsf, sdc = (directory / n for n in ('core_top.sv', 'apf_top.qsf', 'pocket.sdc'))
    q, constraint = qsf.read_text(), sdc.read_text()
    assigned = assignments(directory, q)
    def source(name):
        found = [p for k, p in assigned if k == 'SYSTEMVERILOG_FILE' and p.name == name]
        if len(found) != 1:
            raise ValueError('Expected one assigned ' + name)
        return found[0]
    if source('core_top.sv') != core or digest(core) != expected_top_sha256:
        raise ValueError('Canonical top identity differs')
    boot, cdc = source('psx_boot_gate.sv'), source('psx_mem_cdc.sv')
    original = {str(p): digest(p) for p in (core, qsf, sdc, boot, cdc)}
    new_top = adapt_top(core.read_text())
    adapted = [(boot, adapt_boot(boot.read_text())), (cdc, adapt_cdc(cdc.read_text()))]
    if constraint.count(POWER_CUT) != 1:
        raise ValueError('Expected exactly the inherited broad power-reset cut')
    new_sdc = constraint.replace(POWER_CUT, '', 1)
    work = directory / 'reset-release'
    # Complete source/assignment validation before mutating any project file.
    for old, _ in adapted:
        count = 0
        for line in q.splitlines(keepends=True):
            if assignments(directory, line) == [('SYSTEMVERILOG_FILE', old)]:
                if str(old) in line:
                    replacement = line.replace(str(old), str(work / old.name))
                elif old.is_relative_to(directory) and str(old.relative_to(directory)) in line:
                    replacement = line.replace(str(old.relative_to(directory)), str(work / old.name))
                else:
                    raise ValueError('Unsupported reset-release source assignment')
                q = q.replace(line, replacement, 1)
                count += 1
        if count != 1:
            raise ValueError('Ambiguous reset-release source assignment')
    if not all(digest(Path(p)) == sha for p, sha in original.items()):
        raise ValueError('Project changed while checking reset release')
    work.mkdir()
    for old, text in adapted:
        (work / old.name).write_text(text)
    scope = work / 'pocket_reset_release_scope.sdc'
    shutil.copyfile(ROOT / 'scripts/pocket_reset_release_scope.tcl', scope)
    core.write_text(new_top)
    sdc.write_text(new_sdc)
    qsf.write_text(q + f'\nset_global_assignment -name SDC_FILE "{scope}"\n')
    result = dict(original_inputs=original, top_sha256=digest(core),
                  added=[dict(path=str(p), sha256=digest(p)) for p in sorted(work.iterdir())],
                  preparer_sha256=digest(Path(__file__)),
                  removed_exception=POWER_CUT.strip(),
                  local_reset_release_edges=3, guest_reset_contract_changed=False,
                  CD_reuses_loader_domain_resets=True, duplicate_CD_reset_flops_avoided=6,
                  disc_swap_controllers_use_domain_reset='    psx_disc_swap disc_swap (' in new_top,
                  configuration_reset_unchanged=True, warm_reset_does_not_reset_transport=True,
                  active_transaction_protocol_unchanged=True,
                  fit_verified=False, timing_verified=False, hardware_verified=False,
                  production_selected=False)
    (work / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--expected-top-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_project(args.project, args.expected_top_sha256), indent=2))
