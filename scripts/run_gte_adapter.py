#!/usr/bin/env python3
"""Check clock-enable/read-port adaptation against original GTE and math probes."""
import json
import shutil
import subprocess

from prepare_gte_adapter import adapter_sources, UPSTREAM, PIN
from prepare_resource_targets import digest
from reference_gte_vectors import write_vectors
from tool_paths import ROOT


def main(name='gte-adapter', transform=None, cflags=()):
    verilator=shutil.which('verilator')
    if not verilator:
        local=ROOT/'build/tools/oss-cad-suite/bin/verilator'
        if local.is_file(): verilator=str(local)
    if not verilator: raise SystemExit('Verilator is required for the GTE adapter probes')
    directory=ROOT/'build/sim'/name; directory.mkdir(parents=True,exist_ok=True)
    adapted=adapter_sources(directory/'adapted')
    if transform: transform(adapted)
    originals=sorted(path for path in (UPSTREAM/'rtl').iterdir() if path.suffix in ('.v','.sv'))
    sources=[str(path) for path in originals]+[source['path'] for source in adapted
        if source['assignment']=='SYSTEMVERILOG_FILE' and (source.get('original') or source['path'].endswith('adapter.sv'))]
    top=ROOT/'sim/reference/gte_adapter_top.sv'; harness=ROOT/'sim/reference/gte_adapter_probe.cpp'
    vectors=directory/'gte-vectors.txt'; counts=write_vectors(vectors)
    obj=directory/'obj_dir'; log=directory/'checks.log'
    command=[verilator,'--cc','--exe','--build','-Wno-fatal','-j','4','--top-module','gte_adapter_top',
        '-I'+str(UPSTREAM/'rtl'),*[a for f in cflags for a in ('-CFLAGS',f)],'--Mdir',str(obj),*sources,str(top),str(harness)]
    with log.open('w') as output:
        result=subprocess.run(command,cwd=directory,stdout=output,stderr=subprocess.STDOUT,timeout=300)
    if result.returncode: raise SystemExit(f'GTE adapter compilation failed; see {log}')
    results=[]
    for seed in (1,7,0x475445):
        result=subprocess.run([str(obj/'Vgte_adapter_top'),str(vectors),str(seed)],cwd=directory,
                              capture_output=True,text=True,timeout=60)
        with log.open('a') as output: output.write(result.stdout+result.stderr)
        print(result.stdout+result.stderr,end='',flush=True)
        passed=result.returncode==0 and f'operations={counts[0]} commands={counts[1]} checks={counts[2]}' in result.stdout
        results.append({'seed':seed,'exit_code':result.returncode,'passed':passed,'stdout':result.stdout,'stderr':result.stderr})
    report={'upstream_pin':PIN,'tool_version':subprocess.check_output([verilator,'--version'],text=True).strip(),
        'source_manifest':adapted,'reference_sources':[{'path':str(p),'sha256':digest(p)} for p in originals],
        'top_sha256':digest(top),'harness_sha256':digest(harness),'vector_sha256':digest(vectors),
        'expected_counts':counts,'runs':results,'probe_passed':all(r['passed'] for r in results),
        'full_gte_correctness_verified':False,'mister_busy_latency_equivalence_verified':False,
        'cpu_execution_verified':False,'hardware_verified':False}
    (directory/'probe-report.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['probe_passed']: raise SystemExit('GTE adapter probes failed')


if __name__=='__main__': main()
