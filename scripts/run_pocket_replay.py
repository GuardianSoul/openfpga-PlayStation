#!/usr/bin/env python3
"""Check candidate checkpoint continuation against its uninterrupted diagnostic."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from prepare_resource_targets import digest
from tool_paths import ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--build-dir',type=Path,required=True)
    args=parser.parse_args()
    baseline=args.baseline.resolve();build=args.build_dir.resolve()
    report=json.loads((baseline/'report.json').read_text())
    sidecar=json.loads((baseline/'checkpoint.json').read_text())
    if not report['diagnostic_passed'] or not report['source_and_fixed_input_identity_verified']:
        raise ValueError('Uninterrupted candidate diagnostic is not verified')
    start=sidecar['segment']['end_cycle']
    output=ROOT/'build/sim/pocket-replay'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True)
    command=['python3',str(ROOT/'scripts/run_pocket_checkpoint.py'),'--reuse',
        '--build-dir',str(build),'--output',str(output),'--max-cycles','3000000',
        '--capture-interval','100000','--restore',str(baseline/'checkpoint.bin')]
    with (output/'launch.log').open('w') as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180)
    resumed=json.loads((output/'report.json').read_text())
    if not resumed['diagnostic_passed'] or not resumed['source_and_fixed_input_identity_verified']:
        raise ValueError('Resumed candidate diagnostic failed')
    if resumed['segment']['start_cycle']!=start:
        raise ValueError('Resume began at a different cycle')
    cut=sidecar['segment']['audio_frames']*4
    audio_equal=(baseline/'audio.pcm').read_bytes()[cut:]==(output/'audio.pcm').read_bytes()
    trace_tail=''.join(line for line in (baseline/'execution.csv').read_text().splitlines(keepends=True)
        # The driver logs before the next rising edge. After a falling-edge
        # save, the first resumed sample still carries the saved cycle label.
        if int(line.split(',')[0])>=start)
    trace_equal=trace_tail==(output/'execution.csv').read_text()
    last_vram=sorted(baseline.glob('vram-*.hex32'))[-1].name
    outputs={name:digest(baseline/name)==digest(output/name)
        for name in ('main-ram.hex32','spu-ram.hex32',last_vram,'final-state.bin')}
    passed=audio_equal and trace_equal and all(outputs.values())
    result={'passed':passed,'cut_cycle':start,'end_cycle':resumed['segment']['end_cycle'],
        'independent_diagnostic_results':resumed['segment']['checks'],
        'audio_tail_equal':audio_equal,'sampled_cpu_trace_tail_equal':trace_equal,
        'outputs_equal':outputs,'complete_state_sha256':digest(output/'final-state.bin'),
        'source_identity_verified':True,'game_checkpoint_verified':False,'hardware_verified':False}
    (output/'replay-report.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2));print(output)
    if not passed:raise SystemExit('Candidate replay differs; preserve the first mismatch')


if __name__=='__main__':main()
