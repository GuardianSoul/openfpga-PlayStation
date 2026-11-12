#!/usr/bin/env python3
"""Compare exact base-DSP polygon control with added work-DSP feedback."""
import argparse
import hashlib
from pathlib import Path

import run_gpu_base_dsp as base
from prepare_gpu_base_dsp import transform as base_transform, POLY_SHA
from prepare_gpu_work_dsp import transform, SOURCE
from prepare_spu_ring_paired import once

MODEL_NAME='psx_gpu_work_dsp_model.vhd'
MODEL='''library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity psx_gpu_work_dsp is
   port(clk,enable,load,initialize: in std_logic;
        base,increment: in std_logic_vector(19 downto 0);
        value: out std_logic_vector(19 downto 0));
end entity;
architecture model of psx_gpu_work_dsp is
   signal acc: unsigned(19 downto 0) := (others=>'0');
begin
   value <= std_logic_vector(acc);
   process(clk) begin
      if rising_edge(clk) then
         if initialize='1' then acc <= (others=>'0');
         elsif enable='1' then
            if load='1' then acc <= unsigned(base);
            else acc <= acc+unsigned(increment); end if;
         end if;
      end if;
   end process;
end architecture;
'''


def source_text(name,candidate,fault):
    if name==MODEL_NAME:return MODEL,str(Path(__file__).resolve())
    if name==base.MODEL_NAME:return base.MODEL,str(Path(base.__file__).resolve())
    text,origin=base.release.source_text(name,False,None)
    if origin:
        p=Path(origin);base.ORIGINS[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
    if name=='gpu_poly.vhd':
        if hashlib.sha256(text.encode()).hexdigest()!=POLY_SHA:raise ValueError('Original selected polygon changed')
        text=base_transform(text)
        if hashlib.sha256(text.encode()).hexdigest()!=SOURCE:raise ValueError('Base-DSP control mismatch')
        if candidate:
            text=transform(text)
            if fault:
                text=once(text,'work_dsp_base(1) <= std_logic_vector(base_G);',
                          'work_dsp_base(1) <= std_logic_vector(base_R);')
    return text,origin


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--fault',action='store_true');a=p.parse_args()
    base.dy.SOURCES.insert(base.dy.SOURCES.index(('work','gpu_poly.vhd')),('work',MODEL_NAME))
    base.source_text=source_text; base.POLY_SHA=SOURCE
    raise SystemExit(base.main(a.output.resolve(),a.fault,fault_label='wrong-work-green-base'))
