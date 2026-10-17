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


def prepare(project, *, sort_reread=False):
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
                 'cd-side-slot','gte-mac-ram','gpu-release-watchdogs','cpu-execute-share','cpu-hold-margin'):
        if (project/name).exists():raise ValueError('Fresh project required: '+name)
    if not json.loads((project/'manifest.json').read_text()).get('reset_only'):
        raise ValueError('Reset-only build required')
    qsf=project/'apf_top.qsf'
    selected=assignments(project,qsf.read_text())
    cache_paths={p for _,p in selected if p.name=='psx_spu_ch3_cache.sv'}
    if len(cache_paths)!=1 or digest(next(iter(cache_paths)))!='1e65c93531ea81962f7eaf33d3ee5e0531629c754bfea41cb28999dd396e1911':
        raise ValueError('Released rc8 slim SPU cache and free-slot profile required')
    def one(name):
        paths={p for _,p in selected if p.name==name}
        if len(paths)!=1:raise ValueError('Expected one selected '+name)
        return paths.pop()
    cpu,spu=one('cpu.vhd'),one('spu.vhd')
    cpu_text,spu_text=product_text(cpu.read_text()),queue_text(spu.read_text())
    if hashlib.sha256(cpu_text.encode()).hexdigest()!=CPU_PRODUCT_SHA or hashlib.sha256(spu_text.encode()).hexdigest()!=SPU_QUEUE_SHA:
        raise ValueError('Budget emission differs from measured sources')
    core=one('core_top.sv').read_text()
    analog_sha=digest(one('psx_pocket_analog.sv'))
    if analog_sha not in ('7ba041d832cb9209dfed45642caa2995dfb7fb6ff16d07a7859a6263e270d3e7','c329bb75dab0a24687d365aa1852333a52a462c619d610a5850dd9a6f7745a6a','81ee3571b73e1825701badf2459df8ef4f290b841419ac6bfcb3dbbeb0de00f9','ca29019c8fa94d791b63eb9529785214a14ab9345f878c7b11c9e66e173bd033'):
        raise ValueError('Unreviewed analog transport')
    if ('psx_pocket_stick_map stick_map' not in core or not any(tag in core for tag in ('psx_pocket_analog pad_analog','psx_pocket_analog #(.PASS_CONFIG(1)')) or
            digest(one('joypad_pad_dualshock.vhd'))!='18b893b42b224e17b9d2b3963a60d1e1d86436b488aaba3094da34ce5cb78ebc'):
        raise ValueError('Current DualShock and stick-settings profile required')
    atomic='psx_pocket_analog #(.PASS_CONFIG(1)' in core
    config_bits=15 if 'wire [14:0] pad_stick_config_in' in core else 13 if 'wire [12:0] pad_stick_config_in' in core else 4 if 'wire [3:0] pad_stick_config_in' in core else 0
    # 15 bits: the A/B/X/Y stick list (Claude, 2026-10-17) on the CONFIG_W transport.
    if atomic and ((config_bits==15 and analog_sha!='ca29019c8fa94d791b63eb9529785214a14ab9345f878c7b11c9e66e173bd033') or
                   (config_bits==13 and analog_sha not in ('81ee3571b73e1825701badf2459df8ef4f290b841419ac6bfcb3dbbeb0de00f9','ca29019c8fa94d791b63eb9529785214a14ab9345f878c7b11c9e66e173bd033')) or
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
    args=parser.parse_args()
    print(json.dumps(prepare(args.project,sort_reread=args.sort_reread),indent=2))


if __name__=='__main__':main()
