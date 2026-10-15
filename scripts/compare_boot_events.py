#!/usr/bin/env python3
"""Compare private DuckStation event captures with completed RTL snapshots.

Never equates wall time with emulated time, or HPS fetches with delivered CD
sectors. A frame sample is an observation, not an exact executable-entry event.
"""
import argparse
import csv
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import struct


def rows(path):
    with path.open() as f:return list(csv.reader(f))


def rtl_gpu_events(path, through_cycle):
    """Read settled GP1 acceptance records preceding a completed snapshot.

    The bench logs before the rising-edge cycle counter update; a snapshot at
    N therefore includes GP1 records labelled less than N. Hash only those
    complete records so a live log's unrelated tail cannot alter the proof.
    """
    events, prefix = [], []
    driver_bound = -1
    with path.open() as log:
        for line in log:
            if not line.endswith('\n'): break
            bound = re.match(r'(?:PROGRESS|CHECKPOINT|STOP) cycles=(\d+)\b', line)
            if bound: driver_bound = max(driver_bound, int(bound[1]))
            event = re.fullmatch(r'EVENT GP1 cycles=(\d+) data=([0-9a-fA-F]{8})\n', line)
            if not event or int(event[1]) >= through_cycle: continue
            command = int(event[2], 16)
            prefix.append(line)
            events.append({'cycle_before_rising_edge': int(event[1]),
                'command': event[2].lower(), 'opcode': (command >> 24) & 63})
    return {'accepted_GP1_writes': events,
        'records_sha256': hashlib.sha256(''.join(prefix).encode()).hexdigest(),
        'log_flushed_through_snapshot': driver_bound >= through_cycle,
        'instrumentation_observed': bool(events)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--software',type=Path,required=True)
    p.add_argument('--rtl',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--max-cycles',type=int,required=True,
        help='Use completed snapshots at/before this cycle; bound live CD input by the last snapshot')
    args=p.parse_args(); sw=args.software; rtl=args.rtl
    frames=rows(sw/'ffvii-frame-trace.csv')
    requests=[int(r[2]) for r in rows(sw/'ffvii-cd-requests.csv')]
    snapshots=[];snapshot_hashes={}
    for meta in sorted(rtl.glob('snapshot-*.csv')):
        cycle=int(meta.stem.split('-')[1])
        if cycle>args.max_cycles:continue
        with meta.open() as f:records=list(csv.DictReader(f))
        if len(records)!=1 or int(records[0]['cycles'])!=cycle:
            raise ValueError('Incomplete snapshot: '+str(meta))
        snapshots.append(records[0]);snapshot_hashes[str(meta)]=hashlib.sha256(meta.read_bytes()).hexdigest()
    if not snapshots:raise ValueError('No completed RTL snapshots at the requested bound')
    count=int(snapshots[-1]['sectors'])
    # The HPS log can be actively appended. Only the immutable, completed
    # prefix acknowledged by the last snapshot participates in this report.
    with (rtl/'disc-requests.csv').open() as f:prefix=[next(f) for _ in range(count)]
    fetched=[int(r[1]) for r in csv.reader(prefix)]
    delivered=[int(r[2]) for r in rows(sw/'ffvii-sector-trace.csv')]
    # QueueReadSector includes seek/fetch requests. Consecutive repeated LBAs
    # may hit DuckStation's reader cache; retain both original and compressed.
    def compress(values):return [v for i,v in enumerate(values) if i==0 or values[i-1]!=v]
    a,b=compress(requests),compress(fetched)
    common=0
    while common<min(len(a),len(b)) and a[common]==b[common]:common+=1
    matcher=SequenceMatcher(None,a,b,autojunk=False)
    gp1=rows(sw/'ffvii-gp1-trace.csv'); events=[]
    for r in gp1:
        command=int(r[2],16); op=command>>24
        if op in (0,3,5,8):
            events.append({'tick':int(r[0]),'frame':int(r[1]),'command':r[2],
                'kind':{0:'GPU reset',3:'display enable/disable',5:'display origin',8:'display mode'}[op],
                'display_disabled':bool(command&1) if op==3 else None})
    display=[]; previous=None
    for m in snapshots:
        state=tuple(m[k] for k in ('width','height','x','y','rgb24','display_disabled'))
        if state!=previous:display.append(m);previous=state
    pcm=(sw/'ffvii-software-audio.pcm').read_bytes(); first_audio=None; nonzero=0
    for i,(left,right) in enumerate(struct.iter_unpack('<hh',pcm)):
        if left or right:
            if first_audio is None:first_audio=i
            nonzero+=1
    result={'software_frames':len(frames), 'software_gpu_events':events,
        'rtl_through_completed_cycle':int(snapshots[-1]['cycles']),
        'rtl_CD_prefix_sha256':hashlib.sha256(''.join(prefix).encode()).hexdigest(),
        'rtl_snapshot_hashes':snapshot_hashes,
        'rtl_display_observations':display,
        'CD':{'software_fetch_requests':len(requests),'software_delivered_sectors':len(delivered),
            'rtl_hps_fetch_requests':len(fetched),'deduplicated_common_prefix':common,
            'first_request_divergence':{'software':a[max(0,common-3):common+8],
                'rtl':b[max(0,common-3):common+8]} if common<min(len(a),len(b)) else None,
            'uncompared_tail':{'software':len(a)-common,'rtl':len(b)-common},
            'matching_blocks':[{'software_index':x.a,'rtl_index':x.b,'length':x.size}
                for x in matcher.get_matching_blocks() if x.size],
            'equal_fetch_sequence':a==b,'comparison_is_delivery_equivalence':False},
        'software_audio':{'samples':len(pcm)//4,'nonzero_samples':nonzero,
            'first_nonzero_sample':first_audio,'first_nonzero_emulated_seconds':first_audio/44100 if first_audio is not None else None},
        'limitations':['RTL snapshots only bound display transitions; they do not record each GP1 command',
            'Boot-path and BIOS/disc identity must be checked independently; this script reads capture logs only',
            'RTL HPS fetches include lookahead and seek servicing; software delivery trace has different semantics',
            'Executable entry requires an instruction/event observer; periodic PC samples cannot prove its exact tick',
            'Publisher/Squaresoft/credit labels require inspecting captures and matching their VRAM/display origins',
            'No candidate/reference checkpoint equivalence is asserted by this report'],
        'input_hashes':{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in
            [sw/'ffvii-frame-trace.csv',sw/'ffvii-gp1-trace.csv',sw/'ffvii-cd-requests.csv',
             sw/'ffvii-sector-trace.csv',sw/'ffvii-software-audio.pcm']}}
    result['rtl_gpu_event_observations'] = rtl_gpu_events(
        rtl/'run.log', int(snapshots[-1]['cycles']))
    if result['rtl_gpu_event_observations']['instrumentation_observed']:
        result['limitations'][0] = ('Instrumented RTL records actual GP1 acceptance; '
            'snapshot-only reference runs still bound display transitions')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'output':str(args.output),'CD':result['CD'],'audio':result['software_audio']},indent=2))


if __name__=='__main__':main()
