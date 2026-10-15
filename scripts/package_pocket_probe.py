#!/usr/bin/env python3
"""Package a successfully fitted BIOS probe; never include a BIOS or game."""
import argparse
import json
from pathlib import Path
import re
import shutil
import zipfile
from prepare_resource_targets import digest
from tool_paths import ROOT


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('project',type=Path)
    p.add_argument('--output',type=Path,default=ROOT/'build/pocket/psx-probe')
    args=p.parse_args(); project=args.project.resolve();out=args.output.resolve()
    manifest=json.loads((project/'manifest.json').read_text())
    if manifest['kind']!='bios-probe':p.error('Only the BIOS probe is deployable through this packager')
    for s in manifest['sources']:
        if digest(Path(s['path']))!=s['sha256']:p.error('Build source changed: '+s['path'])
    for n,h in manifest['project_hashes'].items():
        if digest(project/n)!=h:p.error('Build project changed: '+n)
    report=(project/'output_files/apf_top.fit.summary').read_text()
    if 'Fitter Status : Successful' not in report or 'Total virtual pins : 0' not in report:
        p.error('A successful physical fit is required')
    timing=(project/'output_files/apf_top.sta.summary').read_text()
    slacks=re.findall(r'Type\s*:\s*(.*?)\nSlack\s*:\s*(-?[\d.]+)',timing)
    if not slacks or any(float(v)<0 for _,v in slacks):p.error('Reported timing violations remain')
    rbf=project/'output_files/apf_top.rbf'
    if not rbf.is_file() or rbf.stat().st_size<10000:p.error('Assembler RBF missing')
    sd=out/'SD';core=sd/'Cores/GuardianSoul.PSXProbe';core.mkdir(parents=True,exist_ok=True)
    bitreverse=bytes(int(f'{b:08b}'[::-1],2) for b in range(256))
    original=rbf.read_bytes();converted=original.translate(bitreverse)
    if converted.translate(bitreverse)!=original:raise ValueError('Bit reversal round trip')
    (core/'bitstream.rbf_r').write_bytes(converted)
    def write(name,value):(core/name).write_text(json.dumps(value,indent=2)+'\n')
    write('core.json',{'core':{'magic':'APF_VER_1','metadata':{'platform_ids':['psxprobe'],
        'shortname':'PSXProbe','description':'BIOS SDRAM readback and video','author':'GuardianSoul',
        'url':'https://github.com/GuardianSoul/openfpga-PlayStation','version':'0.1.0','date_release':'2026-10-13'},
        'framework':{'target_product':'Analogue Pocket','version_required':'1.1','sleep_supported':False,
            'dock':{'supported':True,'analog_output':False},'hardware':{'link_port':False,'cartridge_adapter':-1}},
        'cores':[{'name':'default','id':0,'filename':'bitstream.rbf_r'}]}})
    write('data.json',{'data':{'magic':'APF_VER_1','data_slots':[{'name':'BIOS','id':'0x20',
        'required':True,'parameters':'0x008','deferload':False,'filename':'bios.bin',
        'extensions':['bin'],'size_exact':524288,'address':'0x00000000'}]}})
    write('video.json',{'video':{'magic':'APF_VER_1','scaler_modes':[{'width':640,'height':480,
        'aspect_w':4,'aspect_h':3,'rotation':0,'mirror':0}]}})
    for key,field,value in [('input','controllers',[]),('interact','variables',[]),('variants','variant_list',[])]:
        body={'magic':'APF_VER_1',field:value}
        if key=='interact':body['messages']=[]
        write(key+'.json',{key:body})
    write('audio.json',{'audio':{'magic':'APF_VER_1'}})
    (core/'info.txt').write_text('BIOS memory and video bring-up probe\n* Green: full BIOS readback passed\n* Blue: loading\n* Yellow: checking\n* Red: failure\n* Audio is silent\n* This does not run PlayStation games\n')
    assets=sd/'Assets/psxprobe/common';assets.mkdir(parents=True,exist_ok=True)
    (assets/'PLACE-BIOS-HERE.txt').write_text('Place your own 524288-byte PSX BIOS dump here as bios.bin.\nNo BIOS or game is included in this package.\n')
    platforms=sd/'Platforms';platforms.mkdir(exist_ok=True)
    (platforms/'psxprobe.json').write_text(json.dumps({'platform':{'category':'Utility',
        'name':'PSX Memory Probe','year':2026,'manufacturer':'GuardianSoul'}},indent=2)+'\n')
    proof={'project':str(project),'rbf_sha256':digest(rbf),'rbf_r_sha256':digest(core/'bitstream.rbf_r'),
        'bytes':len(original),'fit_summary':report,'timing_summary':timing,'source_manifest':manifest,
        'hardware_verified':False,'complete_external_timing_verified':False,'private_assets_included':False}
    out.mkdir(parents=True,exist_ok=True);(out/'build-report.json').write_text(json.dumps(proof,indent=2)+'\n')
    shutil.copy2(ROOT/'docs/pocket-probe.md',out/'INSTALL.md')
    with zipfile.ZipFile(out/'PSXProbe.zip','w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(sd.rglob('*')):
            if path.is_file():z.write(path,path.relative_to(sd))
        z.write(out/'INSTALL.md','PSXProbe-INSTALL.md')
    print(out/'PSXProbe.zip')


if __name__=='__main__':main()
