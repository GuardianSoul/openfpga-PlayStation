#!/usr/bin/env python3
"""Run the existing bounded mathematical GTE probes against the Laxer candidate."""
import json
import shutil
import subprocess

from prepare_laxer_gte_target import prepare_laxer_gte
from prepare_resource_targets import digest
from reference_gte_vectors import write_vectors
from tool_paths import ROOT


def main():
    verilator=shutil.which('verilator')
    if not verilator:
        local=ROOT/'build/tools/oss-cad-suite/bin/verilator'
        if local.is_file(): verilator=str(local)
    if not verilator: raise SystemExit('Verilator is required for the alternative GTE probes')
    directory=ROOT/'build/sim/laxer-gte'; directory.mkdir(parents=True,exist_ok=True)
    target=prepare_laxer_gte(directory)['targets']['laxer_gte']
    sources=[source['path'] for source in target['quartus_sources'] if source['assignment']=='SYSTEMVERILOG_FILE']
    include=ROOT/'references/PS-FPGA/rtl/gte/rtl'
    vectors=directory/'gte-vectors.txt'; counts=write_vectors(vectors)
    obj=directory/'obj_dir'; log=directory/'checks.log'
    command=[verilator,'--cc','--exe','--build','-Wno-fatal','-j','4',
        '--top-module',target['top'],'-I'+str(include),'--Mdir',str(obj),
        *sources,str(ROOT/'sim/reference/laxer_gte_probe.cpp')]
    with log.open('w') as output:
        result=subprocess.run(command,cwd=directory,stdout=output,stderr=subprocess.STDOUT,timeout=300)
    if result.returncode: raise SystemExit(f'Alternative GTE compilation failed; see {log}')
    result=subprocess.run([str(obj/('V'+target['top'])),str(vectors)],cwd=directory,
                          capture_output=True,text=True,timeout=60)
    with log.open('a') as output: output.write(result.stdout+result.stderr)
    expected=f'operations={counts[0]} commands={counts[1]} checks={counts[2]}'
    passed=result.returncode==0 and expected in result.stdout and result.stdout.startswith('PASS Laxer GTE')
    report={'tool_version':subprocess.check_output([verilator,'--version'],text=True).strip(),
        'upstream_pin':'289bcf316966d75dfc60f34d347451c492e02365',
        'source_manifest':target['quartus_sources'],
        'harness_sha256':digest(ROOT/'sim/reference/laxer_gte_probe.cpp'),
        'vectors_sha256':digest(vectors),'expected_counts':counts,'exit_code':result.returncode,
        'stdout':result.stdout,'stderr':result.stderr,'probe_passed':passed,'cpu_integration_verified':False,
        'full_gte_verified':False,'hardware_verified':False}
    (directory/'probe-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(result.stdout+result.stderr,end='')
    if expected not in result.stdout: raise SystemExit('Alternative GTE probe counts incomplete')
    raise SystemExit(result.returncode)


if __name__=='__main__': main()
