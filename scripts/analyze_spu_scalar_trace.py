#!/usr/bin/env python3
"""Measure spatial reuse in scalar SPU requests; no timing/area prediction.

This only replays request addresses. A hit estimate is not evidence that an
RTL buffer meets sample deadlines, handles reset/writes or costs a given area.
"""
import argparse
import csv
import json
from pathlib import Path

from prepare_resource_targets import digest


def folded_index(line,entries):
    bits=(entries-1).bit_length();index=0
    while line:
        index^=line&(entries-1);line>>=bits
    return index


def analyze(path):
    rows=[];previous=-1
    with Path(path).open() as f:
        for fields in csv.reader(f):
            if len(fields)!=7:raise ValueError('Unexpected scalar trace columns')
            cycle,phase,measured,rnw,address,data,mask=map(int,fields)
            if cycle<=previous or address&1 or not 0<=address<524288 or mask!=3:
                raise ValueError('Invalid scalar request trace')
            previous=cycle;rows.append((phase,measured,rnw,address))
    if not rows:raise ValueError('Empty trace')
    candidates=[]
    for line_bytes in (4,8,16):
        for entries in (1,4,8,16,32,64,128):
            for mapping in (('linear',) if entries==1 else ('linear','folded','fully-associative-LRU')):
                tags={};order=[];phases={}
                for phase,measured,rnw,address in rows:
                    line=address//line_bytes
                    index=(line&(entries-1) if mapping=='linear' else
                           folded_index(line,entries) if mapping=='folded' else line)
                    hit=tags.get(index)==line
                    if not rnw:tags.clear();order.clear()
                    elif mapping=='fully-associative-LRU':
                        if line in order:order.remove(line)
                        elif len(order)>=entries:del tags[order.pop(0)]
                        tags[line]=line;order.append(line)
                    else:tags[index]=line
                    if measured:
                        st=phases.setdefault(str(phase),dict(reads=0,writes=0,hits=0,misses=0))
                        if rnw:st['reads']+=1;st['hits' if hit else 'misses']+=1
                        else:st['writes']+=1
                for st in phases.values():st['hit_fraction']=st['hits']/st['reads'] if st['reads'] else None
                candidates.append(dict(line_bytes=line_bytes,entries=entries,mapping=mapping,
                    data_bits=8*line_bytes*entries,full_tag_bits=(19-(line_bytes.bit_length()-1))*entries,
                    valid_bits=entries,write_policy='Invalidate all entries on every accepted write',phases=phases))
    return dict(path=str(path),trace_sha256=digest(path),requests=len(rows),candidates=candidates,
                scope='Address reuse only; misses assume the whole aligned line arrives before the next request; no native timing, bandwidth, area or coherence proof')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();report=json.loads(args.report.read_text())
    if not report['passed'] or not report['source_identity_verified'] or not report.get('scalar_trace_enabled'):
        raise ValueError('Complete source-bound scalar trace run required')
    traces={}
    for run in report['runs']:
        if run['negative_control_detected']:continue
        trace=run['scalar_trace'];path=Path(trace['path'])
        if digest(path)!=trace['sha256']:raise ValueError('Scalar trace changed')
        traces[run['name']]=analyze(path)
    result=dict(source_report=str(args.report),source_report_sha256=digest(args.report),profiles=traces,
        analyzer_sha256=digest(Path(__file__)),RTL_implemented=False,area_measured=False,
        native_deadline_verified=False,production_selected=False,hardware_verified=False,game_verified=False)
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(args.output)


if __name__=='__main__':main()
