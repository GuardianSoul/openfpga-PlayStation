#!/usr/bin/env python3
"""Exact combinational circular-priority selector for captured Pocket SDRAM.

Preserves all selected/selected_valid values, including no-valid and held
invalid owners. No new state, grant policy, controller or pipeline stage.
The selected source may include existing reset/timer adaptations.
"""
import argparse
import json
from pathlib import Path
import re
from prepare_resource_targets import digest

def selector_text(text):
 for pattern in [r'\binput\s+wire\s+\[3:0\]\s+valid\s*,\s*write\b',r'\breg\s+\[1:0\]\s+round_robin\s*=\s*0\s*,\s*locked_owner\s*=\s*0\s*;']:
  if len(re.findall(pattern,text))!=1:raise ValueError('SDRAM selector requires the captured four-client/two-bit geometry')
 marker='    always @* begin\n        selected=locked_owner;'
 if text.count(marker)!=1 or len(re.findall(r'^module psx_sdram_shared\b',text,re.M))!=1:raise ValueError('Expected one SDRAM selector/module')
 start=text.index(marker)
 stop=text.index('    // Keep the readiness return separate',start)
 before=text[start:stop]
 if before.count('for(integer i=0;i<4;i++)')!=1 or before.count("2'(round_robin+i)")!=2:raise ValueError('SDRAM selector changed')
 # Each table is a six-input Boolean function, no new RAM/FF or pipeline.
 low=high=0
 for rr in range(4):
  for valid in range(16):
   # Closed circular priority specification, preserving the no-valid value.
   selected=rr
   for distance in range(3,-1,-1):
    port=(rr+distance)&3
    if valid&(1<<port):selected=port
   index=(rr<<4)|valid;low|=(selected&1)<<index;high|=((selected>>1)&1)<<index
 after=f'''    // Six-input lookup of the unchanged circular priority function.
    // Keep locked-owner handling and the no-valid selection exact.
    localparam [63:0] PICK_LO=64'h{low:016x},PICK_HI=64'h{high:016x};
    wire [5:0] pick_index={{round_robin,valid}};
    wire [1:0] unlocked_pick={{PICK_HI[pick_index],PICK_LO[pick_index]}};
    always @* begin
        selected=locked ? locked_owner:unlocked_pick;
        selected_valid=locked ? valid[locked_owner]:(|valid);
    end
'''
 return text[:start]+after+text[stop:],before,after


def prepare(selected,directory,expected_sha256):
    selected=Path(selected).resolve()
    if digest(selected)!=expected_sha256:raise ValueError('SDRAM source identity mismatch')
    changed,old,new=selector_text(selected.read_text())
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=False)
    path=directory/'psx_sdram_shared.sv';path.write_text(changed)
    if digest(selected)!=expected_sha256:raise ValueError('SDRAM source changed during preparation')
    result=dict(path=str(path),sha256=digest(path),selected=str(selected),selected_sha256=expected_sha256,
        assignment='SYSTEMVERILOG_FILE',library='work',preparer_sha256=digest(Path(__file__)),
        transform='Exact six-input circular-priority lookup; locked and no-valid values retained',
        added_pipeline_clocks=0,arbitration_policy_changed=False,registers_added=0,
        production_selected=False,hardware_verified=False,timing_verified=False,
        limitations=['Only selector changed; full fit and board timing require measurement',
            'Must replace the actually assigned psx_sdram_shared module, not a stale basename copy'])
    (directory/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('selected',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--expected-sha256',required=True);a=p.parse_args()
    print(json.dumps(prepare(a.selected,a.output,a.expected_sha256),indent=2))
