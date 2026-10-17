#!/usr/bin/env python3
"""Prove CD sector streaming from APF data slots with authored disc images."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
from prepare_resource_targets import digest
from psx_disc_host import metadata_words,multitrack_metadata_words
from tool_paths import ROOT,find_tool

CASES={0:'pregap, prefetch, seeks, end, swaps, absent slot',1:'slow APF (up to 0.8 ms per sector), shuffled and stray staging writes',
    2:'APF read error, first mount from a slot update',3:'APF slot reload then swap',4:'selections racing reads',5:'transport reset with the APF port still leased',
    6:'multi-track disc (MAX_TRACKS=4): track table, boundaries, seeks, partial track',
    7:'0x8A right after the first mount remounts at once; after a read it waits for the swap',
    8:'LibCrypt build (LIBCRYPT_SLOT=0x63): each mount reads the .sbi, mask 0x8401 in track word 3, buffer 0 reused',
    9:'one-file build (TABLE_OFFSET=0x40): track table file per mount (LibCrypt mask in word 3), none, wrong size, failed read, last buffer reused, reads racing a remount'}
FAULTS=[
    ('pregap-short',"next_fetch<PREGAP","next_fetch<20'd149",'APF read outside the image'),
    ('offset-wrong',"mul_x*32'd2352","mul_x*32'd2336",'APF sector read shape'),
    ('old-disc-buffers',"for(i=0;i<NBUF;i=i+1) begin bstate[i]<=B_EMPTY;bstale[i]<=0;end","",'Sector data mismatch'),
    ('halfword-order',"(halfword[0] ? q[31:16] : q[15:0])","(halfword[0] ? q[15:0] : q[31:16])",'Sector data mismatch'),
    ('early-change-done',"if(m_valid) cstate<=LIBCRYPT_SLOT!=0 && m_lc_bytes!=0 ? C_LC_ADDR : C_TRACK;",
     "if(m_valid) begin cstate<=LIBCRYPT_SLOT!=0 && m_lc_bytes!=0 ? C_LC_ADDR : C_TRACK;disc_change_done<=selection_current;end",
     'Track table word'),
    ('reload-remounts-at-once',"pending_file<=1;disc_valid<=0;","pending_file<=1;disc_valid<=0;mount_pending<=1;have_size<=1;",'Slot reload remounted without a swap'),
    ('libcrypt-lane',"lc_msf<=lc_b[1] ? {q[23:16],q[31:24],8'h00} : q[23:0];","lc_msf<=lc_b[1] ? {q[15:8],q[23:16],8'h00} : q[23:0];",'Track table word'),
    ('table-word3-from-file',"trackinfo_addr==3 ? {14'b0,host_region,q[15:0]} : q;","q;",'Track table word'),
    ('table-any-size',"side_tb ? table_q==32'd1600 :","side_tb ? table_q!=0 :",'APF sector read shape'),
    ('table-buffer-reuse',"!(i==NBUF-1 && tb_hold)","1'b1",'Track table word'),
    ('table-addressed-late',"rd_addr<=rd_addr+1'b1;cstate<=C_TRACK;end","cstate<=C_TRACK;end",'Track table word'),
    ('table-error-kept',"if(lc_cmd && side_tb) tb_ok<=0; else","if(lc_cmd && side_tb) ; else",'Track table word'),
    ('libcrypt-buffer-reuse',"!(i==0 && lc_hold)","1'b1",'Track table word'),
    ('early-update-waits',"if(mounted_once && read_since_mount) begin","if(mounted_once) begin",'Early slot update left the disc pending'),
    ('done-before-accept',"wire done_window=accepted;","wire done_window=1'b1;",'Sector data mismatch'),
    ('ignore-table-grant',"t_wdata<=disc_end;end\n                        if(!table_grant) twait<=0;","t_wdata<=disc_end;end\n                        if(0) twait<=0;",'%Fatal'),
    ('ignore-apf-error',"bkind[cmd_buf]<=target_error!=0 ? K_FILL : K_DATA;","bkind[cmd_buf]<=K_DATA;",'Sector data mismatch'),
    # Multi-track (built with MAX_TRACKS=4, case 6)
    ('track-search-stuck',"f_lba>=f_q) begin f_k<=f_k+1'b1;","f_lba>=f_q) begin f_k<=f_k;",'%Fatal'),
    ('index-time-missing',"if(mtrack!=0 && track_sectors==PREGAP[7:0]) begin t_we<=1;t_waddr<={mtrack,2'd2};t_wdata<={4'b0,m_minutes,m_seconds};end","",'Track table word'),
    ('index-count-early',"if(track_sectors!=8'hff) track_sectors<=track_sectors+1'b1;","if(track_sectors!=8'hff) track_sectors<=track_sectors+2'd2;",'%Fatal'),
    ('track-pregap-ignored',"t_waddr<={mtrack,2'd0};t_wdata<=disc_end;","t_waddr<={mtrack,2'd0};t_wdata<=disc_end+PREGAP;",'%Fatal'),
]
MULTI_FAULTS={'track-search-stuck','index-time-missing','index-count-early','track-pregap-ignored'}


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--side-slot',action='store_true',help='Exercise the optional constant side-file selector')
    args=parser.parse_args()
    parent=ROOT/'build/sim/cd-stream';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    for name,sectors in [('meta-a.hex',40),('meta-b.hex',9),('meta-a2.hex',30),('meta-big.hex',317787)]:
        (work/name).write_text(''.join(f'{w:08x}\n' for w in metadata_words(sectors,region=2)))
    (work/'meta-tb.hex').write_text(''.join(f'{(0x5eed8401+i*0x9e377900)&0xffffffff:08x}\n' for i in range(400)))
    (work/'meta-mt.hex').write_text(''.join(f'{w:08x}\n' for w in multitrack_metadata_words([20,162,155],region=2)))
    sources=[ROOT/'rtl/pocket/psx_cd_stream.sv',ROOT/'sim/pocket/tb_cd_stream.sv',
             ROOT/'references/MIRLO64/target/pocket/core_bridge_cmd.v',
             ROOT/'references/MIRLO64/platform/pocket/common.v',ROOT/'sim/pocket/mf_datatable_model.sv']
    protected={str(p):digest(p) for p in [*sources,Path(__file__),ROOT/'scripts/psx_disc_host.py']}
    if args.side_slot:
        from prepare_cd_side_slot import side_slot_text
        protected[str(ROOT/'scripts/prepare_cd_side_slot.py')]=digest(ROOT/'scripts/prepare_cd_side_slot.py')
        candidate=work/'psx_cd_stream.sv';candidate.write_text(side_slot_text(sources[0].read_text()))
        sources[0]=candidate;protected[str(candidate)]=digest(candidate)
    arbiters=[ROOT/'rtl/pocket/psx_apf_target_arb.sv',ROOT/'rtl/pocket/psx_apf_table_arb.sv']
    protected.update({str(p):digest(p) for p in arbiters})
    def build(name,selected,arb=False,nbuf=2,tracks=1,lc=False,tb=False):
        directory=work/name;directory.mkdir()
        with (directory/'build.log').open('w') as log:
            subprocess.run([find_tool('verilator'),'--binary','--timing','-O3','-j','4','-Wno-fatal',
                *(['+define+CD_STREAM_ARB'] if arb else []),f'+define+CD_STREAM_NBUF={nbuf}',f'+define+CD_STREAM_TRACKS={tracks}',*(["+define+CD_STREAM_LC=16'h63"] if lc else []),*(["+define+CD_STREAM_TB=16'h40"] if tb else []),*(map(str,arbiters) if arb else []),
                '--top-module','tb_cd_stream','--Mdir',str(directory/'obj'),*map(str,selected)],
                cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
        return directory/'obj/Vtb_cd_stream'
    def run(binary,case,log):
        result=subprocess.run([str(binary),f'+CASE={case}'],cwd=work,capture_output=True,text=True,timeout=900)
        log.write_text(result.stdout+result.stderr);return result.returncode,log.read_text()
    binaries={'direct':build('original',sources),'arbiter':build('arbiter',sources,True),
        'four-buffers':build('four-buffers',sources,True,4),'multi-track':build('multi-track',sources,True,2,4),
        'multi-track-19':build('multi-track-19',sources,True,2,19),
        'libcrypt-19':build('libcrypt-19',sources,True,2,19,True),
        'one-file':build('one-file',sources,True,2,1,False,True)};runs=[];faults=[]
    for mode,binary in binaries.items():
        for case in CASES:
            if case==6 and not mode.startswith(('multi-track','libcrypt')):continue
            if case==8 and not mode.startswith('libcrypt'):continue
            if case==9 and mode!='one-file':continue
            code,output=run(binary,case,work/f'{mode}-case-{case}.log')
            passed=code==0 and output.count('PASS CD STREAM')==1 and not re.search(r'%Fatal|%Error|Assertion failed',output)
            runs.append(dict(mode=mode,case=case,scope=CASES[case],passed=passed,exit_code=code,output=output.strip()[-600:],
                log_sha256=digest(work/f'{mode}-case-{case}.log'),binary_sha256=digest(binary)))
            print(mode,case,passed,output.strip()[-300:],flush=True)
            if not passed:break
        if not all(r['passed'] for r in runs):break
    expected_runs=3*(len(CASES)-3)+2*(len(CASES)-2)+(len(CASES)-1)+(len(CASES)-2)
    if len(runs)==expected_runs and all(r['passed'] for r in runs):
        for name,before,after,marker in FAULTS:
            if args.side_slot:
                before=before.replace('side_tb','side_table');after=after.replace('side_tb','side_table')
            text=sources[0].read_text()
            if text.count(before)!=1:raise ValueError('Fault anchor changed: '+name)
            variant=work/(name+'.sv');variant.write_text(text.replace(before,after))
            multi=name in MULTI_FAULTS;lc=name.startswith('libcrypt');tb=name.startswith('table-')
            faulty=build(name,[variant,*sources[1:]],name=='ignore-table-grant' or multi or lc or tb,2,4 if multi else 19 if lc else 1,lc,tb)
            case=4 if name=='libcrypt-buffer-reuse' else 9 if tb else 8 if lc else 6 if multi else {'ignore-apf-error':2,'reload-remounts-at-once':3,'done-before-accept':5,'early-update-waits':7}.get(name,0)
            code,output=run(faulty,case,work/(name+'.log'))
            rejected=code!=0 and marker in output
            faults.append(dict(name=name,case=case,rejected=rejected,exit_code=code,output=output.strip()[-400:],
                source_sha256=digest(variant)))
            print(name,rejected,output.strip()[-200:],flush=True)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report=dict(passed=identity and len(runs)==expected_runs and all(r['passed'] for r in runs) and
            len(faults)==len(FAULTS) and all(f['rejected'] for f in faults),
        source_identity_verified=identity,source_sha256=protected,runs=runs,faults=faults,
        constant_side_slot=args.side_slot,
        modes=dict(direct='pinned handler only',arbiter='psx_apf_target_arb and psx_apf_table_arb with a card write-back client',
            **{'four-buffers':'arbiter build with NBUF=4','multi-track':'arbiter build with MAX_TRACKS=4, all cases plus case 6','multi-track-19':'arbiter build with MAX_TRACKS=19 (release), all cases plus case 6'}),
        bridge_clock_MHz=74.25,cd_clock_MHz=33.8688,buffers=2,sector_bytes=2352,
        metadata_oracle='scripts/psx_disc_host.py metadata_words, multitrack_metadata_words',
        controller_connected=False,full_top_connected=False,sd_firmware_modelled=False,hardware_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)

if __name__=='__main__':main()
