#!/usr/bin/env python3
"""Check the Gaussian DSP wiring and native SPU full-audio restore."""
import json
import sys
from pathlib import Path

from prepare_spu_gaussian_sum import transform as gaussian
from run_spu_pending_feed import transform as pending
from run_spu_guest_bank import bench
from run_spu_full_context import main as check

ROOT=Path(__file__).resolve().parents[1]
MODEL='''library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity psx_spu_gaussian_sum is
   port(x0,x1,x2,x3,c0,c1,c2,c3: in std_logic_vector(15 downto 0);
        sum: out std_logic_vector(30 downto 0));
end entity;
architecture model of psx_spu_gaussian_sum is
begin
   sum <= std_logic_vector(resize(signed(x0)*signed(c0),31)
         +resize(signed(x1)*signed(c1),31)+resize(signed(x2)*signed(c2),31)
         +resize(signed(x3)*signed(c3),31));
end architecture;
'''


def transform(text,fault):
    return gaussian(pending(text,fault))


if __name__=='__main__':
    result=check(spu_transform=transform,
        bench_source=ROOT/'sim/pocket/tb_spu_global_ring.vhd',
        bench_transform=lambda t:MODEL+'\n'+bench(t),
        extra_inputs=(Path(__file__),ROOT/'scripts/prepare_spu_gaussian_sum.py',
            ROOT/'scripts/run_spu_pending_feed.py',ROOT/'scripts/prepare_spu_pending_feed.py',
            ROOT/'scripts/prepare_spu_record_feed.py',ROOT/'scripts/prepare_spu_ring_paired.py',
            ROOT/'scripts/prepare_spu_global_ring.py',ROOT/'scripts/run_spu_guest_bank.py'),
        fault_choices=(0,1,2,3,5,6),supports_abort=True,
        guest_mutation=('c2 => std_logic_vector(gauss3)','c2 => std_logic_vector(gauss2)'),
        scan_mode='PXS6-Gaussian-hard-sum')
    if '--guest-routing-fault' in sys.argv:
        out=Path(sys.argv[sys.argv.index('--output')+1])
        report=json.loads((out/'report.json').read_text())
        caught=bool(result and report['exit_code']!=0 and not report['inputs_changed']
                    and 'SPU Gaussian DSP sum differs from original' in (out/'run.log').read_text())
        report['expected_gaussian_wiring_mutation_caught']=caught
        report['passed']=caught
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print('PASS Gaussian wiring mutation caught' if caught else 'FAIL Gaussian mutation not caught')
        result=0 if caught else 1
    raise SystemExit(result)
