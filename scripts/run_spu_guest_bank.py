#!/usr/bin/env python3
"""Native equivalence and PXS6 restore for the packed SPU guest register RAM."""
from pathlib import Path
import sys
from prepare_spu_sample_save import transform as samples
from prepare_spu_ring_paired import spu_text,native_bench,once
from prepare_spu_guest_bank import transform as packed
from run_spu_full_context import main as check
ROOT=Path(__file__).resolve().parents[1]

def transform(text,fault):
    return packed(spu_text(samples(text)),fault=fault==8)

def bench(text):
    text=native_bench(text)
    before="                for i in 1 to 40 + rnd(400) loop tick; end loop;\n"
    after="""                -- Read both banks and the just-written register. This is
                -- essential coverage for a read-port storage transformation.
                bus_s.addr<=to_unsigned(address,10); bus_s.rd<='1'; tick; bus_s.rd<='0'; tick;
                bus_s.addr<=to_unsigned(16#180# + 2*(op mod 64),10); bus_s.rd<='1'; tick; bus_s.rd<='0'; tick;
                for i in 1 to 40 + rnd(400) loop tick; end loop;
"""
    if '--guest-equivalence' in sys.argv:
        after += """                if op = OPERATIONS/2 then
                    reset<='1'; for j in 1 to 3 loop tick; end loop; reset<='0';
                    for j in 0 to 63 loop
                        bus_s.addr<=to_unsigned(16#180# + 2*j,10); bus_s.rd<='1'; tick; bus_s.rd<='0'; tick;
                    end loop;
                end if;
"""
    return once(text,before,after)


if __name__=='__main__':
    raise SystemExit(check(spu_transform=transform,bench_source=ROOT/'sim/pocket/tb_spu_global_ring.vhd',
        bench_transform=bench,extra_inputs=(Path(__file__),ROOT/'scripts/prepare_spu_guest_bank.py',
        ROOT/'scripts/prepare_spu_ring_paired.py',ROOT/'scripts/prepare_spu_global_ring.py'),
        fault_choices=(0,1,2,3,5,6,8),guest_mutation=('guest_ram_q <= guestRb(to_integer(bus_addr(6 downto 1)));','guest_ram_q <= guestRb(to_integer((not bus_addr(6)) & bus_addr(5 downto 1)));'),
        supports_abort=True,scan_mode='PXS6-packed-guest'))
