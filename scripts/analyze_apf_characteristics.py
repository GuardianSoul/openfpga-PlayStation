#!/usr/bin/env python3
"""Strict APF datasheet/transit analysis; these values are not timing slack.

Available serial apertures assume the tested rising-edge peer and source
schedule. They do not supply external receiver, PCB, load or jitter limits.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import re
import subprocess

from tool_paths import ROOT

SECTIONS = {
    'Setup Times': 'setup', 'Hold Times': 'hold',
    'Clock to Output Times': 'tco_max', 'Minimum Clock to Output Times': 'tco_min',
    'Output Enable Times': 'enable_max', 'Minimum Output Enable Times': 'enable_min',
    'Output Disable Times': 'disable_max', 'Minimum Output Disable Times': 'disable_min',
}
INPUT_PAIRS = {('bridge_1wire','clk_74a'), ('bridge_spiss','clk_74a'),
               ('bridge_spiss','bridge_spiclk'), ('bridge_spimiso','bridge_spiclk'),
               ('bridge_spimosi','bridge_spiclk')}
OUTPUT_PORTS = {'bridge_1wire','bridge_spiclk','bridge_spimiso','bridge_spimosi'}
MODELS = {'Slow 1100mV 85C Model','Slow 1100mV 0C Model',
          'Fast 1100mV 85C Model','Fast 1100mV 0C Model'}


def number(value):
    result = float(value)
    if not math.isfinite(result):raise ValueError('Nonfinite APF characteristic')
    return result


def parse_sheet(text):
    section = None; header = None; seen_sections = set(); result = {}
    for line in text.splitlines():
        cells = [s.strip() for s in line.split(';')[1:-1]]
        if len(cells)==1 and cells[0] in SECTIONS:
            section=SECTIONS[cells[0]];header=None
            if section in seen_sections:raise ValueError('Duplicate APF datasheet section')
            seen_sections.add(section)
            continue
        if len(cells)==6 and cells[0]=='Data Port':
            header=cells
            continue
        if not cells or not cells[0].startswith('bridge_'):continue
        if section is None or header is None or len(cells)!=6:
            raise ValueError('Malformed APF datasheet row')
        port,clock,a,b,edge,reference=cells
        expected_labels=['0 to Hi-Z','1 to Hi-Z'] if section.startswith('disable_') else ['Rise','Fall']
        if header!=['Data Port','Clock Port',*expected_labels,'Clock Edge','Clock Reference']:
            raise ValueError('Unreviewed APF datasheet columns')
        valid=(port,clock) in INPUT_PAIRS if section in ('setup','hold') else port in OUTPUT_PORTS and clock=='clk_74a'
        if not valid or edge!='Rise' or reference!=clock:
            raise ValueError('Unexpected APF port/clock/reference/edge')
        key=(section,port,clock)
        if key in result:raise ValueError('Duplicate APF datasheet row')
        result[key]=dict(first_ns=number(a),second_ns=number(b),labels=expected_labels)
    expected={(kind,p,c) for kind in ('setup','hold') for p,c in INPUT_PAIRS}
    expected |= {(kind,p,'clk_74a') for kind in SECTIONS.values() if kind not in ('setup','hold') for p in OUTPUT_PORTS}
    if set(result)!=expected or len(seen_sections)!=8:raise ValueError('Incomplete APF datasheet coverage')
    for port in OUTPUT_PORTS:
        for kind in ('tco','enable','disable'):
            lo=result[(kind+'_min',port,'clk_74a')];hi=result[(kind+'_max',port,'clk_74a')]
            if any(lo[k]>hi[k]+.001 for k in ('first_ns','second_ns')):
                raise ValueError('Inverted APF min/max interval')
    return result


def derive(sheet, period):
    def row(kind,port):return sheet[(kind,port,'clk_74a')]
    cmin=row('tco_min','bridge_spiclk');cmax=row('tco_max','bridge_spiclk')
    serial={}
    for pin in ('bridge_spimiso','bridge_spimosi'):
        lo=row('tco_min',pin);hi=row('tco_max',pin)
        serial[pin]=dict(available_setup_ns=period+cmin['first_ns']-max(hi['first_ns'],hi['second_ns']),
                         available_hold_ns=period+min(lo['first_ns'],lo['second_ns'])-cmax['first_ns'])
    lo=row('tco_min','bridge_1wire');hi=row('tco_max','bridge_1wire')
    pulse={}
    for label,cycles in (('one',20),('zero',40)):
        nominal=cycles*period
        pulse[label]=dict(low_min_ns=nominal+lo['first_ns']-hi['second_ns'],
                          low_max_ns=nominal+hi['first_ns']-lo['second_ns'])
    enables={}
    for pin in sorted(OUTPUT_PORTS):
        enables[pin]={}
        for kind in ('enable','disable'):
            a=row(kind+'_min',pin);b=row(kind+'_max',pin)
            enables[pin][kind+'_min_ns']=min(a['first_ns'],a['second_ns'])
            enables[pin][kind+'_max_ns']=max(b['first_ns'],b['second_ns'])
    return dict(serial_pin_apertures=serial,PAD_uninterrupted_low_pulses=pulse,output_driver_delays=enables)


def read_table(path):
    columns = {
        'clocks.tsv': ['clock', 'period_ns'],
        'geometry.tsv': ['direction', 'port', 'register'],
        'transit.tsv': ['corner', 'arc', 'direction', 'port', 'register', 'minimum', 'from', 'to', 'delay_ns'],
    }
    with Path(path).open() as f:
        reader = csv.DictReader(f, delimiter='\t')
        if reader.fieldnames != columns[Path(path).name]:
            raise ValueError('Unreviewed APF table columns')
        rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise ValueError('Malformed APF table row')
    return rows


def analyze(directory):
    directory=Path(directory)
    clocks=read_table(directory/'clocks.tsv')
    if len(clocks)!=2 or {x['clock'] for x in clocks}!={'clk_74a','bridge_spiclk'} or any(
            abs(number(x['period_ns'])-13.468)>.002 for x in clocks):raise ValueError('Unreviewed APF clocks')
    period=number(next(x['period_ns'] for x in clocks if x['clock']=='clk_74a'))
    geometry=read_table(directory/'geometry.tsv')
    expected_counts={('input','bridge_1wire'):1,('input','bridge_spiclk'):17,('input','bridge_spiss'):18,
                     ('input','bridge_spimiso'):4,('input','bridge_spimosi'):4,
                     **{('output',p):2 for p in OUTPUT_PORTS}}
    groups={}
    for row in geometry:
        key=(row['direction'],row['port']);groups.setdefault(key,[]).append(row['register'])
    if set(groups)!=set(expected_counts) or any(len(groups[k])!=n or len(set(groups[k]))!=n for k,n in expected_counts.items()):
        raise ValueError('Incomplete APF physical geometry')
    expected_arcs={(d,p,n) for (d,p),names in groups.items() for n in names if (d,p)!=('input','bridge_spiclk')}
    if len(expected_arcs)!=35:raise ValueError('Missing APF data/control arcs')
    transit=read_table(directory/'transit.tsv');keys=set();arc_ids={}
    for row in transit:
        corner=int(row['corner']);minimum=int(row['minimum']);arc=(row['direction'],row['port'],row['register'])
        if corner not in range(4) or minimum not in (0,1) or arc not in expected_arcs:raise ValueError('Unexpected APF transit endpoint')
        endpoints=(row['port'],row['register']) if row['direction']=='input' else (row['register'],row['port'])
        if (row['from'],row['to'])!=endpoints:raise ValueError('APF transit endpoint mismatch')
        key=(corner,arc,minimum)
        if key in keys:raise ValueError('Duplicate APF transit row')
        keys.add(key);number(row['delay_ns'])
        index=int(row['arc'])
        if index not in range(35) or (index in arc_ids and arc_ids[index]!=arc):
            raise ValueError('Inconsistent APF transit arc index')
        arc_ids[index]=arc
    if len(keys)!=280:raise ValueError('Incomplete APF transit coverage')
    if len(arc_ids)!=35 or set(arc_ids.values())!=expected_arcs:
        raise ValueError('Incomplete APF transit arc indices')
    for corner in range(4):
        for arc in expected_arcs:
            values={int(x['minimum']):number(x['delay_ns']) for x in transit if int(x['corner'])==corner and (x['direction'],x['port'],x['register'])==arc}
            if values[1]>values[0]+.001:raise ValueError('Inverted APF transit min/max')
    corners=[];models=set();rows=[]
    for corner in range(4):
        sheet=parse_sheet((directory/f'corner{corner}-datasheet.txt').read_text())
        model=re.search(r'Delay Model:\s*\n\s*([^\n]+)',(directory/f'corner{corner}-arc0-min0.txt').read_text())
        if not model or model[1].strip() not in MODELS or model[1].strip() in models:raise ValueError('Missing/duplicate APF model')
        models.add(model[1].strip())
        corners.append(dict(corner=corner,model=model[1].strip(),**derive(sheet,period)))
        rows.extend(dict(corner=corner,kind=k[0],port=k[1],clock=k[2],**v) for k,v in sorted(sheet.items()))
    if models!=MODELS:raise ValueError('Missing APF operating corner')
    inputs=[]
    for port,clock in sorted(INPUT_PAIRS):
        inputs.append(dict(port=port,clock=clock,asynchronous_sampler=clock=='clk_74a',
                           intrinsic_setup_ns=max(max(r['first_ns'],r['second_ns']) for r in rows if r['kind']=='setup' and (r['port'],r['clock'])==(port,clock)),
                           intrinsic_hold_ns=max(max(r['first_ns'],r['second_ns']) for r in rows if r['kind']=='hold' and (r['port'],r['clock'])==(port,clock))))
    return dict(scope='FPGA-side characterization, not slack or external timing qualification',corners=corners,
                datasheet_rows=rows,transit_rows=transit,geometry=geometry,input_characteristics=inputs,
                serial_available_setup_min_ns=min(p['available_setup_ns'] for c in corners for p in c['serial_pin_apertures'].values()),
                serial_available_hold_min_ns=min(p['available_hold_ns'] for c in corners for p in c['serial_pin_apertures'].values()),
                path_requirements_added=False,board_qualified=False,MTBF_qualified=False,
                assumptions=['Nominal74.25MHz source and uninterrupted tested launch schedule.',
                             'SPI peer samples the forwarded rising edge; receiver/PCB/jitter limits unqualified.',
                             'Quartus existing IO/load model; raw register-to-pad delay excludes clock tree and clock-to-Q.',
                             'PAD and SS sampler tsu/th values are intrinsic first-flop values, not requirements on an asynchronous source.',
                             'Select also controls receiver enables/resets; the SPI-clocked consumers need real input timing.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path);parser.add_argument('--output',type=Path);args=parser.parse_args()
    result=analyze(args.directory)
    if args.output:
        out=args.output.resolve()
        if out.exists() or not out.is_relative_to(ROOT/'build') or subprocess.run(['git','check-ignore','--quiet',str(out)],cwd=ROOT).returncode:
            parser.error('Use a fresh ignored build output')
        out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2)+'\n')
    else:print(json.dumps(result,indent=2))


if __name__=='__main__':main()
