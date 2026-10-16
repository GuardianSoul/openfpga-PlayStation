"""Diagnostic only: authored CD oracle with bounded, skewed payload wires.

Production RTL is untouched. This models digital propagation, not analog
metastability, and does not qualify a pin or a routed build.
"""
from pathlib import Path
import sys,json,re,subprocess,argparse,tempfile
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'scripts'))
from prepare_resource_targets import digest
from psx_disc_host import metadata_words,multitrack_metadata_words
from tool_paths import find_tool
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path)
parser.add_argument('--libcrypt',action='store_true',help='Exercise slot 99 and delayed 12-bit mount length')
parser.add_argument('--prepared-cdc',action='store_true',help='Preserve the existing 23 synchronizer flops as the before-map helper does')
args=parser.parse_args()
if args.output:
 D=args.output.resolve()
 if not D.is_relative_to(R/'build') or D.exists():raise ValueError('Fresh ignored build output required')
 D.mkdir(parents=True)
else:
 parent=R/'build/sim/cd-bundle-delay';parent.mkdir(parents=True,exist_ok=True)
 D=Path(tempfile.mkdtemp(prefix='checks-',dir=parent))
print(D,flush=True)
source=R/'rtl/pocket/psx_cd_stream.sv';bench=R/'sim/pocket/tb_cd_stream.sv'
if digest(source) not in {'eaaa6978025b0f9a8afb84f6f407b37ccb626648804870710fa2c7779281a5dd',
                         '10717e58e9c4e3d36dcfe36964d15920a899814a5ebcd09246f4ef498b0d909a'}:
 raise ValueError('CD source has not been reviewed for the delay model')
if args.libcrypt or args.prepared_cdc:
 from prepare_pocket_cd_cdc import CD_SHA,preserve_sync_flops
 if digest(source)!=CD_SHA:raise ValueError('LibCrypt/preservation requires the reviewed current source')
paths=[source,bench,R/'scripts/psx_disc_host.py',R/'references/MIRLO64/target/pocket/core_bridge_cmd.v',R/'references/MIRLO64/platform/pocket/common.v',R/'sim/pocket/mf_datatable_model.sv']
before={str(p):digest(p) for p in paths}
for name,n in [('meta-a.hex',40),('meta-b.hex',9),('meta-a2.hex',30),('meta-big.hex',317787)]:
 (D/name).write_text(''.join(f'{x:08x}\n' for x in metadata_words(n,region=2)))
(D/'meta-mt.hex').write_text(''.join(f'{x:08x}\n' for x in multitrack_metadata_words([20,162,155],region=2)))
prepared=preserve_sync_flops(source.read_text()) if args.prepared_cdc else source.read_text()
header,host=prepared.split('    // ============================ host side =============================',1)
host,core=host.split('    // ============================ core side =============================',1)
field_groups={
 'host':[('req_lba','20'),('sel_slot','16')],
 'core':[('m_valid','1'),('m_end','20'),('m_minutes','8'),('m_seconds','8'),('m_ntracks','TB+1'),('m_slot','16'),('resp_buf','SB'),('resp_kind','2')]
}
if args.libcrypt:field_groups['core'].append(('m_lc_bytes','12'))
def delayed(fault=None):
 aliases=[];parts=dict(host=host,core=core)
 for domain,fields in field_groups.items():
  for index,(name,width) in enumerate(fields):
   alias=f'bundle_{domain}_{name}'
   parts[domain],count=re.subn(r'\b'+name+r'\b',alias,parts[domain]);assert count>0,(domain,name)
   aliases.append(f'    wire [{width}-1:0] {alias};')
   if name=='m_valid':
    aliases.append(f'    assign #(13.0) {alias}={name};')
   else:
    late=('80.0' if fault=='request' and name=='req_lba' else
          '200.0' if fault=='length' and name=='m_lc_bytes' else
          f'(13.0*((bit_{alias}+{index})%4)/3.0)')
    aliases.append(f'    for(genvar bit_{alias}=0;bit_{alias}<{width};bit_{alias}++) begin: delays_{alias}\n'
                   f'        assign #({late}) {alias}[bit_{alias}]={name}[bit_{alias}];\n    end')
 return '`timescale 1ns/1ps\n'+header+'\n'+'\n'.join(aliases)+'\n'+parts['host']+'\n'+parts['core']
tb=bench.read_text();old=' always #14.763 cclk=~cclk;';assert tb.count(old)==1
tb=tb.replace(old,''' real bundle_phase=0;
 initial begin
  if($value$plusargs("CORE_PHASE=%f",bundle_phase)) begin end
  #(bundle_phase);forever #14.763 cclk=~cclk;
 end''')
# In the direct fixture case 5's manual lease release originally starts only
# AFTER select returns. With SBI, select itself needs that port. Release the
# old lease independently, just as the real arbiter does; retain all checks.
# Claude's original LibCrypt suite uses the arbiter fixture, without this
# direct-only ready_hold dependency. No shared bench/RTL is changed here.
lease_before='''    select(16'h20);if(!has_disc) $fatal(1,"Remount after reset");
    fork
     begin wait(!apf_serving);repeat(30) @(negedge hclk);ready_hold=0;end
     begin fetch(16'h20,sectors_a,170,0);end
    join'''
lease_after='''    fork
     begin wait(!apf_serving);repeat(30) @(negedge hclk);ready_hold=0;end
     begin
      select(16'h20);if(!has_disc) $fatal(1,"Remount after reset");
      fetch(16'h20,sectors_a,170,0);
     end
    join'''
if args.libcrypt:
 if tb.count(lease_before)!=1:raise ValueError('Direct fixture lease-release anchor changed')
 tb=tb.replace(lease_before,lease_after,1)
(D/'tb.sv').write_text(tb)
runs=[]
profiles=[('bounded13',None),('late-request80','request')]
if args.libcrypt:profiles.append(('late-length200','length'))
positive_cases=[0,4,5,6,7,8] if args.libcrypt else [0,4,6,7]
expected_runs=len(positive_cases)*4+len(profiles)-1
for label,fault in profiles:
 directory=D/label;directory.mkdir();rtl=directory/'stream.sv';rtl.write_text(delayed(fault))
 cmd=[find_tool('verilator'),'--binary','--timing','-O3','-j','4','-Wno-fatal','+define+CD_STREAM_TRACKS=19',*(["+define+CD_STREAM_LC=16'h63"] if args.libcrypt else []),'--top-module','tb_cd_stream','--Mdir',str(directory/'obj'),str(rtl),str(D/'tb.sv'),*map(str,paths[3:])]
 with (directory/'build.log').open('w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,timeout=300,check=True)
 for phase in ([0,.1,6.7,13.4] if not fault else [0]):
  for case in (positive_cases if not fault else [8 if fault=='length' else 0]):
   p=subprocess.run([str(directory/'obj/Vtb_cd_stream'),f'+CASE={case}',f'+CORE_PHASE={phase}'],cwd=D,capture_output=True,text=True,timeout=180)
   out=p.stdout+p.stderr;log=directory/f'phase{phase}-case{case}.log';log.write_text(out)
   marker='Track table word' if fault=='length' else 'Sector data mismatch'
   ok=(p.returncode!=0 and marker in out) if fault else (p.returncode==0 and out.count('PASS CD STREAM')==1 and not re.search(r'%Fatal|%Error|Assertion failed',out))
   runs.append(dict(label=label,phase_ns=phase,case=case,fault=fault,exit_code=p.returncode,passed=ok,log_sha256=digest(log),rtl_sha256=digest(rtl)))
   print(label,phase,case,ok,out.strip()[-180:],flush=True)
   if not ok:break
  if not all(x['passed'] for x in runs):break
 if not all(x['passed'] for x in runs):break
identity=all(digest(Path(p))==h for p,h in before.items())
report=dict(passed=identity and len(runs)==expected_runs and all(x['passed'] for x in runs),source_identity_verified=identity,original_sources=before,runs=runs,maximum_valid_payload_delay_ns=13,per_bit_skew_modelled=True,toggle_delivery_unmodified=True,clock_phases_ns=[0,.1,6.7,13.4],LIBCRYPT_SLOT=99 if args.libcrypt else 0,synchronizer_preservation=args.prepared_cdc,libcrypt_length_bits_delayed=12 if args.libcrypt else 0,direct_fixture_releases_old_lease_independently=args.libcrypt,copied_bench_sha256=digest(D/'tb.sv'),analog_metastability_modelled=False,physical_fit_verified=False,hardware_verified=False)
(D/'report.json').write_text(json.dumps(report,indent=2)+'\n');print('FINAL',report['passed'],D/'report.json',flush=True)
if not report['passed']:raise SystemExit(1)
