"""Portable, source-bound GHDL interfaces for the actual selected PGXP core."""
# SPDX-License-Identifier: MIT
from pathlib import Path
from types import SimpleNamespace
import json
import re
import subprocess
from prepare_pgxp import selected
from measure_pocket_seeds import inputs
from prepare_resource_targets import digest, UPSTREAM, hardware_text, GPU_SOURCES
from prepare_system_verilator import gpu_transcode_source, split_divider_ports, joypad_transcode_source
from ghdl_verilog import normalize_generated_verilog
from tool_paths import find_tool
ROOT = Path(__file__).resolve().parents[1]
def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Unexpected simulation source: ' + before[:80])
    return text.replace(before, after, 1)
g = SimpleNamespace(one=selected, once=once, digest=digest, inputs=inputs, P=ROOT, R=ROOT)

def split_new(t):
 for i in range(1,7):
  t,n=re.subn(rf'      div{i}\s*: inout pgxp_div_type;',f'      div{i}_in:in pgxp_div_type;\n      div{i}_out:out pgxp_div_type;',t);assert n==1
  for f in ['start','dividend','divisor']:t=t.replace(f'div{i}.{f}',f'div{i}_out.{f}')
  for f in ['done','quotient','remainder']:t=t.replace(f'div{i}.{f}',f'div{i}_in.{f}')
 return g.once(t,'begin \n','begin \n'+'\n'.join(f"   div{i}_out.done<='0';div{i}_out.quotient<=(others=>'0');div{i}_out.remainder<=(others=>'0');"for i in range(1,7))+'\n')

def transcode_gpu(project, folder, log):
 new=True;candidate=project;control=project
 folder.mkdir();srcproject=candidate if new else control;sources=[]
 def selected(n):return g.one(srcproject,n)
 def analyze(p,lib='work'):subprocess.run([find_tool('ghdl'),'-a','--std=08','-frelaxed','--work='+lib,str(p)],cwd=folder,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
 for n in ['RamMLAB.vhd','dpram.vhd']:
  p=folder/n;p.write_bytes((UPSTREAM/'sim/system/src/mem'/n).read_bytes());analyze(p,'ramip')
 p=folder/'identity.vhd';p.write_text('library ieee;use ieee.std_logic_1164.all;entity psx_hold_cell is port(i:in std_logic;o:out std_logic);end;architecture simulation of psx_hold_cell is begin o<=i;end;');analyze(p)
 video=selected('gpu_videoout_async.vhd').read_text();bindings=[]
 for n,body in re.findall(r'   component (psx_video_\w+_cdc) is\n(.*?)   end component;',video,re.S):
  p=folder/(n+'_interface.vhd');p.write_text('library ieee;use ieee.std_logic_1164.all;entity '+n+' is\n'+body+'end;architecture opaque of '+n+' is begin end;');analyze(p,'pocketip');bindings.append(n)
 assert set(bindings)=={'psx_video_config_cdc'}
 order=['pGPU.vhd','psx_gpu_read_math.vhd','mul32u.vhd','mul9s.vhd','divider.vhd','SyncFifo.vhd','SyncFifoFallThrough.vhd'];order +=[n for n in GPU_SOURCES if n not in order]
 if new:
  bank=['psx_pgxp_divider_bank.vhd'] if any(Path(r['path']).name=='psx_pgxp_divider_bank.vhd' for r in g.inputs(project)) else []
  order=['psx_pgxp_pkg.vhd','psx_pgxp_gpu_pkg.vhd','psx_pgxp_divider.vhd']+bank+order
 for n in order:
  p=selected(n);raw=p.read_text();t=hardware_text(raw)
  if n in ['gpu_line.vhd','gpu_poly.vhd','gpu.vhd']:
   t,k=re.subn(r'(?m)^begin[ \t]*\n','begin \n',t);assert k==1
  if n=='gpu_poly.vhd':
   assert t.count('abs(vt(')==4 and t.count('x"100000000"')==2
   t=t.replace('abs(vt(','magnitude(vt(').replace('x"100000000"',"signed'(\""+('0'*(17 if new else 9))+"\" & x\"100000000\")")
   t=g.once(t,'begin \n','   function magnitude(v:integer) return integer is begin if v<0 then return -v;else return v;end if;end function;\nbegin \n')
   t=g.once(t,'to_integer(xStart(43 downto 32)) > drawingAreaRight','xStart(43 downto 32) > signed(resize(drawingAreaRight,12))')
   t=split_new(t)if new else split_divider_ports(t)
  elif n=='gpu.vhd' and new:
   t=g.once(t,'   type t_native_div_array is array(0 to 5) of div_type;', '   type t_native_div_array is array(0 to 5) of div_type;\n   signal line_div_out:t_native_div_array;\n   signal poly_div_out:t_div_array;')
   for kind in ['line','poly']:
    for i in range(1,7):
     t,k=re.subn(rf'      div{i}\s*=> {kind}_div\({i-1}\),', f'      div{i}_in=>{kind}_div({i-1}),div{i}_out=>{kind}_div_out({i-1}),',t);assert k==1
    for f in ['start','dividend','divisor']:
     marker=f'      div_array(i).{f}';a=t.index(marker);t=t[:a]+f'      {kind}_div(i).{f}<={kind}_div_out(i).{f};\n'+t[a:]
  elif n in ['gpu_line.vhd','gpu.vhd']:t=gpu_transcode_source(p.stem,t,True)
  if n=='gpu_videoout.vhd':
   for expr,count in {'to_integer(videoout_request_clkvid.lineDisp)':9,'to_integer(Gun1Y_screen)':2,'to_integer(Gun2Y_screen)':2}.items():assert t.count(expr)==count;t=t.replace(expr,"integer'("+expr+")")
   t,k=re.subn(r'(i_pixel_out_x\s*=> )videoout_request_clkvid.xpos,',r"\1integer'(videoout_request_clkvid.xpos),",t);assert k==5
  if n=='gpu_videoout_async.vhd':
   t='library pocketip;\n'+t
   for ip in bindings:
    inst=re.findall(r'(\w+)\s*:\s*'+ip+r'\s+port map',t,re.I);assert len(inst)==1;t,k=re.subn(r'('+inst[0]+r'\s*:\s*)'+ip+r'\s+port map',r'\1entity pocketip.'+ip+' port map',t);assert k==1
  t,k=re.subn(r'entity (?:work|mem)\.(?:pocket_dpram_sdp(?:_reverse)?|dpram)\b','entity ramip.dpram',t,flags=re.I);t,m=re.subn(r'entity (?:work|mem)\.(RamMLAB|dpram_dif)\b',r'entity ramip.\1',t,flags=re.I)
  if k or m:t='library ramip;\n'+t
  q=folder/n;q.write_text(t)
  for lib in (['work','mem']if n.startswith('SyncFifo')else['work']):analyze(q,lib)
  sources.append(dict(original=str(p),original_sha256=g.digest(p),path=str(q),sha256=g.digest(q)))
 out=folder/'system.v'
 with out.open('w')as f:subprocess.run([find_tool('ghdl'),'--synth','--std=08','-frelaxed','--vendor-library=ramip','--vendor-library=pocketip','--out=verilog','gpu'],cwd=folder,stdout=f,stderr=log,check=True,timeout=180)
 normalize_generated_verilog(out);t=out.read_text();names=re.findall(r'^module (\w+)',t,re.M);pre='shared__'if new else'original__';expr=re.compile(r'\b('+'|'.join(sorted(names,key=len,reverse=True))+r')\b');q=folder/'renamed.v';q.write_text(expr.sub(lambda m:pre+m[0],t));top=re.findall(r'^module (gpu(?:_Barch[^\s]*)?)\s*\n',t,re.M);assert len(top)==1;return dict(sources=sources,verilog=str(q),module=pre+top[0])

def transcode_core(project, output, gpu):
    C=Path(project).resolve();D=Path(output).resolve();F=D/'vhdl';F.mkdir();rows=g.inputs(C);protected={};sources=[]
    # Reuse the exact already tested GPU compatibility copies, whose origins
    # are the same selected frozen source graph. Analyze them into this new lib.
    gpurows={Path(x['original']).name:x for x in gpu['sources']}
    log=(D/'transcode.log').open('w')
    def analyze(p,lib='work'):
     r=subprocess.run([find_tool('ghdl'),'-a','--std=08','-frelaxed','--work='+lib,str(p)],cwd=F,stdout=log,stderr=subprocess.STDOUT,timeout=60);assert r.returncode==0,(p,D/'transcode.log')
    # Common RAM behavioral interface, blackboxed into the checked SV memories.
    for n in ['RamMLAB.vhd','dpram.vhd']:
     p=F/n;p.write_bytes((UPSTREAM/'sim/system/src/mem'/n).read_bytes());analyze(p,'ramip')
    p=g.one(C,'psx_m10k_sdp.vhd');t=p.read_text();a=t.index('architecture ');stub=F/'psx_m10k_sdp.vhd';stub.write_text(t[:a].replace('library altera_mf;','').replace('use altera_mf.altera_mf_components.all;','')+'architecture opaque of psx_m10k_sdp is begin end;\n');analyze(stub,'ramip')
    # Bind mixed-language interfaces by their exact selected component ports.
    bindings={}
    for n in ['gte.vhd','psx_top.vhd','gpu_videoout_async.vhd','spu.vhd']:
     t=g.one(C,n).read_text()
     for name,body in re.findall(r'\bcomponent\s+(\w+)\s+is\s*\n(.*?)\bend component\s*;',t,re.S|re.I):
      assert name.startswith('psx_'),name
      q=F/(name+'-interface.vhd');q.write_text('library ieee;use ieee.std_logic_1164.all;use ieee.numeric_std.all;\nentity '+name+' is\n'+body+'end;architecture opaque of '+name+' is begin end;');analyze(q,'pocketip');bindings[name]=g.digest(q)
    for n,x in gpurows.items():
     p=Path(x['original']);assert g.digest(p)==x['original_sha256'];assert g.digest(Path(x['path']))==x['sha256'];assert g.digest(g.one(C,n))==x['original_sha256'];protected[str(p)]=g.digest(p)
    # Own packages precede CPU, DMA and the top.
    for n in ['psx_pgxp_pkg.vhd','psx_pgxp_gpu_pkg.vhd','psx_pgxp_divider.vhd']:
     p=g.one(C,n);q=F/n;q.write_bytes(p.read_bytes());analyze(q);protected[str(p)]=g.digest(p)
    for n,x in gpurows.items():
     if n in ['psx_pgxp_pkg.vhd','psx_pgxp_gpu_pkg.vhd','psx_pgxp_divider.vhd']:continue
     t=Path(x['path']).read_text();q=F/n;q.write_text(t)
     if n in ['SyncFifo.vhd','SyncFifoFallThrough.vhd']:
      analyze(q,'mem');analyze(q)
     else:analyze(q)
    # pJoypad before pad entities; QSF entity order otherwise retained. Defer
    # the system wrapper until all selected leaves and memory adapters exist.
    items=[]
    for row in rows:
     if row['assignment']!='VHDL_FILE':continue
     p=Path(row['path']);n=p.name
     if n in gpurows or n in ['RamMLAB.vhd','dpram.vhd','psx_m10k_sdp.vhd','pocket_dpram_sdp.vhd','pocket_dpram_sdp_reverse.vhd']:continue
     items.append((n,p))
    priority=['export.vhd','pJoypad.vhd','psx_wfifo_m10k.vhd']
    items.sort(key=lambda x:(0,priority.index(x[0]))if x[0]in priority else(2,0)if x[0]=='psx_top.vhd'else(3,0)if x[0]=='psx_mister_psx_top_resource.vhd'else(1,0))
    for n,p in items:
     raw=p.read_text();t=hardware_text(raw);changes=[]
     if n=='export.vhd':
      t=t.split('end package;')[0];t=g.once(t,'   signal regs                   : tExportRegs;','')+'end package;\n';changes.append('omit debug-only package signal')
     if n=='spu_ram.vhd':
      t=t.replace('LIBRARY altera_mf;','').replace('USE altera_mf.altera_mf_components.all;','');changes.append('omit unused vendor context')
     if n=='psx_top.vhd':
      t,k=re.subn(r'(savestate_number\s*=> )savestate_number,',r"\1integer'(savestate_number),",t);assert k==2
      old="signal ss_ram_BURSTCNT        : std_logic_vector(7 downto 0) := (others => '0');";t=g.once(t,old,"signal ss_ram_BURSTCNT        : std_logic_vector(7 downto 0);");changes.append('explicit integer associations and undriven-port alias initializer omission')
     if n=='cd_top.vhd':
      t=g.once(t,'std_logic_vector(to_unsigned(positionInIndex, 20))','std_logic_vector(to_signed(positionInIndex, 20))');changes.append('20-bit signed pregap encoding')
     if n.startswith('joypad_pad') and 'portStates(portNr).dsRumbleConfig' in t:t=joypad_transcode_source(t);changes.append('static identical rumble byte selection')
     # Bind these pure wire/clock interfaces to the REAL selected SV modules.
     has=False
     for name in bindings:
      pat=r'(\bcomponent\s+'+name+r'\s+is\s*\n.*?\bend component\s*;)'
      if re.search(pat,t,re.S|re.I):
       t,k=re.subn(pat,r'\1\n   for all:'+name+' use entity pocketip.'+name+';',t,flags=re.S|re.I);assert k==1;has=True
     if has:t='library pocketip;\n'+t;changes.append('exact component interface bindings to selected real SV')
     t,k=re.subn(r'entity (?:work|mem)\.(?:pocket_dpram_sdp(?:_reverse)?|dpram)\b','entity ramip.dpram',t,flags=re.I)
     t,m=re.subn(r'entity (?:work|mem)\.(RamMLAB|dpram_dif|psx_m10k_sdp)\b',r'entity ramip.\1',t,flags=re.I)
     if k or m:t='library ramip;\n'+t;changes.append('common opaque synchronous RAM interfaces')
     q=F/n;q.write_text(t)
     for lib in (['work','mem']if n in ['SyncFifoFallThroughMLAB.vhd','psx_wfifo_m10k.vhd']else['work']):analyze(q,lib)
     protected[str(p)]=g.digest(p);sources.append(dict(original=str(p),original_sha256=g.digest(p),converted=str(q),converted_sha256=g.digest(q),changes=changes))
    out=D/'system.v'
    with out.open('w')as f:r=subprocess.run([find_tool('ghdl'),'--synth','--std=08','-frelaxed','--vendor-library=ramip','--vendor-library=pocketip','--out=verilog','psx_mister_psx_top_resource'],cwd=F,stdout=f,stderr=log,timeout=240)
    assert r.returncode==0,D/'transcode.log';normalize_generated_verilog(out);log.close();assert all(g.digest(Path(p))==h for p,h in protected.items());(D/'transcode-report.json').write_text(json.dumps(dict(scope='Actual full selected PGXP resource with real SV blackboxes and common behavioral RAM interfaces; no CPU/GTE/DMA/GPU/memory service logic replaced. Guest qualification pending.',sources=sources,protected=protected,gpu_sources=gpu,bindings=bindings,verilog_sha256=g.digest(out)),indent=2)+'\n');print('Transcoded actual fully connected PGXP core',flush=True)

    return json.loads((D/'transcode-report.json').read_text())
