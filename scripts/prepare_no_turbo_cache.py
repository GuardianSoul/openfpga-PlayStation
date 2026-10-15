"""Remove MiSTer's optional turbo data cache only with its controls fixed off.

The real PSX instruction cache and 1 KiB scratchpad are separate and retained.
No instruction, memory, interrupt or GTE pipeline state is changed.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess

if __package__:
    from .prepare_resource_targets import UPSTREAM, PIN, digest
else:
    from prepare_resource_targets import UPSTREAM, PIN, digest


def no_turbo_cache_source(directory):
    actual=subprocess.check_output(['git','-C',str(UPSTREAM),'rev-parse','HEAD'],text=True).strip()
    if actual!=PIN or subprocess.run(['git','-C',str(UPSTREAM),'diff','--quiet','HEAD']).returncode:
        raise ValueError('CPU reference differs from the recorded pin')
    original=UPSTREAM/'rtl/cpu.vhd'; text=original.read_text()
    pattern=r'   idatacache : entity work.datacache\n.*?   \);'
    found=re.findall(pattern,text,re.S)
    if len(found)!=1 or 'SIZE              => 16384,' not in found[0] or 'BITWIDTH          => 32' not in found[0]:
        raise ValueError('Pinned optional data-cache instance changed')
    text=text.replace(found[0],'''   -- Optional turbo data cache is unobservable with TURBO_CACHE=0.
   dcache_read_hit <= '0';
   dcache_read_data <= (others=>'0');''')
    marker='begin \n\n   -- IO'
    if text.count(marker)!=1: raise ValueError('Pinned CPU architecture marker changed')
    text=text.replace(marker,'''begin 

   -- synthesis translate_off
   assert TURBO_CACHE='0' and TURBO_CACHE50='0'
      report "CPU specialization requires both turbo-cache controls off" severity failure;
   -- synthesis translate_on

   -- IO''')
    directory=Path(directory).resolve(); directory.mkdir(parents=True,exist_ok=True)
    path=directory/'cpu.vhd'; path.write_text('-- GPL-2.0 CPU specialization; original instruction cache and scratchpad retained.\n'+text)
    shutil.copy2(UPSTREAM/'LICENSE',directory/'COPYING.GPL2')
    return {'path':str(path),'library':'work','sha256':digest(path),
        'original':str(original),'original_sha256':digest(original),
        'transform':'Omit optional 16384-word turbo data cache; tie its unused outputs to zero',
        'required_controls':{'TURBO_CACHE':0,'TURBO_CACHE50':0},
        'psx_instruction_cache_retained':True,'psx_scratchpad_retained':True,
        'game_verified':False,'hardware_verified':False}


def apply_no_turbo_cache(manifest):
    if set(manifest['targets']) not in ({'cpu'},{'psx_top'}):
        raise ValueError('CPU specialization requires CPU or the connected system alone')
    name=next(iter(manifest['targets'])); target=manifest['targets'][name]
    if 'no_turbo_cache' in target: raise ValueError('CPU specialization already applied')
    for control in ('TURBO_CACHE','TURBO_CACHE50'):
        if target['fixed_ports'].get(control)!="'0'": raise ValueError('CPU control contract not satisfied: '+control)
    directory=Path(target['directory']); copy=no_turbo_cache_source(directory/'no-turbo-cache')
    changes=0
    for index,source in enumerate(target['quartus_sources']):
        if source['path']==copy['original']: target['quartus_sources'][index]=copy; changes+=1
    if changes!=1: raise ValueError('Expected one CPU source')
    qsf=directory/(target['top']+'.qsf'); text=qsf.read_text()
    if text.count(copy['original'])!=1: raise ValueError('Expected one CPU assignment')
    qsf.write_text(text.replace(copy['original'],copy['path']))
    target['project_sha256']['.qsf']=digest(qsf)
    target['no_turbo_cache']=copy; manifest['no_turbo_cache']=copy
    (directory.parent/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
