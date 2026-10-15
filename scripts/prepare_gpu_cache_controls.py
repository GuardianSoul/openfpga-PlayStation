#!/usr/bin/env python3
"""Prepare unused GPU miss payloads in IDLE; retain original tag RAM/cycles.

Original acceptance and active RAM writes stay unchanged; palette/filtered
requests retain overrides. A per-type one-hot annotation shortens state decode.
This is source-bound preparation, not a production selection or timing proof.
"""
import argparse
import json
from pathlib import Path
import shutil
from prepare_resource_targets import UPSTREAM,digest

import re

def cache_controls_text(text):
 marker="         elsif (ce = '1') then\n"
 if text.count(marker)!=1:raise ValueError('Pixel pipeline enable block changed')
 if 'tagGeneration' in text:
  tag="std_logic_vector(tagGeneration) & std_logic_vector(tag_data(0))"
 else:tag='std_logic_vector(tag_data(0))'
 fields=[('cacherequest',"(others => '0')"),('tag_data_a',tag),('tag_address_a','tag_addr(0)'),('cache_address_a','tag_addr(0)'),('reqVRAMXPos','stage0_textaddr(0)(10 downto 1)'),('reqVRAMYPos','stage0_textaddr(0)(19 downto 11)'),('reqVRAMSize','to_unsigned(1, 11)')]
 # Remove only assignments in the original unfiltered texture-miss arm.
 start=text.index("                  elsif (stage0_valid = '1' and stage0_texture = '1' and cachehit(0) = '0') then")
 end=text.index('\n                  end if;',start)
 block=text[start:end]
 for name,value in fields:
  pattern=r'^\s*'+name+r'\s*<=\s*'+re.escape(value)+r';[^\S\n]*$'
  block,n=re.subn(pattern,'',block,flags=re.M)
  if n!=1:raise ValueError('Miss payload assignment changed: '+name)
 text=text[:start]+block+text[end:]
 spec="\n            -- Prepare ungranted miss payloads; accepted requests keep the same values.\n            if state=IDLE then\n"+''.join('               '+name+' <= '+value+';\n' for name,value in fields)+'            end if;\n'
 text=text.replace(marker,marker+spec)
 if 'syn_encoding' in text:raise ValueError('Encoding already selected')
 pattern=r'(   signal state\s*:\s*tState := IDLE;)'
 text,n=re.subn(pattern,'   attribute syn_encoding : string;\n   attribute syn_encoding of tState : type is "one-hot";\n'+r'\1',text)
 if n!=1:raise ValueError('Pixel pipeline state declaration changed')
 return text

def prepare(selected,directory,expected_sha256):
    selected=Path(selected).resolve()
    if digest(selected)!=expected_sha256:raise ValueError('Selected pixel pipeline identity mismatch')
    changed=cache_controls_text(selected.read_text())
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=False)
    output=directory/'gpu_pixelpipeline.vhd';output.write_text(changed)
    if digest(selected)!=expected_sha256:raise ValueError('Pixel source changed during preparation')
    shutil.copyfile(UPSTREAM/'LICENSE',directory/'COPYING.GPL2')
    result=dict(path=str(output),sha256=digest(output),selected=str(selected),selected_sha256=expected_sha256,
        assignment='VHDL_FILE',library='work',preparer_sha256=digest(Path(__file__)),
        transform='Prepare miss payloads in IDLE, retain palette/filtered overrides and original tag RAM; one-hot pixel FSM',
        added_pipeline_clocks=0,new_clock=False,tag_storage_changed=False,
        production_selected=False,fit_verified=False,timing_verified=False,hardware_verified=False,game_verified=False,
        required_contracts=['Original request enables/state transitions and CE/reset retained',
            'Unused IDLE payload values are observed only on original acceptance or active RAM writes',
            'Tag write enable/address/data and cache write enable/address retain values when enabled',
            'Measure complete physical top under unchanged constraints before selection'],
        limitations=['No preset or timing exception selected','Bounded GPU equivalence is not whole release/game qualification'])
    (directory/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('selected',type=Path);p.add_argument('output',type=Path);p.add_argument('--expected-sha256',required=True)
    a=p.parse_args();print(json.dumps(prepare(a.selected,a.output,a.expected_sha256),indent=2))
