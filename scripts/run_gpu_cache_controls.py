#!/usr/bin/env python3
"""Whole-GPU cache-control cycle/write-port checks, filters and RTL faults.

Both engines retain original MLAB tags and the same generations. Reuses the
prepared-payload fixture with real memory stalls, pause and scanout. No BIOS,
disc, complete release or hardware claim. Physical timing needs a routed fit.
"""
from pathlib import Path
import json,sys,shutil,re,subprocess,tempfile
import run_gpu_payload_timing as whole
import run_gpu_line_share as gpu
from prepare_resource_targets import digest
from prepare_gpu_tag_generation import tag_generation_text
from prepare_gpu_cache_controls import cache_controls_text as adapt
from tool_paths import ROOT,find_tool


def main():
    root=ROOT
    parent=ROOT/'build/sim/gpu-cache-controls';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    protected={str(p):digest(p) for p in [Path(__file__),root/'scripts/prepare_gpu_cache_controls.py',root/'scripts/prepare_gpu_tag_generation.py']}
    archive=work/'driver-inputs';archive.mkdir(exist_ok=False)
    for p,h in protected.items():shutil.copyfile(p,archive/Path(p).name)
    original_prepare=gpu.prepare

    def prepare(directory,**options):
     m=original_prepare(directory,**options);assert directory.name in ('original','adapted')
     for s in list(m['sources']):
      if Path(s['path']).name!='gpu_pixelpipeline.vhd':continue
      p=directory/'cache-controls'/'gpu_pixelpipeline.vhd';p.parent.mkdir()
      text=tag_generation_text(Path(s['path']).read_text())
      if directory.name=='adapted':text=adapt(text)
      assert text.count('itagram : entity ramip.RamMLAB')==1 and 'clock_b => not clk2x' not in text
      assert ('one-hot' in text)==(directory.name=='adapted')
      p.write_text(text)
      m['sources'][m['sources'].index(s)]={**s,'path':str(p),'sha256':digest(p),'selected':s['path'],'selected_sha256':s['sha256']}
      (work/'engine-base.txt').write_text(str(directory.parent.parent)+'\n')
     return m

    gpu.prepare=prepare;original_bench=gpu.bench

    def bench(directory,*args,**kwargs):
     p=original_bench(directory,*args,**kwargs);s=p.read_text();marker='reg toggle=0,sampled=0;';assert s.count(marker)==1
     monitor='''integer tag_write_checks=0,cache_write_checks=0,all_four_lookups=0,filter_mode=0;
    initial if(!$value$plusargs("filter=%d",filter_mode))filter_mode=0;
    always @(negedge clk2x) if(!reset && new_gpu.igpu_pixelpipeline.stage0_valid && new_gpu.igpu_pixelpipeline.stage0_filter)all_four_lookups++;
    always @(posedge clk2x) if(!reset) begin
     if(old_gpu.igpu_pixelpipeline.tag_wren_a!==new_gpu.igpu_pixelpipeline.tag_wren_a)$fatal(1,"Tag write enable mismatch");
     if(old_gpu.igpu_pixelpipeline.tag_wren_a) begin
      if(old_gpu.igpu_pixelpipeline.tag_address_a!==new_gpu.igpu_pixelpipeline.tag_address_a || old_gpu.igpu_pixelpipeline.tag_data_a!==new_gpu.igpu_pixelpipeline.tag_data_a)$fatal(1,"Tag active write mismatch");
      tag_write_checks++;
     end
     if(old_gpu.igpu_pixelpipeline.cache_wren_a!==new_gpu.igpu_pixelpipeline.cache_wren_a)$fatal(1,"Cache write enable mismatch");
     if(old_gpu.igpu_pixelpipeline.cache_wren_a) begin
      if(old_gpu.igpu_pixelpipeline.cache_address_a!==new_gpu.igpu_pixelpipeline.cache_address_a)$fatal(1,"Cache active write mismatch");
      cache_write_checks++;
     end
    end
    '''
     s=s.replace(marker,monitor+marker)
     marker=' $display("INDEXED TEXTURE checks=%d",checks);';assert s.count(marker)==1
     s=s.replace(marker,marker+"\n texturefilter=2'(filter_mode);texturefilterstrength=2'(filter_mode);\n")
     marker=' $display("PASS GPU cycle outputs ticks=%d",cycle_io_ticks);';assert s.count(marker)==1
     s=s.replace(marker,''' if(tag_write_checks<100 || cache_write_checks<100 || (filter_mode!=0 && all_four_lookups<100))$fatal(1,"Cache controls coverage missing");
     $display("CACHE_CONTROLS_COVERAGE filter=%0d tags=%0d cache=%0d four_lookups=%0d",filter_mode,tag_write_checks,cache_write_checks,all_four_lookups);
    '''+marker)
     p.write_text(s);return p

    gpu.bench=bench;original_faults=whole.reject_rtl_faults

    def faults(base):
     p=base/'gpu-oracle';filters=[]
     for mode in [1,2]:
      log=p/f'filter{mode}.log'
      with log.open('w') as f:r=subprocess.run([str(p/'obj/Vtb_gpu_share'),f'+filter={mode}'],cwd=p,stdout=f,stderr=subprocess.STDOUT,timeout=180)
      text=log.read_text();passed=r.returncode==0 and 'PASS GPU cycle outputs' in text and f'CACHE_CONTROLS_COVERAGE filter={mode}' in text
      filters.append(dict(mode=mode,passed=passed,exit_code=r.returncode,output=text,log_sha256=digest(log)));print(mode,passed,text[-1100:],flush=True)
      if not passed:raise ValueError('Filtered cache controls comparison failed')
     (work/'filters.json').write_text(json.dumps(dict(passed=True,runs=filters),indent=2)+'\n')
     # Actual tag-write-address RTL corruption must be caught by the active
     # RAM port monitor, rather than counting a compile failure as detection.
     source=p/'adapted/renamed.v';text=source.read_text();pattern=r'(    tag_address_a = )([^;]+); // \(isignal\)';assert len(re.findall(pattern,text))==1
     changed=re.sub(pattern,lambda m:m[1]+'('+m[2]+") ^ 8'h01; // (isignal)",text)
     case=work/'fault-active-write';case.mkdir();bad=case/'candidate.v';bad.write_text(changed)
     sources=[p/'original/renamed.v',bad,root/'sim/reference/mister_memory_models.sv',p/'tb_gpu_share.sv']
     with (case/'build.log').open('w') as f:subprocess.run([find_tool('verilator'),'--binary','--timing','-O3','-j','4','-Wno-fatal','--output-split','20000','--top-module','tb_gpu_share','--Mdir',str(case/'obj'),*map(str,sources)],cwd=case,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)
     with (case/'run.log').open('w') as f:r=subprocess.run([str(case/'obj/Vtb_gpu_share')],cwd=case,stdout=f,stderr=subprocess.STDOUT,timeout=180)
     text=(case/'run.log').read_text();passed=r.returncode!=0 and 'Tag active write mismatch' in text
     (case/'report.json').write_text(json.dumps(dict(passed=passed,rejected=passed,exit_code=r.returncode,source_sha256=digest(bad),log_sha256=digest(case/'run.log'),output=text),indent=2)+'\n');print('Active-write RTL fault rejected',passed,text[-800:],flush=True)
     if not passed:raise ValueError('Active tag-write fault missed')
     return original_faults(base)

    whole.reject_rtl_faults=faults
    try:whole.main()
    finally:
     gpu.prepare=original_prepare
     gpu.bench=original_bench
     whole.reject_rtl_faults=original_faults
    assert all(digest(Path(p))==h for p,h in protected.items())
    base=Path((work/'engine-base.txt').read_text().strip());r=json.loads((base/'report.json').read_text());assert r['passed'] and r['source_identity_verified']
    (work/'report.json').write_text(json.dumps(dict(passed=True,source_identity_verified=True,source_sha256=protected,whole_gpu=dict(path=str(base/'report.json'),sha256=digest(base/'report.json')),filters=dict(path=str(work/'filters.json'),sha256=digest(work/'filters.json')),active_write_fault=dict(path=str(work/'fault-active-write/report.json'),sha256=digest(work/'fault-active-write/report.json')),tag_storage='Both reference and candidate retain original RamMLAB; both same8-bit generations',logical_cycles_added=0,hardware_verified=False),indent=2)+'\n')


if __name__=='__main__':main()
