#!/usr/bin/env python3
"""Prepare/compile opt-in audio in the real physical budget top.

Default preset preserves the 1,846-LAB comparison; current adds later
candidate area/timing variants. CPU tag and local VRAM experiments are explicit.
Adds source completion, atomic stereo CDC, sinc conversion and native I2S.
Known host/video/memory CDC and final-topology/service gaps remain. Defaults
to preparation only; --map-only validates elaboration without a long route.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import tempfile

from prepare_full_pocket import prepare_full,VARIANTS,EXTRA_VARIANTS
from prepare_pocket_audio_stream import apply_audio_stream
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool
from verify_pocket_audio_rom import verify as verify_rom


VARIANTS_SELECTED=list(VARIANTS)+['dma-channel-ram','spu-reverb-table','gte-control-ram','gte-matrix-ram',
    'gpu-poly-share','gpu-vertex-ram','gpu-tag-generation','cd-ffvii','gte-multicycle','mdec-idct-pipe',
    'spu-guest-m10k','gpu-mul-narrow','gpu-half-pipe']
VARIANTS_CURRENT=VARIANTS_SELECTED+['gpu-poly-onehot','gte-add-table','gte-vector-ram',
    'dma-chcr-ram','gpu-mul-retime']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--map-only',action='store_true')
    parser.add_argument('--full-fit',action='store_true')
    parser.add_argument('--preset',choices=('baseline','current'),default='baseline')
    parser.add_argument('--cpu-tag-contract',type=Path,help='Complete cached contract; apply the private tag RAM trial')
    parser.add_argument('--vram-response-enable',action='store_true',help='Tested local response enables, with mandatory eight-register map gate')
    parser.add_argument('--exact-reverb',action='store_true',help='Include the opt-in internal 39-tap reverb for an explicit cost measurement')
    parser.add_argument('--release-boot-contract',type=Path,help='Complete release BIOS loader/gate contract; requires APF contract')
    parser.add_argument('--apf-boot-contract',type=Path,help='Pinned APF command-handler boot contract')
    parser.add_argument('--release-no-diagnostics',action='store_true',help='Opt-in host readback/button cleanup; retains CD/card/region service transport')
    parser.add_argument('--host-trim-contract',type=Path,help='Complete release host/button cleanup proof')
    parser.add_argument('--release-init-contract',type=Path,help='Complete lean reset/header/clear comparison and fault proof')
    args=parser.parse_args()
    if args.map_only and args.full_fit:parser.error('Select one compile scope')
    if bool(args.release_boot_contract)!=bool(args.apf_boot_contract):
        parser.error('Release boot needs both loader and APF contracts')
    if args.release_no_diagnostics and not args.release_boot_contract:
        parser.error('Release host cleanup requires tested release boot')
    if args.release_no_diagnostics != bool(args.host_trim_contract):
        parser.error('Host cleanup requires its proof; select both flag and contract')
    parent=ROOT/'build/pocket/audio-stream/runs';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),dir=parent))
    variants=list(VARIANTS_CURRENT if args.preset=='current' else VARIANTS_SELECTED)
    if args.exact_reverb: variants.append('spu-reverb-resample')
    if any(flag not in VARIANTS+EXTRA_VARIANTS for flag in variants):
        raise ValueError('candidate preset contains an unsupported flag')
    manifest=prepare_full(work,variants,True,'area-min-state')
    if args.cpu_tag_contract:
        from prepare_cpu_tag_trial import apply_cpu_tag_trial
        manifest=apply_cpu_tag_trial(manifest,work,args.cpu_tag_contract)
    if args.vram_response_enable:
        from prepare_vram_response_enable import apply_response_enable
        manifest=apply_response_enable(manifest,work)
    manifest=apply_audio_stream(manifest,work)
    if args.release_boot_contract:
        from prepare_pocket_boot_loader import apply_boot_loader
        manifest=apply_boot_loader(manifest,work,args.release_boot_contract,args.apf_boot_contract)
    if args.release_no_diagnostics:
        from prepare_release_host_trim import apply_release_host_trim
        from apply_release_cleanup import validate_cleanup
        _,proof=validate_cleanup(args.host_trim_contract,'host')
        manifest=apply_release_host_trim(manifest,work)
        manifest['release_host_trim']['contract']=proof
    if args.release_init_contract:
        from apply_release_cleanup import apply_release_init
        manifest=apply_release_init(manifest,work,args.release_init_contract)
    manifest['audio_fit_preset']={'name':args.preset,'selected_variants':variants,
        'cpu_tag_trial':bool(args.cpu_tag_contract),'local_vram_response_enables':args.vram_response_enable,
        'exact_internal_reverb':args.exact_reverb,'producer_sha256':digest(Path(__file__))}
    manifest['audio_fit_preset']['release_boot_loader']=bool(args.release_boot_contract)
    manifest['audio_fit_preset']['release_no_diagnostics']=args.release_no_diagnostics
    manifest['audio_fit_preset']['release_lean_init']=bool(args.release_init_contract)
    manifest['limitations']=[x for x in manifest['limitations'] if x!=
        'Audio currently sample-held into 48 kHz I2S; required resampler not yet included']
    if not args.exact_reverb:
        manifest['limitations'].append('Internal reverb retains upstream lightweight filter for this budget comparison; exact 39-tap audio remains a release cost')
    (work/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(work,flush=True)
    if not args.map_only and not args.full_fit:return
    binary=Path(find_tool('quartus_sh')).parent
    report={'manifest':manifest,'steps':[],'fit_verified':False,'timing_verified':False,'hardware_verified':False,
            'tool_version':subprocess.check_output([str(binary/'quartus_sh'),'--version'],text=True)}
    tools=('quartus_map',) if args.map_only else ('quartus_map','quartus_fit','quartus_sta')
    with (work/'quartus.log').open('w') as log:
        for name in tools:
            result=subprocess.run([str(binary/name),'apf_top'],cwd=work,stdout=log,stderr=subprocess.STDOUT,timeout=3600)
            report['steps'].append({'tool':name,'exit_code':result.returncode})
            report['source_identity_verified']=all(digest(Path(s['path']))==s['sha256'] for s in manifest['sources'])
            report['project_identity_verified']=all(digest(work/p)==sha for p,sha in manifest['project_hashes'].items())
            coefficients=manifest['pocket_audio_stream']['coefficients']
            report['coefficient_identity_verified']=digest(Path(coefficients['compact_path']))==coefficients['compact_sha256']
            if name=='quartus_map' and result.returncode==0:report['mapped_rom']=verify_rom(work,coefficients['compact_path'])
            for kind in ('map','fit','sta'):
                path=work/f'output_files/apf_top.{kind}.summary'
                if path.exists():report[kind+'_summary']={'path':str(path),'sha256':digest(path),'text':path.read_text()}
            report['fit_verified']=any(s['tool']=='quartus_fit' and s['exit_code']==0 for s in report['steps']) and all(
                report[k] for k in ('source_identity_verified','project_identity_verified','coefficient_identity_verified'))
            (work/'quartus-report.json').write_text(json.dumps(report,indent=2)+'\n')
            print(name,result.returncode,flush=True)
            if result.returncode or not all(report[k] for k in ('source_identity_verified','project_identity_verified','coefficient_identity_verified')):
                raise SystemExit('Audio top compilation/identity failed; preserve '+str(work))
            if name=='quartus_map' and args.vram_response_enable:
                script=ROOT/'scripts/report_vram_response_enable.tcl'; before=digest(work/'apf_top.qsf')
                check=subprocess.run([str(binary/'quartus_sta'),'-t',str(script),str(work/'map-enable-check'),'post-map'],
                    cwd=work,capture_output=True,text=True,timeout=120)
                output=check.stdout+check.stderr; (work/'map-enable-check.log').write_text(output)
                import re
                counts=re.findall(r'VRAM_LOCAL_ENABLE_REGISTERS (\d+)',output)
                report['post_map_enable_gate']={'passed':check.returncode==0 and counts==['8']
                    and digest(work/'apf_top.qsf')==before,'counts':counts,'exit_code':check.returncode,
                    'script_sha256':digest(script),'log_sha256':digest(work/'map-enable-check.log')}
                (work/'quartus-report.json').write_text(json.dumps(report,indent=2)+'\n')
                if not report['post_map_enable_gate']['passed']:
                    raise SystemExit('Local VRAM mapping failed; fitter must not run')


if __name__=='__main__':main()
