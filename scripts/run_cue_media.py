#!/usr/bin/env python3
"""Check private raw CUE media through the real SERV mount firmware.

Only boundary-sector samples are copied into ignored output. No game CPU,
cd_top, psx_cd_stream, real APF latency or hardware is exercised here.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess

from make_pocket_instances import SECTOR, one_file_plan, parse_cue, track_table
from run_cue_mount import COMMON, SERV_FILES, words_hex
from tool_paths import ROOT, find_tool


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda: f.read(1 << 20), b''): h.update(data)
    return h.hexdigest()


def prepare(cue, output, mode):
    parsed = parse_cue(cue)
    plan = one_file_plan(parsed, cue.parent)
    if isinstance(plan, str): raise ValueError(plan)
    sources, tracks = plan
    # This runner deliberately rejects external gaps/lead-in; extend with an
    # independent segmented oracle before claiming those private layouts.
    sectors = sum(n for _, n in sources)
    if len(sources) > 99: raise ValueError('more than 99 files')
    files = [p.resolve() for p, _ in sources] + [cue]
    if len(set(files)) != len(files): raise ValueError('repeated FILE unsupported by this runner')
    paths = [COMMON + cue.parent.name + '/' + p.name for p in files]
    if mode=='json-trk':
        table_path=output/'Game.trk'
        table_path.write_bytes(b''.join(w.to_bytes(4,'little') for w in track_table(tracks,sectors,region=0)))
        files.append(table_path); paths.append(COMMON + cue.parent.name + '/Game.trk')
    json_path = output/'Game.json'
    json_path.write_text('{"instance": {}}\n')
    if mode.startswith('json'):
        files.append(json_path); paths.append(COMMON + cue.parent.name + '/Game.json')
    if any(len(p.encode()) >= 256 for p in paths): raise ValueError('APF path too long')
    if any(len(str(p).encode()) >= 1024 for p in files): raise ValueError('local path too long')
    (output/'real_paths.hex').write_text(''.join(str(p).encode().hex().rjust(2048, '0')+'\n' for p in files))
    (output/'paths.hex').write_text(''.join(words_hex(p.encode()+bytes(256-len(p.encode()))) for p in paths))
    (output/'meta.hex').write_text(''.join(f'00000000\n{p.stat().st_size:08x}\n' for p in files))
    (output/'table.hex').write_text(''.join(f'{w:08x}\n' for w in track_table(tracks,sectors,region=0)))
    boundaries = {0, sectors-1}
    at = 0
    for _, n in sources:
        at += n
        boundaries.update((at-1, at))
    for i0, i1 in tracks:
        boundaries.update((i0-1, i0, i1-1, i1))
    boundaries = sorted(b for b in boundaries if 0 <= b < sectors)
    # Alternating high/low requests forces reopening rather than only forward access.
    bounds = boundaries[::2] + boundaries[1::2][::-1]
    if len(bounds) > 256: raise ValueError('more than 256 boundary samples')
    samples = bytearray()
    for b in bounds:
        offset = b
        for p,n in sources:
            if offset < n:
                with p.open('rb') as f:
                    f.seek(offset*SECTOR); data=f.read(SECTOR)
                if len(data)!=SECTOR: raise ValueError('short oracle read')
                samples.extend(data); break
            offset -= n
        else: raise ValueError('oracle sector unmapped')
    (output/'image.hex').write_text(''.join(f'{v:02x}\n' for v in samples))
    (output/'bounds.hex').write_text(''.join(f'{b:08x}\n' for b in bounds+[0xffffffff]*(256-len(bounds))))
    picked = len(files)-1
    extra = []
    if mode != 'pick': extra.append('+launch')
    if mode.startswith('json'):
        extra.append(f'+cue100={len(sources)}')
        if len(sources)==1: extra.append('+slot101=0')
        if mode=='json-trk': extra += [f'+trk102={len(sources)+1}','+json']
    if mode in ('json-no-path','json-trk') and len(sources)!=1:
        raise ValueError('no-path/table modes require one merged BIN')
    if mode=='json-no-path':
        if len(sources)!=1: raise ValueError('no-path mode requires one merged BIN')
        extra.append('+nogetfile')
    return dict(cue=str(cue), cue_sha256=sha(cue), mode=mode,
                files=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for p,_ in sources],
                tracks=len(tracks), sectors=sectors, boundary_sectors=bounds,
                oracle_samples_sha256=hashlib.sha256(samples).hexdigest(),
                args=[f'+base={output}',f'+nfiles={len(files)}',f'+picked={picked}',
                      f'+size={sectors*SECTOR}',f'+sectors={sectors}','+real_files',*extra])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cue',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mode',choices=('pick','launch','json','json-no-path','json-trk'),default='launch')
    parser.add_argument('--timeout',type=int,default=1800)
    parser.add_argument('--simulator',choices=('iverilog','verilator'),default='iverilog')
    parser.add_argument('--fault-offset',action='store_true',help='Plant a one-sector offset fault in a private RTL copy; must fail')
    parser.add_argument('--test4-project',type=Path,help='Validate and use the published test4 mount RTL/firmware identities')
    args=parser.parse_args()
    output=args.output.resolve(); output.mkdir(parents=True,exist_ok=False)
    report=prepare(args.cue.resolve(),output,args.mode)
    mount=ROOT/'rtl/pocket/psx_cue_mount.sv'
    fw=ROOT/'rtl/pocket/psx_cue_fw'
    lc=0
    if args.test4_project:
        project=args.test4_project.resolve()
        qsf=next(project.glob('*.qsf')).read_text()
        mount=Path(re.search(r'-name SYSTEMVERILOG_FILE "([^"]*/psx_cue_mount.sv)"',qsf)[1])
        fw=project/'psx_cue_fw'
        expected={row.split('\t')[2]:row.split('\t')[1] for row in (ROOT/'docs/evidence/candidate-055-test4-inputs.tsv').read_text().splitlines() if len(row.split('\t'))==3}
        protected={'repo/rtl/pocket/psx_cue_mount.sv':mount}
        protected.update({f'project/psx_cue_fw{k}.hex':Path(str(fw)+f'{k}.hex') for k in range(4)})
        for key,path in protected.items():
            if sha(path)!=expected[key]: raise ValueError(f'test4 identity mismatch: {key}')
        for source in SERV_FILES(ROOT/'references/serv/rtl'):
            key='repo/'+str(source.relative_to(ROOT))
            if sha(source)!=expected[key]: raise ValueError(f'test4 SERV identity mismatch: {key}')
        report['test4_mount_inputs_verified']=True
        lc=1
    sources=[*SERV_FILES(ROOT/'references/serv/rtl'),mount, ROOT/'sim/pocket/tb_cue_mount.sv']
    firmware=[Path(str(fw)+f'{k}.hex') for k in range(4)]
    report['source_sha256']={str(p):sha(p) for p in sources+firmware}
    report['libcrypt']=lc
    (output/'inputs.json').write_text(json.dumps(report,indent=2)+'\n')
    hdl=[str(p) for p in sources if p.suffix in ('.v','.sv')]
    if args.fault_offset:
        text=mount.read_text(); before='adr[2] ? u_offset :'
        if text.count(before)!=1: raise ValueError('offset fault anchor changed')
        mutant=output/'psx_cue_mount_offset_fault.sv'
        mutant.write_text(text.replace(before,"adr[2] ? (u_id==16'd101 ? u_offset+32'd2352 : u_offset) :"))
        hdl[hdl.index(str(mount))]=str(mutant)
        report['fault_source_sha256']=sha(mutant)
    report['simulator']=args.simulator
    with (output/'build.log').open('w') as log:
        if args.simulator=='iverilog':
            command=[find_tool('iverilog'),'-g2012','-o',str(output/'tb.vvp'),
                     f'-Ptb_cue_mount.FW="{fw}"',f'-Ptb_cue_mount.LC={lc}',
                     '-Ptb_cue_mount.BOUND_COUNT=256','-Ptb_cue_mount.MOUNT_LIMIT=200000000','-Ptb_cue_mount.STREAM_LIMIT=20000000',*hdl]
            executable=[find_tool('vvp'),str(output/'tb.vvp')]
        else:
            command=[find_tool('verilator'),'--binary','--timing','-Wno-fatal','-j','2',
                     '--top-module','tb_cue_mount','--Mdir',str(output/'obj'),
                     f'-GFW="{fw}"',f'-GLC={lc}','-GBOUND_COUNT=256','-GMOUNT_LIMIT=200000000','-GSTREAM_LIMIT=20000000',*hdl]
            executable=[str(output/'obj/Vtb_cue_mount')]
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
    result=subprocess.run([*executable,*report.pop('args')],
                          capture_output=True,text=True,timeout=args.timeout)
    report['executable_sha256']=sha(Path(executable[-1]))
    text=result.stdout+result.stderr; (output/'run.log').write_text(text)
    cost=re.search(r'mount_cost: cycles=(\d+) reads=(\d+) opens=(\d+) commands=(\d+)',text)
    if cost: report['mount_cost']=dict(zip(('cycles_100mhz_model','read_commands','open_commands','all_commands'),map(int,cost.groups())))
    report.update(passed=result.returncode==0 and 'PASS' in text and 'FAIL' not in text,
                  scope=('SERV precomputed-table bypass only; downstream stream not exercised' if args.mode=='json-trk' else 'SERV CUE mount and virtual slot reads; stream stand-in; no game execution'),
                  hardware_verified=False)
    report['source_identity_verified']=all(sha(Path(p))==digest for p,digest in report['source_sha256'].items())
    report['passed']=report['passed'] and report['source_identity_verified']
    if args.fault_offset:
        report['fault_rejected']=report['source_identity_verified'] and not report['passed'] and 'FAIL: sector data' in text
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(text[-3000:]); print(f'report: {output/"report.json"}')
    if not report.get('fault_rejected',report['passed']): raise SystemExit(1)


if __name__=='__main__': main()
