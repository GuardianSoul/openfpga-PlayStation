#!/usr/bin/env python3
"""Cycle-check the exact selected polygon in the established GPU oracle fixture."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import run_gpu_dy_ram as dy
import run_gpu_poly_base_share as release
import run_gpu_recip as recip
from run_gpu_poly_stepadd import make_bench
from prepare_gpu_base_dsp import transform,POLY_SHA
from prepare_spu_ring_paired import once
from tool_paths import ROOT,find_tool

MODEL_NAME='psx_gpu_base_dsp_model.vhd'
MODEL='''library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity psx_gpu_base_dsp is
   port(clk,enable,load,initialize: in std_logic;
        vertex: in std_logic_vector(7 downto 0);
        increment: in std_logic_vector(19 downto 0);
        value: out std_logic_vector(19 downto 0));
end entity;
architecture model of psx_gpu_base_dsp is
   signal acc: unsigned(19 downto 0) := (others=>'0');
begin
   value <= std_logic_vector(acc);
   process(clk) begin
      if rising_edge(clk) then
         if initialize='1' then acc <= (others=>'0');
         elsif enable='1' then
            if load='1' then acc <= unsigned(vertex) & x"800";
            else acc <= acc+unsigned(increment); end if;
         end if;
      end if;
   end process;
end architecture;
'''
ORIGINS={}
DERIVED={}


def source_text(name,candidate,fault):
    if name==MODEL_NAME:return MODEL,str(Path(__file__).resolve())
    text,origin=release.source_text(name,False,None)
    if origin:
        p=Path(origin);ORIGINS[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
    if name=='gpu_poly.vhd':
        if hashlib.sha256(text.encode()).hexdigest()!=POLY_SHA:
            raise ValueError('Fixture must regenerate the exact selected polygon')
        if candidate:
            text=transform(text)
            if fault:
                text=once(text,'base_dsp_vertex(1) <= std_logic_vector(vt(coreVertex).g);',
                          'base_dsp_vertex(1) <= std_logic_vector(vt(coreVertex).r);')
    return text,origin


def main(output,fault,fault_label='wrong-green-vertex'):
    if output.exists():raise ValueError('Fresh output required')
    output.mkdir(parents=True)
    # The historical fixture regenerates this exact current polygon; its
    # surrounding GPU glue is not a claim of current full-console coverage.
    if ('work',recip.UNIT_NAME) not in dy.SOURCES:
        dy.SOURCES.insert(dy.SOURCES.index(('work','gpu.vhd')),('work',recip.UNIT_NAME))
    dy.SOURCES.insert(dy.SOURCES.index(('work','gpu_poly.vhd')),('work',MODEL_NAME))
    dy.source_text=source_text;dy.make_bench=make_bench
    owned=[Path(__file__),ROOT/'scripts/prepare_gpu_base_dsp.py',ROOT/'scripts/run_gpu_dy_ram.py',
           ROOT/'scripts/run_gpu_poly_base_share.py',ROOT/'scripts/run_gpu_poly_stepadd.py',
           ROOT/'scripts/run_gpu_line_share.py',ROOT/'scripts/run_gpu_recip.py',
           ROOT/'scripts/run_gpu_sort_reread.py',ROOT/'scripts/prepare_system_verilator.py',
           ROOT/'sim/reference/mister_memory_models.sv',ROOT/'sim/reference/tb_gpu_primitives.sv']
    protected={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in owned}
    for module in tuple(sys.modules.values()):
        name=getattr(module,'__file__',None)
        if name:
            path=Path(name).resolve()
            if path.suffix=='.py' and path.is_relative_to(ROOT/'scripts'):
                protected[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    original_transcode=dy.transcode
    def closed_transcode(*args,**kwargs):
        generated=original_transcode(*args,**kwargs)
        for row in generated[0]:DERIVED[row['path']]=row['sha256']
        DERIVED[str(generated[1])]=hashlib.sha256(Path(generated[1]).read_bytes()).hexdigest()
        return generated
    dy.transcode=closed_transcode
    def closed_bench(*args):
        path=make_bench(*args)
        DERIVED[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
        return path
    dy.make_bench=closed_bench
    with (output/'baseline.log').open('w') as log:
        baseline=dy.transcode(output/'original',False,None,find_tool('ghdl'),log)
    result=dy.run(output,baseline,fault_label if fault else None,
                  find_tool('ghdl'),find_tool('verilator'),4)
    protected.update(ORIGINS)
    for row in (*baseline[0],*result['candidate_sources']):
        origin=row.get('origin')
        if origin:
            if origin in protected and protected[origin]!=row['origin_sha256']:
                raise ValueError('Origin drift between GPU builds: '+origin)
            protected[origin]=row['origin_sha256']
    # Keep the complete helper/source closure used by the emitted models.
    evidence=output/'source-evidence'
    for p,h in protected.items():
        target=evidence/p.lstrip('/');target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(Path(p).read_bytes())
        if hashlib.sha256(target.read_bytes()).hexdigest()!=h:raise ValueError('Input changed during capture')
    drift=[p for p,h in protected.items() if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
    drift += [p for p,h in DERIVED.items() if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
    log=(output/'run.log').read_text()
    caught=fault and result['exit_code']!=0 and ('mismatch' in log.lower() or 'differs' in log.lower())
    passed=(bool(caught) if fault else result['passed']) and not drift
    report=dict(passed=passed,result=result,inputs=protected,inputs_changed=drift,
                generated_inputs=DERIVED,baseline_sources=baseline[0],
                exact_selected_polygon_sha256=POLY_SHA,expected_wiring_mutation_caught=bool(caught),
                scope='Exact selected polygon; established GPU context with portable DSP and RAM models',
                full_current_console_or_hardware_verified=False,selected=False)
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    compact={k:result[k] for k in ('fault','passed','exit_code','cycle_ticks','programs','memory_checks','first_failure','seconds')}
    print('PASS' if passed else 'FAIL',json.dumps(compact),flush=True)
    return 0 if passed else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--fault',action='store_true');a=p.parse_args();raise SystemExit(main(a.output.resolve(),a.fault))
