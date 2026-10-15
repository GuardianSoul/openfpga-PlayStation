#!/usr/bin/env python3
"""Opt-in BUSY-independent GPU read payload preparation, no added cycles.

All request enables/state transitions keep the pinned ready/acceptance gate.
Numeric producer payloads are prepared from state independently of BUSY. One
late mux applies video priority; no inactive primitive payload can pollute it.
Payload ports may change while not granted; this helper requires the matching
GPU top and all seven producer copies. No production preset is selected.
"""
import argparse
import json
import shutil
from pathlib import Path
from prepare_resource_targets import digest,UPSTREAM
from tool_paths import ROOT
import re
CHILDREN=('gpu_vram2vram.vhd','gpu_vram2cpu.vhd','gpu_line.vhd','gpu_rect.vhd','gpu_poly.vhd','gpu_pixelpipeline.vhd','gpu_videoout.vhd')

def ungate(s):
 # Exactly one IDLE factor, on either side of the parenthesized conjunction.
 if s.count("requestVRAMIdle = '1'")!=1:raise ValueError('Payload gate changed')
 for before in [" and requestVRAMIdle = '1'","requestVRAMIdle = '1' and "]:
  if before in s:return s.replace(before,'',1)
 raise ValueError('Missing IDLE conjunction')

def payload_text(name,text):
 if name in CHILDREN:
  for field in ['requestVRAMXPos','requestVRAMYPos','requestVRAMSize']:
   if len(re.findall(r'\b'+field+r'\b',text,re.I))!=2:raise ValueError('Output used internally '+name)
  count=0
  def replace(m):
   nonlocal count
   count+=1;return ungate(m[0])
  text=re.sub(r'^\s*requestVRAM(?:XPos|YPos|Size)\s*<=[^;]+;',replace,text,flags=re.M)
  if count!=3:raise ValueError('Payload assignments '+name)
  if name=='gpu_videoout.vhd':
   text,n=re.subn(r'^(\s*requestVRAMEnable\s*:\s*out\s+std_logic[^;]*;)',r'\1\n      requestVRAMWant : out std_logic;',text,flags=re.M)
   if n!=1:raise ValueError('Video output anchor')
   match=re.search(r'^(\s*requestVRAMEnable\s*<=[^;]+;)',text,re.M)
   if not match:raise ValueError('Video intent')
   intent=ungate(match[0].replace('requestVRAMEnable','requestVRAMWant'))
   text=text[:match.end()]+intent+text[match.end():]
  return text
 if name!='gpu.vhd':raise ValueError(name)
 compact=re.sub(r'\s+','',text)
 for anchor in ["VRAMIdle<='1'when(vramState=IDLEand(vram_WE='0'orvram_BUSY='0'))else'0';",'reqVRAMIdle<=VRAMIdleand(notvideoout_reqVRAMEnable)and(notvram_pause);']:
  if compact.count(anchor)!=1:raise ValueError('Acceptance/arbitration invariant changed')
 text,n=re.subn(r'^(\s*signal reqVRAMIdle\s*:\s*std_logic;)',r'\1\n   signal videoout_reqVRAMWant : std_logic;',text,flags=re.M)
 if n!=1:raise ValueError('Top declaration')
 for field in ['XPos','YPos','Size']:
  pattern=r'^(\s*reqVRAM'+field+r'\s*<=)([^;]+);'
  match=re.search(pattern,text,re.M)
  if not match:raise ValueError('Top payload mux '+field)
  rhs=match[2];video='videoout_reqVRAM'+field
  primitive,n=re.subn(r'\s+or\s+'+video+r'\s*$','',rhs)
  if n!=1:raise ValueError('Top video OR input '+field)
  changed=match[1]+primitive+" when videoout_reqVRAMWant='0' else "+video+';'
  text=text[:match.start()]+changed+text[match.end():]
 text,n=re.subn(r'^(\s*requestVRAMIdle\s*=> VRAMIdle and \(not vram_pause\),)',r'\1\n      requestVRAMWant => videoout_reqVRAMWant,',text,flags=re.M)
 if n!=1:raise ValueError('Video intent mapping')
 return text

def prepare(directory,sources):
    selected=[]
    for name in (*CHILDREN,'gpu.vhd'):
        matches=[s for s in sources if Path(s['path']).name==name]
        if len(matches)!=1:raise ValueError('Expected exactly one selected '+name)
        source=matches[0];path=Path(source['path']).resolve()
        if digest(path)!=source['sha256']:raise ValueError('Selected GPU source changed: '+name)
        selected.append((source,path,payload_text(name,path.read_text())))
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=False)
    generated=[]
    for source,selected_path,text in selected:
        path=directory/selected_path.name;path.write_text(text)
        if digest(selected_path)!=source['sha256']:raise ValueError('GPU source changed during preparation')
        generated.append(dict(path=str(path),sha256=digest(path),library=source.get('library','work'),
            assignment='VHDL_FILE',selected=str(selected_path),selected_sha256=source['sha256']))
    shutil.copyfile(UPSTREAM/'LICENSE',directory/'COPYING.GPL2')
    result=dict(sources=generated,preparer_sha256=digest(Path(__file__)),
        transform='Prepare all seven numeric read payloads without backend BUSY; apply video priority once after primitive OR',
        added_pipeline_clocks=0,handshake_cycles_changed=False,payload_when_ungranted_changed=True,
        required_invariant='Numeric read payloads are consumed only when VRAMIdle=1 and vram_pause=0; all seven producers and GPU top must be replaced together',
        production_selected=False,hardware_verified=False,game_verified=False,
        limitations=['No mixing new producer interfaces with an unadapted GPU top',
            'This preparation alone does not establish full fit, routed timing or game qualification'])
    (directory/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest',type=Path,help='Captured assigned-source JSON (quartus_sources, source_manifest or sources)')
    p.add_argument('output',type=Path)
    p.add_argument('--expected-manifest-sha256',required=True)
    a=p.parse_args()
    if digest(a.manifest)!=a.expected_manifest_sha256:raise ValueError('Captured manifest identity mismatch')
    m=json.loads(a.manifest.read_text())
    sources=next((m[k] for k in ['quartus_sources','source_manifest','sources'] if k in m),None)
    if not isinstance(sources,list):raise ValueError('Captured source list missing')
    print(json.dumps(prepare(a.output,sources),indent=2))
