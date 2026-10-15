#!/usr/bin/env python3
"""Measure cached/scalar-buffer SPU area in private copies of a captured top.

No bitstream is produced. The captured project's other features, omissions,
clocks and constraints are retained; this is not release qualification.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import tempfile

from prepare_resource_targets import digest
from prepare_spu_scalar_dual_buffer import prepare
from run_resource_fit import fit_target
from tool_paths import ROOT, find_tool


FILE_ASSIGNMENT = re.compile(
    r'^set_global_assignment -name (VERILOG_FILE|SYSTEMVERILOG_FILE|VHDL_FILE|MISC_FILE|SDC_FILE|MIF_FILE) '
    r'(?:"([^"]+)"|(\S+))', re.M)


def stream_top(text):
    """Only the scalar SPU word port changes in this private captured copy."""
    from prepare_spu_scalar_bram_buffer import replace
    text=replace(text,'    psx_spu_sdram_adapter spu_commands (',
        '    wire scalar_fill_valid;wire [1:0] scalar_fill_word;wire [31:0] scalar_fill_data;\n'
        '    psx_spu_sdram_adapter spu_commands (')
    text=replace(text,'.p_rsp_rdata(packet_read[1])',
        '.p_rsp_rdata(packet_read[1]),.fill_valid(scalar_fill_valid),.fill_word(scalar_fill_word),.fill_data(scalar_fill_data)')
    pattern=r'        psx_mem_bridge #\(\.PIPELINE_READS\(1\)\) front \(.*?\);'
    matches=list(re.finditer(pattern,text,re.S))
    if len(matches)!=1:raise ValueError('Expected one packet frontend generate body')
    generic=matches[0][0]
    direct=generic.replace('psx_mem_bridge #(.PIPELINE_READS(1))','psx_spu_scalar_bridge')
    direct=replace(direct,'.rsp_error(packet_error[client])',
        '.rsp_error(packet_error[client]),.fill_valid(scalar_fill_valid),.fill_word(scalar_fill_word),.fill_data(scalar_fill_data)')
    return replace(text,generic,'        if(client==1) begin:scalar_stream\n'+direct+
        '\n        end else begin:generic_packet\n'+generic+'\n        end')


def inputs(directory):
    result = {}
    for m in FILE_ASSIGNMENT.finditer((directory/'apf_top.qsf').read_text()):
        path = Path(m[2] or m[3])
        if not path.is_absolute(): path = directory/path
        path = path.resolve()
        if not path.is_file(): raise ValueError('Missing compile input: '+str(path))
        result[str(path)] = dict(path=str(path), sha256=digest(path), assignment=m[1], library='work')
    # Retain ROMs named inside HDL too, including the audio coefficient image.
    for source in list(result.values()):
        if source['assignment'] in ('VERILOG_FILE', 'SYSTEMVERILOG_FILE', 'VHDL_FILE'):
            text = Path(source['path']).read_text()
            for name in re.findall(r'"([^"\n]+\.(?:hex18|hex|mif))"', text):
                path = Path(name)
                if not path.is_absolute(): path = directory/path
                path = path.resolve()
                if not path.is_file(): raise ValueError('Missing ROM input: '+str(path))
                result[str(path)] = dict(path=str(path), sha256=digest(path), assignment='ROM', library='work')
    for name in ('apf_top.qsf', 'apf_top.qpf', 'pocket.sdc'):
        p = directory/name
        result[str(p)] = dict(path=str(p), sha256=digest(p), assignment='PROJECT', library='work')
    return list(result.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('captured_run', type=Path)
    parser.add_argument('--timeout', type=int, default=1800)
    parser.add_argument('--stream', action='store_true',help='Compare a captured scalar dual-clock top against the direct-fill endpoint')
    args = parser.parse_args()
    captured = args.captured_run.resolve()
    parent = ROOT/'build/resources/spu-scalar-full-top/runs'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=parent))
    print(work, flush=True)
    original_inputs = inputs(captured)
    protected = {s['path']: s['sha256'] for s in original_inputs}
    for name in ('measure_spu_scalar_full_top.py', 'prepare_spu_scalar_dual_buffer.py',
                 'prepare_spu_scalar_bram_buffer.py', 'prepare_spu_scalar_buffer.py'):
        p = ROOT/'scripts'/name; protected[str(p)] = digest(p)
    if args.stream:
        p=ROOT/'scripts/prepare_spu_scalar_stream_buffer.py';protected[str(p)]=digest(p)
    archives = {}
    def archive(paths):
        for name in paths:
            p = Path(name).resolve(); h = digest(p)
            if str(p) in archives:
                if archives[str(p)]['sha256'] != h: raise ValueError('Fit input changed between builds')
                continue
            copy = work/'input-archive'/str(len(archives))/p.name
            copy.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(p, copy)
            if digest(copy) != h: raise ValueError('Fit input changed during archive')
            archives[str(p)] = dict(path=str(copy), sha256=h)
    archive(protected)
    report = dict(captured_run=str(captured), source_sha256=protected, input_archives=archives,
                  results={}, production_selected=False, board_timing_verified=False,
                  hardware_verified=False, release_qualified=False,
                  scope='Captured physical top, identical settings except SPUSDRAM selection and private scalar adapter; other omissions retained')
    names=('scalar-M10K-dual','scalar-M10K-stream') if args.stream else ('cached','scalar-M10K-dual')
    for name in names:
        directory = work/name
        shutil.copytree(captured, directory, ignore=shutil.ignore_patterns(
            'db', 'incremental_db', 'output_files', 'input-archive', '*.rpt', '*.summary', '*.log'))
        # Rewrite all copied text inputs (absolute project/ROM paths included).
        for p in directory.rglob('*'):
            if p.is_file() and p.suffix in ('.sv', '.v', '.vhd', '.qsf', '.qpf', '.sdc'):
                text = p.read_text(); changed = text.replace(str(captured), str(directory))
                if changed != text: p.write_text(changed)
        qsf = directory/'apf_top.qsf'; text = qsf.read_text()
        selection = None
        if name != names[0]:
            wrappers = [s for s in inputs(directory) if Path(s['path']).name == 'psx_mister_psx_top_resource.vhd']
            if len(wrappers) != 1: raise ValueError('Expected one connected PSX wrapper')
            wrapper = Path(wrappers[0]['path'])
            if not wrapper.is_relative_to(directory): raise ValueError('Wrapper is outside private copy')
            w = wrapper.read_text()
            expected="SPUSDRAM => '1'," if args.stream else "SPUSDRAM => '0',"
            if w.count(expected) != 1: raise ValueError('Unexpected baseline SPU memory mode')
            if args.stream:
                from prepare_spu_scalar_stream_buffer import prepare as prepare_stream
                selection=prepare_stream(directory/'direct-fill')
                core=directory/'core_top.sv';core.write_text(stream_top(core.read_text()))
                adapters=[s for s in inputs(directory) if Path(s['path']).name=='psx_spu_sdram_adapter.sv']
                if len(adapters)!=1:raise ValueError('Expected one scalar buffer source')
                old=adapters[0]['path']
                text += '\nset_global_assignment -name SYSTEMVERILOG_FILE "'+selection['bridge_path']+'" -library work\n'
            else:
                wrapper.write_text(w.replace("SPUSDRAM => '0',", "SPUSDRAM => '1',"))
                selection = prepare(directory/'scalar-buffer')
                old = str(ROOT/'rtl/pocket/psx_spu_sdram_adapter.sv')
            if text.count('"'+old+'"') != 1: raise ValueError('Expected one scalar frontend assignment')
            qsf.write_text(text.replace('"'+old+'"', '"'+selection['path']+'"'))
        sources = inputs(directory); archive(s['path'] for s in sources)
        target = dict(top='apf_top', directory=str(directory), quartus_sources=sources,
                      project_sha256={'.qsf': digest(qsf), '.qpf': digest(directory/'apf_top.qpf')},
                      fixed_ports={}, physical_clock_ports=['clk_74a', 'clk_74b'])
        (directory/'area-inputs.json').write_text(json.dumps(dict(target=target, selection=selection), indent=2)+'\n')
        result = fit_target(Path(find_tool('quartus_sh')).parent, target, '5CEBA4F23C8', args.timeout)
        fit_report = directory/'output_files/apf_top.fit.rpt'
        if fit_report.is_file():
            body = fit_report.read_text(errors='replace')
            required = re.search(r'Fitter requires (\d+) LABs.*only (\d+) LABs', body)
            used = re.search(r'Total LABs:\s+partially or completely used\s*;\s*([\d,]+) /', body)
            result['required_LABs'] = int(required[1]) if required else int(used[1].replace(',', '')) if used else None
            result['fit_report'] = dict(path=str(fit_report), sha256=digest(fit_report))
        capacity_failure = len(result['steps']) == 2 and result['steps'][0] == dict(tool='quartus_map', exit_code=0) and (
            result['steps'][1]['tool'] == 'quartus_fit' and result['steps'][1]['exit_code'] not in (None, 0)
            and (result.get('required_LABs') or 0) > 1848
            and any('Error (170012)' in e for e in result['tool_error_messages'])
            and all(e.startswith(('Error (170012)', 'Error (11802)', 'Error:')) for e in result['tool_error_messages']))
        result['measurement_valid'] = result['source_identity_verified'] and result['project_identity_verified'] and (
            result['fit_verified'] or capacity_failure)
        report['results'][name] = result
        report['source_identity_verified'] = all(digest(Path(p)) == h for p, h in protected.items())
        report['passed'] = report['source_identity_verified'] and len(report['results']) == 2 and all(
            r['measurement_valid'] for r in report['results'].values())
        (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        print(name, 'fit=', result['fit_verified'], 'LABs=', result.get('required_LABs'),
              'measurement_valid=', result['measurement_valid'], flush=True)
        if not result['measurement_valid']: break
    print(work/'report.json', flush=True)
    if not report['passed']: raise SystemExit(1)


if __name__ == '__main__': main()
