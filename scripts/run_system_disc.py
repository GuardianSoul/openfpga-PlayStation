#!/usr/bin/env python3
"""Execute connected PSX BIOS/disc RTL with local sector delivery and media capture."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

from prepare_system_verilator import prepare
from prepare_resource_targets import digest
from psx_disc_host import DiscHost
from system_disc_bench import write_bench
from tool_paths import ROOT,find_tool


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bios',type=Path,required=True)
    parser.add_argument('--cue',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'build/sim/system-disc')
    parser.add_argument('--max-cycles',type=int,default=160000000)
    parser.add_argument('--timeout',type=int,default=2400)
    parser.add_argument('--fastboot',action='store_true',help='Enable upstream read-time BIOS logo skip; original file retained')
    parser.add_argument('--reuse',action='store_true')
    parser.add_argument('--trace-devices',action='store_true',help='Record CD/DMA/IRQ activity and final main RAM locally')
    parser.add_argument('--expected',type=Path,help='2648-word table for an authored reset diagnostic; otherwise run BIOS mode')
    parser.add_argument('--capture-interval',type=int,default=0,help='Periodically capture primary VRAM and display metadata (cycles)')
    parser.add_argument('--apf-loader',action='store_true',help='Serve sectors through the Pocket SD loader (psx_cd_stream) and pinned APF handler')
    parser.add_argument('--pad-file',type=Path,help='Mutable active-high digital pad mask in a local hexadecimal text file')
    args=parser.parse_args()
    if args.max_cycles<=0 or args.max_cycles>0x7fffffff or args.timeout<=0 or not 0<=args.capture_interval<=0x7fffffff:
        parser.error('Execution bounds must be positive 32-bit values and capture interval nonnegative')
    bios=args.bios.read_bytes()
    if len(bios)!=524288:parser.error('BIOS must be exactly 512 KiB')
    directory=args.output.resolve();directory.mkdir(parents=True,exist_ok=True)
    if directory.is_relative_to(ROOT) and subprocess.run(['git','check-ignore','--quiet',str(directory/'boot.hex8')],cwd=ROOT).returncode:
        parser.error('Private BIOS/discs/media must use a Git-ignored directory')
    with DiscHost(args.cue) as host:disc=host.prepare(directory)
    if (disc['index_sector']+disc['sectors'])*2352>0x7fffffff:parser.error('Current SV file API supports offsets below 2 GiB')
    ghdl=find_tool('ghdl');verilator=find_tool('verilator') or str(ROOT/'build/tools/oss-cad-suite/bin/verilator')
    if not ghdl or not Path(verilator).is_file():parser.error('GHDL synthesis and Verilator required')
    models=ROOT/'sim/reference/mister_memory_models.sv';host_rtl=ROOT/'sim/reference/psx_disc_host.sv'
    from run_cd_controller import APF_HOST
    host_rtls=APF_HOST if args.apf_loader else [host_rtl]
    verilog=directory/'system.v';build_file=directory/'build-manifest.json';executable=directory/'obj/Vtb_psx_fast'
    if args.reuse:
        build=json.loads(build_file.read_text());manifest=build['preparation'];bench=Path(build['bench'])
        identity=manifest.get('fast_boot',False)==args.fastboot
        identity &= build.get('trace_devices',False)==args.trace_devices
        identity &= build.get('apf_loader',False)==args.apf_loader
        identity &= all(digest(Path(p))==h for p,h in build.get('apf_loader_sha256',{}).items())
        if (args.capture_interval or args.pad_file) and not build.get('interactive_capture',False):
            parser.error('Recorded build lacks periodic capture/pad support; rebuild required')
        for path,key in ((models,'models_sha256'),(host_rtl,'host_sha256'),(verilog,'verilog_sha256'),(bench,'bench_sha256'),(executable,'executable_sha256')):
            identity &= digest(path)==build[key]
        identity &= all(digest(Path(s['path']))==s['sha256'] and digest(Path(s['original']))==s['original_sha256'] for s in manifest['sources'])
        identity &= all('selected' not in s or digest(Path(s['selected']))==s['selected_sha256'] for s in manifest['sources'])
        if not identity:parser.error('Recorded build changed; rebuild required')
    else:
        manifest=prepare(directory/'sources',fast_reset=False,fast_boot=args.fastboot);bench=write_bench(directory,args.trace_devices)
        with (directory/'build.log').open('w') as log:
            for source in manifest['sources']:
                subprocess.run([ghdl,'-a','--std=08','-frelaxed',f'--work={source["library"]}',source['path']],cwd=directory,
                    stdout=log,stderr=subprocess.STDOUT,timeout=60,check=True)
            with verilog.open('w') as out:
                subprocess.run([ghdl,'--synth','--std=08','-frelaxed','--vendor-library=ramip','--out=verilog',
                    'psx_mister_psx_top_resource'],cwd=directory,stdout=out,stderr=log,timeout=120,check=True)
            from ghdl_verilog import normalize_generated_verilog
            manifest['verilog_normalization']=normalize_generated_verilog(verilog)
            print('Building connected BIOS/disc system.',flush=True)
            subprocess.run([verilator,'--binary','--timing','-O3','-j','4','-Wno-fatal','--output-split','20000',
                '--top-module','tb_psx_fast','--Mdir',str(directory/'obj'),str(verilog),str(models),*map(str,host_rtls),str(bench)],
                cwd=directory,stdout=log,stderr=subprocess.STDOUT,timeout=300,check=True)
        build={'preparation':manifest,'trace_devices':args.trace_devices,'apf_loader':args.apf_loader,
            'apf_loader_sha256':{str(p):digest(p) for p in host_rtls} if args.apf_loader else {},'trace_schema':2 if args.trace_devices else None,
            'interactive_capture':True,
            'models_sha256':digest(models),'host_sha256':digest(host_rtl),
            'verilog_sha256':digest(verilog),'bench':str(bench),'bench_sha256':digest(bench),'executable_sha256':digest(executable)}
        build_file.write_text(json.dumps(build,indent=2)+'\n')
    from ghdl_verilog import normalize_signed_shifts
    if normalize_signed_shifts(verilog.read_text())[1]:
        parser.error('Recorded build contains logical signed shifts; rebuild with the conversion fix')
    image=directory/'boot.hex8';image.write_text(''.join(f'{b:02x}\n' for b in bios))
    protected={str(path):digest(path) for path in (models,host_rtl,verilog,bench,executable,image,disc['metadata'],args.bios.resolve(),args.cue.resolve(),build_file)}
    for source in manifest['sources']:
        for key in ('path','original','selected'):
            if key in source:protected[source[key]]=digest(Path(source[key]))
    # Hash the actual local image without copying it into the fixture.
    protected[str(args.bios.resolve())]=hashlib.sha256(bios).hexdigest()
    if args.expected:
        values=args.expected.read_text().splitlines()
        if len(values)!=2648 or any(not 0<=int(value,16)<=0xffffffff for value in values):
            parser.error('Authored diagnostic needs exactly 2648 expected 32-bit words')
        expected=directory/'expected.hex';expected.write_bytes(args.expected.read_bytes())
        protected[str(expected)]=digest(expected)
        protected[str(args.expected.resolve())]=digest(args.expected.resolve())
    disc_image=Path(disc['image']);image_stat=disc_image.stat()
    image_hash=hashlib.sha256()
    with disc_image.open('rb') as file:
        for chunk in iter(lambda:file.read(4*1024*1024),b''):image_hash.update(chunk)
    started=time.monotonic();timed_out=False
    command=[str(executable),*(['+bios'] if args.expected is None else []),f'+max_cycles={args.max_cycles}',f'+disc_file={disc["image"]}',
        f'+disc_metadata={disc["metadata"]}',f'+disc_sectors={disc["sectors"]}',f'+disc_index={disc["index_sector"]}']
    command.append(f'+capture_interval={args.capture_interval}')
    if args.pad_file:command.append(f'+pad_file={args.pad_file.resolve()}')
    mode='authored reset diagnostic' if args.expected else 'private BIOS/disc'
    print(f'Running {mode}: cycles={args.max_cycles}, fastboot={args.fastboot}',flush=True)
    with (directory/'run.log').open('w') as log:
        try:code=subprocess.run(command,cwd=directory,stdout=log,stderr=subprocess.STDOUT,timeout=args.timeout).returncode
        except subprocess.TimeoutExpired:timed_out=True;code=None
    output=(directory/'run.log').read_text(errors='replace')
    identity=all(digest(Path(path))==sha for path,sha in protected.items())
    stat_after=Path(disc['image']).stat();identity &= (image_stat.st_size,image_stat.st_mtime_ns)==(stat_after.st_size,stat_after.st_mtime_ns)
    report={'build_manifest_sha256':digest(build_file),'protected_sha256':protected,
        'disc':{key:value for key,value in disc.items() if key!='metadata'},'disc_image_sha256':image_hash.hexdigest(),
        'bios_sha256':hashlib.sha256(bios).hexdigest(),'bios_file_modified':False,'upstream_logo_skip_enabled':args.fastboot,
        'trace_devices':args.trace_devices,'sector_host':'APF loader psx_cd_stream' if args.apf_loader else 'reference simulation host',
        'input_mode':mode,
        'capture_interval_cycles':args.capture_interval,
        'dynamic_pad_file':str(args.pad_file.resolve()) if args.pad_file else None,
        'pad_events_sha256':digest(directory/'pad-events.csv') if (directory/'pad-events.csv').exists() else None,
        'trace_schema':build.get('trace_schema',1 if args.trace_devices else None),
        'source_and_fixed_input_identity_verified':identity,
        'source_and_input_identity_verified':identity and args.pad_file is None,
        'max_cycles':args.max_cycles,'elapsed_seconds':time.monotonic()-started,
        'ghdl_version':subprocess.check_output([ghdl,'--version'],text=True),
        'verilator_version':subprocess.check_output([verilator,'--version'],text=True).strip(),
        'exit_code':code,'wall_timeout':timed_out,'stdout':output,
        'bounded_execution_completed':code==0 and identity and 'BIOS/DISC BOUNDED STOP' in output,
        'authored_diagnostic_completed':args.expected is not None and code==0 and identity and 'PASS FAST SYSTEM' in output,
        'bios_boot_verified':False,'game_verified':False,'audio_verified':False,'fmv_verified':False,'hardware_verified':False}
    (directory/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('\n'.join(output.splitlines()[-14:]),flush=True)
    if not (report['bounded_execution_completed'] or report['authored_diagnostic_completed']):raise SystemExit(f'BIOS/disc execution failed its bounded check; see {directory}/run.log')


if __name__=='__main__':main()
