"""Packed divider: exact GPU output cycles, primitive/readback/VRAM oracle and actual CDCs."""
from pathlib import Path
import sys,json,re,subprocess,hashlib
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'scripts'))
from measure_pocket_seeds import inputs
from prepare_resource_targets import GPU_SOURCES,UPSTREAM,digest,hardware_text
from prepare_system_verilator import gpu_transcode_source,split_divider_ports
from ghdl_verilog import normalize_generated_verilog
from tool_paths import find_tool
from prepare_divider_bank import CONSUMERS,PACKED_SHA,DIVIDER_SHA
import argparse
import run_gpu_line_share
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('project',type=Path);parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--fault',action='store_true',help='Require an actual shifted-input fault to fail at a public output')
args=parser.parse_args();P=args.project.resolve();D=args.output.resolve();W=R/'rtl/pocket'
mode='faultinv' if args.fault else 'positive'
if D.exists() or not D.is_relative_to(R/'build') or subprocess.run(['git','check-ignore','--quiet',str(D/'report.json')],cwd=R).returncode:raise ValueError('Fresh ignored GPU output required')
if digest(W/'psx_divider_bank.vhd')!=PACKED_SHA:raise ValueError('Packed divider source identity differs')
D.mkdir(parents=True)
originals=inputs(P);by={}
for s in originals:
 by.setdefault(Path(s['path']).name,[]).append(s)
def selected(name):
 rows=by[name];assert len(rows)==1,(name,rows)
 return Path(rows[0]['path'])
for name in ('gpu.vhd','gpu_poly.vhd','gpu_line.vhd','pGPU.vhd'):
 if digest(selected(name))!=CONSUMERS[name]:raise ValueError('Unreviewed GPU consumer '+name)
if digest(selected('divider.vhd'))!=DIVIDER_SHA:raise ValueError('Original divider identity differs')
needed=set(GPU_SOURCES)|{'psx_gpu_read_math.vhd','SyncFifo.vhd','SyncFifoFallThrough.vhd'}
# GPU sources are derived only from the frozen physical source manifest.
# Same opaque upstream RAM port/clock semantics on both sides; actual CDC RTL.
def transcode(folder,candidate,log):
 folder.mkdir();sources=[]
 def analyze(p,lib):
  subprocess.run([find_tool('ghdl'),'-a','--std=08','-frelaxed','--work='+lib,str(p)],cwd=folder,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
 for name in ('RamMLAB.vhd','dpram.vhd'):
  p=folder/name;p.write_bytes((UPSTREAM/'sim/system/src/mem'/name).read_bytes());analyze(p,'ramip')
 identity=folder/'identity.vhd';identity.write_text('library ieee;use ieee.std_logic_1164.all;entity psx_hold_cell is port(i:in std_logic;o:out std_logic);end;architecture simulation of psx_hold_cell is begin o<=i;end;\n');analyze(identity,'work')
 # Bind the selected mixed-language CDC interfaces to opaque entities and
 # supply the actual frozen SV implementations to Verilator below.
 video=selected('gpu_videoout_async.vhd').read_text();bindings=[]
 for name,body in re.findall(r'   component (psx_video_\w+_cdc) is\n(.*?)   end component;',video,re.S):
  p=folder/(name+'_interface.vhd');p.write_text('library ieee;use ieee.std_logic_1164.all;entity '+name+' is\n'+body+'end entity;architecture opaque of '+name+' is begin end;\n');analyze(p,'pocketip');bindings.append(name)
 assert set(bindings)=={'psx_video_config_cdc','psx_video_request_cdc','psx_video_reports_cdc'}
 order=['pGPU.vhd','psx_gpu_read_math.vhd','mul32u.vhd','mul9s.vhd','divider.vhd','SyncFifo.vhd','SyncFifoFallThrough.vhd']
 order += [n for n in GPU_SOURCES if n not in order]
 for name in order:
  origin=selected(name)
  raw=origin.read_text();changes=[]
  if name=='divider.vhd' and candidate:
   raw=(W/'psx_divider_bank.vhd').read_text()
   changes.append('Private packed dividend/quotient bank; completion timing unchanged')
   if mode=='faultinv':
    marker="r_next(0) := work_q(work_q'left);";assert raw.count(marker)==1
    raw=raw.replace(marker,"r_next(0) := not work_q(work_q'left);");changes.append('Actual candidate-only shifted input bit inversion')
  raw_sha256=hashlib.sha256(raw.encode()).hexdigest()
  text=hardware_text(raw)
  if name in ('gpu_line.vhd','gpu_poly.vhd','gpu.vhd'):
   text,n=re.subn(r'(?m)^begin[ \t]*\n','begin \n',text);assert n==1
   changes.append('Normalize architecture begin whitespace for existing compatibility guards')
  if name=='gpu_poly.vhd':
   assert text.count('abs(')==4 and text.count('x"100000000"')==2
   text=text.replace('abs(','magnitude(').replace('x"100000000"','signed\'(\"000000000\" & x\"100000000\")')
   marker='begin \n';assert text.count(marker)==1
   text=text.replace(marker,'   function magnitude(value : integer) return integer is\n   begin\n      if value < 0 then return -value; else return value; end if;\n   end function;\n\n'+marker)
   marker='to_integer(xStart(43 downto 32)) > drawingAreaRight';assert text.count(marker)==1
   text=text.replace(marker,'xStart(43 downto 32) > signed(resize(drawingAreaRight, 12))')
   text=split_divider_ports(text);changes.append('existing signed/constant/divider GHDL compatibility, exact selected two-constant variant')
  elif name in ('gpu_line.vhd','gpu.vhd'):
   text=gpu_transcode_source(origin.stem,text,True);changes.append('existing divider record compatibility')
  if name=='gpu_videoout.vhd':
   for expr,count in {'to_integer(videoout_request_clkvid.lineDisp)':9,'to_integer(Gun1Y_screen)':2,'to_integer(Gun2Y_screen)':2}.items():
    assert text.count(expr)==count;text=text.replace(expr,"integer'("+expr+")")
   text,n=re.subn(r'(i_pixel_out_x\s*=> )videoout_request_clkvid.xpos,',r"\1integer'(videoout_request_clkvid.xpos),",text);assert n==5
   changes.append('existing explicit integer scalar associations')
  if name=='gpu_videoout_async.vhd':
   # GHDL e589c698c Synth_Attribute_Port asserts on record/output attributes.
   # These two Quartus synthesis hints have no simulation behavior.
   text,n=re.subn(r'(?m)^   attribute altera_attribute of (?:videoout_reports_1x|allowunpause1x) : signal is [^\n]*;\n','',text);assert n==2
   changes.append('Simulation-only omission of two Quartus output-port annotations; preserve all logic and internal attributes')
   text='library pocketip;\n'+text
   for ip in bindings:
    marker='   component '+ip+' is\n';start=text.index(marker);stop=text.index('   end component;',start)+len('   end component;')
    instances=re.findall(r'(\w+)\s*:\s*'+ip+r'\s+port map',text,re.I);assert len(instances)==1
    text,n=re.subn(r'('+re.escape(instances[0])+r'\s*:\s*)'+ip+r'\s+port map',r'\1entity pocketip.'+ip+' port map',text);assert n==1
   changes.append('opaque exact selected CDC port interfaces; actual frozen SV linked')
  text,n=re.subn(r'entity (?:work|mem)\.(?:pocket_dpram_sdp(?:_reverse)?|dpram)\b','entity ramip.dpram',text,flags=re.I)
  text,m=re.subn(r'entity (?:work|mem)\.(RamMLAB|dpram_dif)\b',r'entity ramip.\1',text,flags=re.I)
  if n or m:text='library ramip;\n'+text;changes.append('common opaque pinned RAM behavioral bindings, same clocks/ports')
  p=folder/name;p.write_text(text)
  libs=['work','mem'] if name.startswith('SyncFifo') else ['work']
  for lib in libs:analyze(p,lib)
  sources.append(dict(original=str(origin),original_sha256=digest(origin),path=str(p),sha256=digest(p),raw_pre_compat_sha256=raw_sha256,changes=changes))
 assert {Path(s['original']).name for s in sources}>=set(GPU_SOURCES)
 output=folder/'system.v'
 with output.open('w') as f:subprocess.run([find_tool('ghdl'),'--synth','--std=08','-frelaxed','--vendor-library=ramip','--vendor-library=pocketip','--out=verilog','gpu'],cwd=folder,stdout=f,stderr=log,check=True,timeout=180)
 normalize_generated_verilog(output)
 text=output.read_text();names=re.findall(r'^module (\w+)',text,re.M);prefix='shared__' if candidate else 'original__';expr=re.compile(r'\b('+'|'.join(sorted(names,key=len,reverse=True))+r')\b');renamed=folder/'renamed.v';renamed.write_text(expr.sub(lambda m:prefix+m[0],text));gpu=re.findall(r'^module (gpu(?:_Barch[^\s]*)?)\s*\n',text,re.M);assert len(gpu)==1
 return dict(sources=sources,verilog=str(renamed),sha256=digest(renamed),module=prefix+gpu[0])
log=D/'checks.log'
with log.open('w') as f:
 old=transcode(D/'original',False,f);print('original transcoded',flush=True)
 new=transcode(D/'adapted',True,f);print('candidate transcoded',flush=True)
 top=run_gpu_line_share.bench(D,old['module'],new['module'],timing_prune=True)
 text=top.read_text();a=text.index('integer old_trace,new_trace;');b=text.index('integer checks=0;',a);text=text[:a]+text[b:]
 # Drop only optional debug-only internal probes in the timeout message.
 text=re.sub(r'\$fatal\(1,"GPU timeout program=.*?\);', '$fatal(1,"GPU timeout");',text)
 top.write_text(text)
 sv_names=['psx_video_config_cdc.sv','psx_video_reports_cdc.sv','psx_video_request_cdc.sv','dual_clock_fifo.sv']
 sv=[selected(n) for n in sv_names]
 cmd=[find_tool('verilator'),'--binary','--timing','-O2','-j','2','-Wno-fatal','--output-split','20000','--top-module','tb_gpu_share','--Mdir',str(D/'obj'),old['verilog'],new['verilog'],str(R/'sim/reference/mister_memory_models.sv'),str(top),*map(str,sv)]
 subprocess.run(cmd,cwd=D,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)
 exe=D/'obj/Vtb_gpu_share';r=subprocess.run([str(exe)],cwd=D,stdout=f,stderr=subprocess.STDOUT,timeout=300)
text=log.read_text();positive=r.returncode==0 and 'PASS GPU timing prune' in text and 'PASS GPU cycle outputs' in text and 'PASS GPU readback checks=' in text
if mode=='positive':assert positive,text[-2500:]
else:assert r.returncode!=0 and any(m in text for m in ['GPU cycle output mismatch','Shared GPU VRAM mismatch','Shared GPU oracle word mismatch']),text[-2500:]
assert inputs(P)==originals
for side in (old,new):
 for s in side['sources']:assert digest(Path(s['original']))==s['original_sha256'] and digest(Path(s['path']))==s['sha256']
report=dict(mode=mode,original=old,candidate=new,log=str(log),log_sha256=digest(log),exit_code=r.returncode,expected_verdict_matched=True,exact_selected_GPU_hierarchy=True,actual_selected_CDC_SV=[dict(path=str(p),sha256=digest(p)) for p in sv],common_pinned_RAM_models=True,original_inputs_unchanged=True,fit_verified=False,hardware_verified=False)
(D/'report.json').write_text(json.dumps(report,indent=2)+'\n');print('GPU',mode,'expected verdict matched',r.returncode,flush=True)
