#!/usr/bin/env python3
"""Package the physical BIOS + both-die PSRAM probe, without private assets."""
import argparse
import json
from pathlib import Path
import re
import shutil
import zipfile

from prepare_resource_targets import digest
from tool_paths import ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'build/pocket/psx-burst-probe')
    args=parser.parse_args();project=args.project.resolve();out=args.output.resolve()
    manifest=json.loads((project/'manifest.json').read_text())
    if manifest['kind']!='bios-psram-burst-probe' or manifest['virtual_pins']:
        parser.error('Physical BIOS/PSRAM burst probe required')
    for s in manifest['sources']:
        if digest(Path(s['path']))!=s['sha256']:parser.error('Compiled source changed: '+s['path'])
    for name,sha in manifest['project_hashes'].items():
        if digest(project/name)!=sha:parser.error('Compiled project changed: '+name)
    fit=(project/'output_files/apf_top.fit.summary').read_text()
    if 'Fitter Status : Successful' not in fit or 'Total virtual pins : 0' not in fit:
        parser.error('Successful physical fit required')
    timing=(project/'output_files/apf_top.sta.summary').read_text()
    slacks=re.findall(r'Type\s*:\s*(.*?)\nSlack\s*:\s*(-?[\d.]+)',timing)
    if not slacks or any(float(v)<0 for _,v in slacks):parser.error('Reported timing violations remain')
    rbf=project/'output_files/apf_top.rbf'
    if not rbf.is_file() or rbf.stat().st_size<10000:parser.error('Assembler RBF missing')
    sd=out/'SD';core=sd/'Cores/GuardianSoul.PSXBurstProbe';core.mkdir(parents=True,exist_ok=True)
    original=rbf.read_bytes();table=bytes(int(f'{b:08b}'[::-1],2) for b in range(256))
    converted=original.translate(table)
    if converted.translate(table)!=original:raise ValueError('Bit reversal round trip failed')
    (core/'bitstream.rbf_r').write_bytes(converted)
    def write(name,value):(core/name).write_text(json.dumps(value,indent=2)+'\n')
    write('core.json',{'core':{'magic':'APF_VER_1','metadata':{
        'platform_ids':['psxburstprobe'],'shortname':'PSXBurstProbe',
        'description':'BIOS SDRAM and both-die PSRAM burst readback',
        'author':'GuardianSoul','url':'https://github.com/GuardianSoul/openfpga-PlayStation','version':'0.1.0','date_release':'2026-10-13'},
        'framework':{'target_product':'Analogue Pocket','version_required':'1.1','sleep_supported':False,
            'dock':{'supported':True,'analog_output':False},'hardware':{'link_port':False,'cartridge_adapter':-1}},
        'cores':[{'name':'default','id':0,'filename':'bitstream.rbf_r'}]}})
    write('data.json',{'data':{'magic':'APF_VER_1','data_slots':[{
        'name':'BIOS','id':'0x20','required':True,'parameters':'0x008','deferload':False,
        'filename':'bios.bin','extensions':['bin'],'size_exact':524288,'address':'0x00000000'}]}})
    write('video.json',{'video':{'magic':'APF_VER_1','scaler_modes':[{
        'width':640,'height':480,'aspect_w':4,'aspect_h':3,'rotation':0,'mirror':0}]}})
    for key,field in [('input','controllers'),('interact','variables'),('variants','variant_list')]:
        body={'magic':'APF_VER_1',field:[]}
        if key=='interact':body['messages']=[]
        write(key+'.json',{key:body})
    write('audio.json',{'audio':{'magic':'APF_VER_1'}})
    (core/'info.txt').write_text('BIOS + PSRAM burst memory probe\n* Green: BIOS readback AND both PSRAM dies passed\n* Yellow: checking\n* Blue: BIOS loading\n* Red: memory failure\n* Audio is silent\n* This does not run PlayStation games\n')
    assets=sd/'Assets/psxburstprobe/common';assets.mkdir(parents=True,exist_ok=True)
    (assets/'PLACE-BIOS-HERE.txt').write_text('Place your own 524288-byte PSX BIOS dump here as bios.bin.\nNo BIOS or game is included.\n')
    platforms=sd/'Platforms';platforms.mkdir(exist_ok=True)
    (platforms/'psxburstprobe.json').write_text(json.dumps({'platform':{
        'category':'Utility','name':'PSX Burst Memory Probe','year':2026,'manufacturer':'GuardianSoul'}},indent=2)+'\n')
    # Keep the upstream source/license notices with the build artifact.
    notices=core/'licenses';notices.mkdir(exist_ok=True)
    shutil.copyfile(ROOT/'references/MIRLO64/LICENSE',notices/'MIRLO64-LICENSE.txt')
    notice=ROOT/'references/MIRLO64/NOTICE'
    if notice.is_file():shutil.copyfile(notice,notices/'MIRLO64-NOTICE.txt')
    shutil.copyfile(ROOT/'THIRD_PARTY_NOTICES.md',notices/'THIRD_PARTY_NOTICES.md')
    shutil.copyfile(ROOT/'licenses/LiteX-BSD-2-Clause.txt',notices/'LiteX-BSD-2-Clause.txt')
    if (ROOT/'LICENSE').is_file():shutil.copyfile(ROOT/'LICENSE',notices/'PSX_Pocket-LICENSE.txt')
    headers=[]
    for path in (ROOT/'references/MIRLO64/target/pocket/sound_i2s.sv',
                 ROOT/'references/MIRLO64/platform/pocket/apf_top.v'):
        headers.append(path.name+'\n'+path.read_text().split('module ',1)[0])
    (notices/'APF-and-audio-source-notices.txt').write_text('\n\n'.join(headers))
    proof=dict(project=str(project),rbf_sha256=digest(rbf),rbf_r_sha256=digest(core/'bitstream.rbf_r'),
        bytes=len(original),fit_summary=fit,timing_summary=timing,source_manifest=manifest,
        hardware_verified=False,complete_external_timing_verified=False,private_assets_included=False)
    (out/'build-report.json').write_text(json.dumps(proof,indent=2)+'\n')
    shutil.copyfile(ROOT/'docs/pocket-burst-probe.md',out/'INSTALL.md')
    with zipfile.ZipFile(out/'PSXBurstProbe.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(sd.rglob('*')):
            if path.is_file():archive.write(path,path.relative_to(sd))
        archive.write(out/'INSTALL.md','PSXBurstProbe-INSTALL.md')
    print(out/'PSXBurstProbe.zip')


if __name__=='__main__':main()
