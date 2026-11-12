#!/usr/bin/env python3
"""Pack the two SPU static/reverb guest copies into one existing-clock RAM.

Both copies accept the same CPU write data/address on mutually exclusive
address ranges. Their read ports are sampled on the same falling edge.
Keep both original validity maps, so unwritten registers and warm resets
still read zero. The live engine registers/table and all PXS6 state remain.
"""
import hashlib
import json
from pathlib import Path
from prepare_spu_ring_paired import PAIRED_SPU_SHA,once


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def transform(text,fault=False,engine_ram=False):
    if hashlib.sha256(text.encode()).hexdigest()!=PAIRED_SPU_SHA:
        raise ValueError('Requires exact paired PXS6 SPU')
    for line in ('   signal rvTable_guest  : trvTable;\n',
                 '   attribute ramstyle of rvTable_guest  : signal is "M10K, no_rw_check";\n',
                 '   signal rv_guest_ram_q : std_logic_vector(15 downto 0);\n',
                 '   signal staticRb       : trvTable;\n',
                 '   attribute ramstyle of staticRb : signal is "M10K, no_rw_check";\n',
                 '   signal static_ram_q   : std_logic_vector(15 downto 0);\n'):
        text=once(text,line,'')
    text=once(text,'   signal rv_guest_q     : std_logic_vector(15 downto 0);\n',
        '''   signal rv_guest_q     : std_logic_vector(15 downto 0);
   type tguestRb is array(0 to 63) of std_logic_vector(15 downto 0);
   signal guestRb : tguestRb;
   attribute ramstyle of guestRb : signal is "M10K, no_rw_check";
   signal guest_ram_q : std_logic_vector(15 downto 0);
''')
    text=once(text,'                     staticRb(rv_guest_addr)       <= bus_dataWrite;\n','')
    text=once(text,'                     rvTable_guest(rv_guest_addr)  <= bus_dataWrite;\n','')
    anchor='''                  if (bus_addr(9 downto 6) = "0110" and bus_addr(0) = '0' and STATIC_RB(rv_guest_addr) = '1') then
'''
    write='''                  -- One guest write port: static controls or reverb, never both.
                  if bus_addr(9 downto 7) = "011" and bus_addr(0) = '0' and
                     (bus_addr(6) = '1' or STATIC_RB(rv_guest_addr) = '1') then
                     guestRb(to_integer(bus_addr(6 downto 1))) <= bus_dataWrite;
                  end if;
'''
    text=once(text,anchor,write+anchor)
    old='''         rv_guest_ram_q <= rvTable_guest(rv_guest_addr);
         static_ram_q   <= staticRb(rv_guest_addr);
'''
    address='(not bus_addr(6)) & bus_addr(5 downto 1)' if fault else 'bus_addr(6 downto 1)'
    text=once(text,old,f'         guest_ram_q <= guestRb(to_integer({address}));\n')
    text=once(text,'rv_guest_q     <= rv_guest_ram_q when','rv_guest_q     <= guest_ram_q when')
    text=once(text,'static_q       <= static_ram_q when','static_q       <= guest_ram_q when')
    if engine_ram:
        text=once(text,'attribute ramstyle of rvTable_engine : signal is "MLAB, no_rw_check";',
                       'attribute ramstyle of rvTable_engine : signal is "M10K, no_rw_check";')
        text=once(text,'   signal rv_engine_q    : std_logic_vector(15 downto 0);',
                       '   signal rv_engine_q    : std_logic_vector(15 downto 0);\n   signal rv_engine_ram_q : std_logic_vector(15 downto 0);')
        text=once(text,'   rv_engine_q    <= rvTable_engine(rv_engine_addr) when rvValid(rv_engine_addr)',
            '''   process (clk1x)
   begin
      if falling_edge(clk1x) then
         rv_engine_ram_q <= rvTable_engine(rv_engine_addr);
      end if;
   end process;
   rv_engine_q    <= rv_engine_ram_q when rvValid(rv_engine_addr)''')
    return text


def prepare(source,output,engine_ram=False):
    from run_frozen_pocket_fit import snapshot,hold_guard
    from measure_pocket_seeds import inputs
    source,output=Path(source).resolve(),Path(output).resolve()
    if sha(source/'save-states/spu.vhd')!=PAIRED_SPU_SHA:raise ValueError('Requires paired PXS6 graph')
    result=snapshot(source,source/'fit-inputs.json',output,2)
    target=output/'save-states/spu.vhd';target.write_text(transform(target.read_text(),engine_ram=engine_ram))
    current=inputs(output)
    changed={a['path'] for a,b in zip(result['quartus_sources'],current) if a!=b}
    if len(current)!=len(result['quartus_sources']) or changed!={str(target)}:
        raise ValueError('Packed guest bank changed other selected inputs')
    hold_guard(output,current);result['quartus_sources']=current
    (output/'fit-inputs.json').write_text(json.dumps(result,indent=2)+'\n')
    # This storage-only variant retains the PXS6 port layout and ROM exactly.
    fw=source/'staged-ring.S'
    if fw.is_file():(output/'staged-ring.S').write_bytes(fw.read_bytes())
    report=dict(source=str(source),spu_sha256=sha(target),engine_ram=engine_ram,firmware_compatible=True,
                private_abi_unchanged=True,full_audio_preserved=True,selected=False,
                hardware_verified=False,all_machine_state_covered=False)
    (output/'save-guest-bank.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--engine-ram',action='store_true',help='reuse the recovered block for the engine configuration table')
    a=p.parse_args();print(json.dumps(prepare(a.source,a.output,a.engine_ram),indent=2))
