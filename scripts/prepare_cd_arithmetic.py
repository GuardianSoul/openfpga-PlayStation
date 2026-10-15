"""Preserve CD controller behavior while replacing two expensive arithmetic forms.

Replace division used solely to classify track sectors with exact LBA thresholds,
and bounded variable modulo with repeated subtraction. All FSMs remain intact.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

if __package__:
    from .prepare_resource_targets import UPSTREAM, PIN, digest
else:
    from prepare_resource_targets import UPSTREAM, PIN, digest


def arithmetic_functions():
    original=UPSTREAM/'rtl/cd_top.vhd'; text=original.read_text()
    pattern=r'   function getSectorsPerTrack\(lba : integer\) return integer is\n.*?   end function;'
    found=re.findall(pattern,text,re.S)
    if len(found)!=1: raise ValueError('Pinned track-sector function changed')
    old=found[0]
    thresholds=[(4,9),(7,10),(11,11),(16,12),(23,13),(27,14),(32,15),
                (39,16),(44,17),(52,18),(60,19),(67,20),(74,21)]
    parsed=[tuple(map(int,pair)) for pair in re.findall(r'elsif \(mm <= (\d+)\) then return (\d+);',old)]
    if parsed!=thresholds or 'if (mm = 0) then return 8;' not in old or 'else return 22;' not in old:
        raise ValueError('Pinned track-sector table changed')
    new='''   function getSectorsPerTrack(lba : integer) return integer is
   begin
      -- Integer division truncates toward zero: also preserve negative inputs.
      if (lba > -FRAMES_PER_MINUTE and lba < FRAMES_PER_MINUTE) then return 8;
'''
    new+=''.join(f'      elsif (lba < {minute+1} * FRAMES_PER_MINUTE) then return {sectors};\n'
                 for minute,sectors in thresholds)
    new+='''      else return 22;
      end if;
   end function;'''
    wrap='''   function wrapSectorOffset(old_offset : integer; sectors_per_track : integer) return integer is
      variable remaining : integer range 0 to 32;
      variable divisor : integer range 8 to 22;
   begin
      -- synthesis translate_off
      assert old_offset >= 0 and old_offset <= 31 and sectors_per_track >= 8 and sectors_per_track <= 22
         report "CD rotation arithmetic input outside original signal ranges" severity failure;
      -- synthesis translate_on
      remaining := old_offset + 1;
      divisor := sectors_per_track;
      -- 32 / 8 is the largest possible quotient: four subtractions suffice.
      for step in 1 to 4 loop
         if remaining >= divisor then remaining := remaining - divisor; end if;
      end loop;
      return remaining;
   end function;'''
    return old,new,wrap


def cd_arithmetic_source(directory):
    actual=subprocess.check_output(['git','-C',str(UPSTREAM),'rev-parse','HEAD'],text=True).strip()
    if actual!=PIN or subprocess.run(['git','-C',str(UPSTREAM),'diff','--quiet','HEAD']).returncode:
        raise ValueError('CD reference differs from its recorded pin')
    original=UPSTREAM/'rtl/cd_top.vhd'; text=original.read_text()
    required=('signal phy_oldOffset            : integer range 0 to 31;',
              'signal phy_spt                  : integer range 8 to 22 := 8;',
              'constant FRAMES_PER_MINUTE       : integer := 75 * 60;')
    if any(text.count(marker)!=1 for marker in required): raise ValueError('CD arithmetic input ranges changed')
    old,new,wrap=arithmetic_functions()
    text=text.replace(old,new+'\n\n'+wrap)
    statement='phy_newOffset <= (phy_oldOffset + 1) mod phy_spt;'
    if text.count(statement)!=1: raise ValueError('Pinned CD modulo assignment changed')
    text=text.replace(statement,'phy_newOffset <= wrapSectorOffset(phy_oldOffset, phy_spt);')
    directory=Path(directory).resolve(); directory.mkdir(parents=True,exist_ok=True)
    path=directory/'cd_top.vhd'; path.write_text('-- GPL-2.0 derived arithmetic adaptation; upstream control/audio/sector logic retained.\n'+text)
    shutil.copy2(UPSTREAM/'LICENSE',directory/'COPYING.GPL2')
    return {'path':str(path),'library':'work','sha256':digest(path),
        'original':str(original),'original_sha256':digest(original),
        'transform':'Exact LBA thresholds instead of minute division; four bounded subtracts instead of modulo',
        'required_ranges':{'phy_oldOffset':[0,31],'phy_spt':[8,22]},
        'state_machine_latency_changed':False,'audio_and_sector_logic_changed':False,
        'game_verified':False,'hardware_verified':False}


def apply_cd_arithmetic(manifest):
    if set(manifest['targets']) not in ({'cd_top'},{'psx_top'}):
        raise ValueError('CD arithmetic adaptation requires CD or the connected system alone')
    name=next(iter(manifest['targets'])); target=manifest['targets'][name]
    if 'cd_arithmetic' in target: raise ValueError('CD arithmetic adaptation already applied')
    directory=Path(target['directory']); copy=cd_arithmetic_source(directory/'cd-arithmetic')
    changes=0
    for index,source in enumerate(target['quartus_sources']):
        if source['path']==copy['original']:
            target['quartus_sources'][index]=copy; changes+=1
    if changes!=1: raise ValueError('Expected one original CD controller')
    qsf=directory/(target['top']+'.qsf'); text=qsf.read_text()
    if text.count(copy['original'])!=1: raise ValueError('Expected one CD source assignment')
    qsf.write_text(text.replace(copy['original'],copy['path']))
    target['project_sha256']['.qsf']=digest(qsf)
    target['cd_arithmetic']=copy; manifest['cd_arithmetic']=copy
    (directory.parent/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
