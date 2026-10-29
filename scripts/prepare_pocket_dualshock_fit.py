#!/usr/bin/env python3
"""Reproduce the budget overlays for the current 0.5.3 DualShock/stick build.

Input is the fresh prepare_pocket_early release recipe with --dualshock
--stick-settings --dpram-sdp --spu-cache --spu-cache-slim --spu-free-slot.
The released0.5.3/rc8 cache is mandatory for this0.5.4 feature target.
This selects existing qualified transformations; no production default or
whole-core timing/hardware qualification is implied. Must run before map.
--sort-reread applies the reviewed vertex reread AFTER the divider guards;
it adds four GPU clocks per triangle. Do not select it in the input recipe.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from measure_pocket_seeds import inputs
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import UPSTREAM, digest
from tool_paths import ROOT

CPU_SHA='e238aa73e7ae5617e5686bf636b68eb7f453336d63f844eafdde34c12d38efba'
SPU_SHA='6399255d10dcabc98068bac155c0124e1f49bbcd67843f46b2d52ac7cf51bd22'
CPU_PRODUCT_SHA='e7fac3b0ebed127f8ff2f6b763419e6dbe92c84a54c94f1440470a57c95cdf65'
SPU_QUEUE_SHA='169813eeab5a521bc897cc7a5c64923571a4fd52f2454ab49758ce27d11361be'
# rtl/pocket/joypad_pad_table.vhd (prepare --pad-table, make test-joypad-pad-table)
PAD_TABLE_SHA='2f845f92c56c82fde1d933c4889a89968628246009e384fb969c5b87c61951e6'
# 2e9058d: each port keeps its DualShock state, as upstream (--pad2; lockstep
# with the pinned MiSTer pad in make test-joypad-pad-table / -dualshock-lean)
PAD_TABLE_SHAS=(PAD_TABLE_SHA,'b6788a427cdd0d165966995a41ab8fba70a957a5726650b27ac0d09c40b13136',
               # 04403537: the same with DIGITAL_HOLD (Parasite Eve USA, prepare --digital-hold)
               '04403537ac8315538353c87caf327b044417be33b1a779de5252080a81661f0d')

def once(t,a,b):

 if t.count(a)!=1:raise ValueError('CPU product anchor changed: '+a[:90])
 return t.replace(a,b,1)
def product_text(t):
 if hashlib.sha256(t.encode()).hexdigest()!=CPU_SHA:raise ValueError('Unreviewed DualShock-fit CPU')
 t=once(t,'   signal mulResultS                   : signed(63 downto 0);\n   signal mulResultU                   : unsigned(63 downto 0);',
 '''   -- One product for MULT/MULTU; launch-latched signedness, original wait.
   signal mulSigned                    : std_logic := '0';
   signal mulResult                    : signed(65 downto 0);''')
 t=once(t,'            mulResultS <= signed(mul1) * signed(mul2);\n            mulResultU <= mul1 * mul2;',
 '            mulResult <= signed((mulSigned and mul1(31)) & mul1) * signed((mulSigned and mul2(31)) & mul2);')
 t=once(t,'when HILOCALC_MULT  => hi <=   unsigned(mulResultS(63 downto 32));  lo <=    unsigned(mulResultS(31 downto 0));',
 'when HILOCALC_MULT  => hi <= unsigned(mulResult(63 downto 32)); lo <= unsigned(mulResult(31 downto 0));')
 t=once(t,'when HILOCALC_MULTU => hi <=            mulResultU(63 downto 32);   lo <=             mulResultU(31 downto 0);',
 'when HILOCALC_MULTU => hi <= unsigned(mulResult(63 downto 32)); lo <= unsigned(mulResult(31 downto 0));')
 for op,flag in [('MULT','1'),('MULTU','0')]:
  anchor=f"                  if (EXEcalc{op} = '1') then\n                     hilocalc <= HILOCALC_{op};"
  t=once(t,anchor,anchor+f"\n                     mulSigned <= '{flag}';")
 if 'mulResultS' in t or 'mulResultU' in t:raise ValueError('Unconverted CPU product consumer')
 return t


def queue_text(source):
    if hashlib.sha256(source.encode()).hexdigest()!=SPU_SHA:raise ValueError('Unreviewed DualShock-fit SPU')
    p=re.compile(r'   icdq : entity (\w+)\.RamMLAB\n   GENERIC MAP \(width => 32, widthad => 4, width_byteena => 1\)\n   PORT MAP \(\n      inclock    => clk1x,\n      wren       => cdq_wren,\n      data       => std_logic_vector\(cd_left\) & std_logic_vector\(cd_right\),\n      wraddress  => std_logic_vector\(cdq_wp\),\n      rdaddress  => std_logic_vector\(cdq_rp\),\n      q          => cdq_head\n   \);')
    matches=list(p.finditer(source));assert len(matches)==1
    m=matches[0];lib=m[1]
    new=f'''   icdq : entity {lib}.pocket_dpram_sdp
   GENERIC MAP (addr_width => 4, data_width => 32)
   PORT MAP (
      clock_a    => clk1x,
      address_a  => std_logic_vector(cdq_wp),
      data_a     => std_logic_vector(cd_left) & std_logic_vector(cd_right),
      wren_a     => cdq_wren,
      q_a        => open,
      clock_b    => clk1x,
      address_b  => std_logic_vector(cdq_rp),
      data_b     => (others => '0'),
      wren_b     => '0',
      q_b        => cdq_head
   );'''
    return source[:m.start()]+new+source[m.end():]


def prepare(project, *, sort_reread=False, gpu_recip=False, gpu_poly_stepadd=False, gpu_poly_diffmux=False):
    from prepare_divider_bank import prepare_project as divider
    from prepare_cd_side_slot import prepare_project as side
    from prepare_gte_mac_ram import prepare as mac
    from prepare_gpu_release_watchdogs import prepare_project as watchdog
    from prepare_cpu_execute_share import prepare_project as execute
    from prepare_pocket_cpu_hold import prepare_project as hold

    project=Path(project).resolve()
    if (not project.is_relative_to(ROOT/'build') or subprocess.run(
            ['git','check-ignore','--quiet',str(project/'dualshock-fit/manifest.json')],cwd=ROOT).returncode):
        raise ValueError('Ignored build project required')
    for name in ('db','incremental_db','output_files','dualshock-fit','divider-bank',
                 'cd-side-slot','gte-mac-ram','gpu-release-watchdogs','cpu-execute-share','cpu-hold-margin','gpu-recip'):
        if (project/name).exists():raise ValueError('Fresh project required: '+name)
    if not json.loads((project/'manifest.json').read_text()).get('reset_only'):
        raise ValueError('Reset-only build required')
    qsf=project/'apf_top.qsf'
    selected=assignments(project,qsf.read_text())
    cache_paths={p for _,p in selected if p.name=='psx_spu_ch3_cache.sv'}
    # b81de5f1: the same cache with the CHEAT port (off unless prepare --cheats);
    # 9bb0744e: plus VOICE_SETS (533c49b, default 0 = the address hash, so unchanged until prepare_spu_voice_sets.py)
    if len(cache_paths)!=1 or digest(next(iter(cache_paths))) not in ('1e65c93531ea81962f7eaf33d3ee5e0531629c754bfea41cb28999dd396e1911',
                                                                      'b81de5f149563ddd9f4ac1c7364e90bfe7be390214af1b8d7683718eeba965c2',
                                                                      '9bb0744e473b86b416319765ae6fc61d256994100c2603b6acc3ec55fac0c9ea'):
        raise ValueError('Released rc8 slim SPU cache and free-slot profile required')
    def one(name):
        paths={p for _,p in selected if p.name==name}
        if len(paths)!=1:raise ValueError('Expected one selected '+name)
        return paths.pop()
    def pad_digest():
        # prepare --analog-setting selects a copy whose only change is the
        # ANALOG_LEVEL default (true); check it as the reviewed pad.
        names={p.name for _,p in selected}
        if 'joypad_pad_table.vhd' in names:
            # prepare --pad-table: the table pad with the replaced pad's
            # STICK_DPAD/ANALOG_LEVEL defaults; checked with both at false.
            text=one('joypad_pad_table.vhd').read_text()
            text=re.sub(r'(STICK_DPAD|ANALOG_LEVEL|DIGITAL_HOLD) : boolean := true',r'\1 : boolean := false',text)
            sha=hashlib.sha256(text.encode()).hexdigest()
            return sha if sha in PAD_TABLE_SHAS else None
        if 'joypad_pad_dualshock_analog.vhd' in names and 'joypad_pad_dualshock.vhd' not in names:
            text=one('joypad_pad_dualshock_analog.vhd').read_text()
            if text.count('ANALOG_LEVEL : boolean := true')!=1: return None
            return hashlib.sha256(text.replace('ANALOG_LEVEL : boolean := true','ANALOG_LEVEL : boolean := false').encode()).hexdigest()
        return digest(one('joypad_pad_dualshock.vhd'))
    cpu,spu=one('cpu.vhd'),one('spu.vhd')
    cpu_text,spu_text=product_text(cpu.read_text()),queue_text(spu.read_text())
    if hashlib.sha256(cpu_text.encode()).hexdigest()!=CPU_PRODUCT_SHA or hashlib.sha256(spu_text.encode()).hexdigest()!=SPU_QUEUE_SHA:
        raise ValueError('Budget emission differs from measured sources')
    core=one('core_top.sv').read_text()
    analog_sha=digest(one('psx_pocket_analog.sv'))
    if analog_sha not in ('7ba041d832cb9209dfed45642caa2995dfb7fb6ff16d07a7859a6263e270d3e7','c329bb75dab0a24687d365aa1852333a52a462c619d610a5850dd9a6f7745a6a','81ee3571b73e1825701badf2459df8ef4f290b841419ac6bfcb3dbbeb0de00f9','ca29019c8fa94d791b63eb9529785214a14ab9345f878c7b11c9e66e173bd033',
                         # 92c073e0: ca29019c with type 3 sticks checked as type 2 (#19)
                         '92c073e06aff9ca4d515ed26bafe17531723e5db1da063fcb730c3be539d8fb7'):
        raise ValueError('Unreviewed analog transport')
    if ('psx_pocket_stick_map stick_map' not in core or not any(tag in core for tag in ('psx_pocket_analog pad_analog','psx_pocket_analog #(.PASS_CONFIG(1)')) or
            # 83d3cc78: the same pad with the ANALOG_LEVEL generic (--analog-setting, 77d5f25)
            pad_digest() not in (*PAD_TABLE_SHAS, '18b893b42b224e17b9d2b3963a60d1e1d86436b488aaba3094da34ce5cb78ebc',
                                 '83d3cc782c51427806a58793f1168396274e9239ddec471fe4ace34fd031045e',
                                 # 8cbbc514: 83d3cc78 with a name dropped from one comment (72cefc8)
                                 '8cbbc514d567d3ac59e34a6cad97868bcac24d26d597abef501416302cca35ad',
                                 # 964fd214: per-port DualShock state (2e9058d)
                                 '964fd214d71db2a712cb2e1451b1188a1d387dd57811824a150d8e65dc7ebd40',
                                 # 3ed122d6: DIGITAL_HOLD (Parasite Eve USA, prepare --digital-hold)
                                 '3ed122d6bcb0e11a729f1058193a6db20ab0a943985049525e4361dc9c8d07e3')):
        raise ValueError('Current DualShock and stick-settings profile required')
    atomic='psx_pocket_analog #(.PASS_CONFIG(1)' in core
    config_bits=16 if 'wire [15:0] pad_stick_config_in' in core else 15 if 'wire [14:0] pad_stick_config_in' in core else 13 if 'wire [12:0] pad_stick_config_in' in core else 4 if 'wire [3:0] pad_stick_config_in' in core else 0
    # 15 bits: the A/B/X/Y stick list (Claude, 2026-10-17) on the CONFIG_W transport;
    # 16: SELECT + D-Pad widened to 3 bits for its Face Buttons option (same transport).
    if atomic and ((config_bits in (15,16) and analog_sha not in ('ca29019c8fa94d791b63eb9529785214a14ab9345f878c7b11c9e66e173bd033','92c073e06aff9ca4d515ed26bafe17531723e5db1da063fcb730c3be539d8fb7')) or
                   (config_bits==13 and analog_sha not in ('81ee3571b73e1825701badf2459df8ef4f290b841419ac6bfcb3dbbeb0de00f9','ca29019c8fa94d791b63eb9529785214a14ab9345f878c7b11c9e66e173bd033','92c073e06aff9ca4d515ed26bafe17531723e5db1da063fcb730c3be539d8fb7')) or
                   (config_bits==4 and analog_sha!='c329bb75dab0a24687d365aa1852333a52a462c619d610a5850dd9a6f7745a6a') or config_bits==0):
        raise ValueError('Input-settings packet/transport version mismatch')
    sort_source=None
    sort_text=None
    if sort_reread:
        from prepare_gpu_sort_reread import sort_reread_text
        sort_source=one('gpu_poly.vhd')
        if digest(sort_source)!='f169f05c98a8d714c881568a44cdc0b7c2d6eb864808510f6c9c9f2af8cf5d90':
            raise ValueError('Unreviewed DualShock sorted-reread polygon source')
        sort_text=sort_reread_text(sort_source.read_text())
        if hashlib.sha256(sort_text.encode()).hexdigest()!='b9873962f71dfb78522a39079025800c5a982eb8c4f51bb5f6884ba59aa88796':
            raise ValueError('Sorted-reread emission differs from checked source')
    original=inputs(project)
    work=project/'dualshock-fit';work.mkdir()
    report=dict(original_inputs=original,overlays={},additional_CPU_hold_ns=.15,
        production_selected=False,fit_verified=False,timing_verified=False,hardware_verified=False,
        analog_source_sha256=analog_sha,atomic_stick_config=atomic,input_settings_bits=config_bits,
        cache_enabled=True,cache_free_slot=True,cache_source_sha256=digest(next(iter(cache_paths))),
        extra_GPU_clocks_per_triangle=4 if sort_reread else 0,
        scope='Released0.5.3/rc8 slimSPUcache/free-slot + DualShock/stick-settings; existing budget transformations, features retained. Optional sorted reread adds4GPUclocks/triangle. Inherited release exceptions remain, not strict signoff.')
    report['overlays']['divider']=divider(project)
    report['overlays']['side']=side(project)
    q=qsf.read_text()
    for old,text in [(cpu,cpu_text),(spu,spu_text)]:
        out=work/old.name;out.write_text(text)
        if q.count('"'+str(old)+'"')!=1:raise ValueError('Ambiguous budget source assignment')
        q=q.replace('"'+str(old)+'"','"'+str(out)+'"')
    qsf.write_text(q);shutil.copyfile(UPSTREAM/'LICENSE',work/'COPYING.GPL2')
    m=mac(project,project/'gte-mac-ram');q=qsf.read_text()
    for row in m['sources']:
        if q.count('"'+row['source']+'"')!=1:raise ValueError('Ambiguous GTE source assignment')
        q=q.replace('"'+row['source']+'"','"'+row['path']+'"')
    qsf.write_text(q);report['overlays']['GTE_MAC']=m
    report['overlays']['watchdogs']=watchdog(project)
    report['overlays']['CPU_execute']=execute(project,address_share=True)
    report['overlays']['hold']=hold(project,digest(project/'core_top.sv'),margin_ns=.15)
    if sort_reread:
        if digest(sort_source)!='f169f05c98a8d714c881568a44cdc0b7c2d6eb864808510f6c9c9f2af8cf5d90':
            raise ValueError('Polygon input changed during preparation')
        sort_source.write_text(sort_text)
        report['overlays']['GPU_sort_reread']=dict(path=str(sort_source),
            original_sha256='f169f05c98a8d714c881568a44cdc0b7c2d6eb864808510f6c9c9f2af8cf5d90',
            sha256=digest(sort_source),extra_GPU_clocks_per_triangle=4,
            preparer_sha256=digest(Path(__file__).with_name('prepare_gpu_sort_reread.py')))
    if gpu_recip:
        # After the watchdog and sorted-reread sources: the final gpu, poly and line.
        from prepare_gpu_recip import apply_project
        selected_now=assignments(project,qsf.read_text())
        def final(name):
            paths={p for _,p in selected_now if p.name==name}
            if len(paths)!=1:raise ValueError('Expected one selected '+name)
            return paths.pop()
        report['overlays']['GPU_recip']=dict(apply_project(project,final),
            preparer_sha256=digest(Path(__file__).with_name('prepare_gpu_recip.py')))
    if gpu_poly_stepadd:
        if not gpu_recip:raise ValueError('--gpu-poly-stepadd needs --gpu-recip')
        from prepare_gpu_poly_stepadd import apply_project as stepadd
        report['overlays']['GPU_poly_stepadd']=dict(stepadd(project),
            preparer_sha256=digest(Path(__file__).with_name('prepare_gpu_poly_stepadd.py')))
    if gpu_poly_diffmux:
        if not gpu_recip:raise ValueError('--gpu-poly-diffmux needs --gpu-recip')
        from prepare_gpu_poly_diffmux import apply_project as diffmux
        report['overlays']['GPU_poly_diffmux']=dict(diffmux(project),
            preparer_sha256=digest(Path(__file__).with_name('prepare_gpu_poly_diffmux.py')))
    report['helper_sha256']=digest(Path(__file__))
    report['inputs']=inputs(project)
    report['project_sha256']={ext:digest(project/('apf_top'+ext))for ext in ['.qsf','.qpf']}
    target=dict(top='apf_top',directory=str(project),quartus_sources=report['inputs'],
        project_sha256=report['project_sha256'],fixed_ports={},physical_clock_ports=['clk_74a','clk_74b'])
    (project/'timing-inputs.json').write_text(json.dumps(target,indent=2)+'\n')
    (work/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project',type=Path)
    parser.add_argument('--sort-reread',action='store_true',help='Sorted vertex RAM reread: +4 GPU clocks per triangle')
    parser.add_argument('--gpu-recip',action='store_true',help='GPU gradients through one reciprocal and a multiply, three 11-bit dividers (prepare_gpu_recip.py; same pixels, earlier gradients)')
    parser.add_argument('--gpu-poly-stepadd',action='store_true',help='gpu_poly base/work colour and texture steps through one adder per channel (prepare_gpu_poly_stepadd.py; needs --gpu-recip; cycle-identical)')
    parser.add_argument('--gpu-poly-diffmux',action='store_true',help='gpu_poly colour/texture differences subtracted after the muldiv input mux, no difference registers (prepare_gpu_poly_diffmux.py; needs --gpu-recip; cycle-identical)')
    args=parser.parse_args()
    print(json.dumps(prepare(args.project,sort_reread=args.sort_reread,gpu_recip=args.gpu_recip,
                             gpu_poly_stepadd=args.gpu_poly_stepadd,gpu_poly_diffmux=args.gpu_poly_diffmux),indent=2))


if __name__=='__main__':main()
