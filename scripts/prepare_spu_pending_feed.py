#!/usr/bin/env python3
"""Reuse live SPU work values instead of three pending-write data banks.

Extends record direct feed with envelope volume/counter and decoded-sample
RAM payloads. Small zero/source selectors preserve init and copy operations.
Simulation-only old payload latches check every enabled normal-port write;
quiet full-context restoration is qualified separately. No extra RAM/clock.
"""
import json
from pathlib import Path
import re
from prepare_spu_record_feed import transform as record,once,sha
from prepare_spu_ring_paired import PAIRED_SPU_SHA


def transform(text):
    text=record(text)
    for signal,typ,zero,expected in (
        ('RamVoiceVolumes_dataA','std_logic_vector(15 downto 0)',"(others => '0')",'volume_expected'),
        ('EnvCounterRam_dataA','std_logic_vector(24 downto 0)',"(others => '0')",'counter_expected'),
        ('adpcm_ram_data_a','tadpcm_data',"(others => (others => '0'))",'adpcm_expected')):
        pattern=rf'^(   signal {signal}\s*: {re.escape(typ)}) := {re.escape(zero)};[^\n]*$'
        text,n=re.subn(pattern,rf'\1;\n   -- synthesis translate_off\n   signal {expected} : {typ} := {zero};\n   -- synthesis translate_on',text,flags=re.M)
        if n!=1:raise ValueError('Pending buffer declaration changed '+signal)
    text=once(text,'   signal EnvCounterRam_write : std_logic := \'0\';',
        '''   signal EnvCounterRam_write : std_logic := '0';
   signal volume_feed_zero, counter_feed_zero : std_logic := '1';
   signal adpcm_feed_mode : integer range 0 to 2 := 0; -- zero, decode, copy
''')
    counts={name:0 for name in ('RamVoiceVolumes_dataA','EnvCounterRam_dataA','adpcm_ram_data_a')}
    def rewrite(m):
        indent,name,part,rhs,comment=m.groups();counts[name]+=1
        expected={'RamVoiceVolumes_dataA':'volume_expected','EnvCounterRam_dataA':'counter_expected','adpcm_ram_data_a':'adpcm_expected'}[name]
        mirror=indent+expected+(part or '')+' <= '+rhs+';'+comment
        if name=='RamVoiceVolumes_dataA':
            if rhs=="(others => '0')":actual="volume_feed_zero <= '1';"
            elif rhs.startswith('SS_DataWrite'):actual="envVolume <= signed("+rhs+"); volume_feed_zero <= '0';"
            elif rhs=='std_logic_vector(envVolume)':actual="volume_feed_zero <= '0';"
            else:raise ValueError('Volume source changed '+rhs)
        elif name=='EnvCounterRam_dataA':
            if rhs=="(others => '0')":actual="counter_feed_zero <= '1';"
            elif rhs.startswith('SS_DataWrite'):actual="envTicksNew <= SS_DataWrite(24); envTicks <= unsigned(SS_DataWrite(23 downto 0)); counter_feed_zero <= '0';"
            elif rhs=='envTicksNew & std_logic_vector(envTicks)':actual="counter_feed_zero <= '0';"
            else:raise ValueError('Counter source changed '+rhs)
        else:
            if rhs=="(others => (others => '0'))":actual='adpcm_feed_mode <= 0;'
            elif rhs=='std_logic_vector(adpcm_decode_result)':actual='adpcm_feed_mode <= 1;'
            elif rhs=='adpcm_ram_q_b':actual='adpcm_feed_mode <= 2;'
            else:raise ValueError('Sample source changed '+rhs)
        return indent+actual+'\n'+indent+'-- synthesis translate_off\n'+mirror+'\n'+indent+'-- synthesis translate_on'
    pattern=r'^([ \t]*)(RamVoiceVolumes_dataA|EnvCounterRam_dataA|adpcm_ram_data_a)(\([^\n]*?\))?\s*<= ([^;]+);([^\n]*)$'
    text=re.sub(pattern,rewrite,text,flags=re.M)
    if counts!={'RamVoiceVolumes_dataA':3,'EnvCounterRam_dataA':3,'adpcm_ram_data_a':4}:
        raise ValueError('Pending buffer sites changed '+str(counts))
    feed='''   RamVoiceVolumes_dataA <= (others => '0') when volume_feed_zero = '1' else std_logic_vector(envVolume);
   EnvCounterRam_dataA <= (others => '0') when counter_feed_zero = '1' else envTicksNew & std_logic_vector(envTicks);
   pending_samples : for i in 0 to 3 generate
      adpcm_ram_data_a(i) <= (others => '0') when adpcm_feed_mode = 0 else
                            std_logic_vector(voice.adpcmLast0) when adpcm_feed_mode = 1 else adpcm_ram_q_b(i);
   end generate;
   -- synthesis translate_off
   process (clk1x)
   begin
      if rising_edge(clk1x) then
         if RamVoiceVolumes_write = '1' then
            assert RamVoiceVolumes_dataA = volume_expected report "Volume-feed payload differs from source latch" severity failure;
         end if;
         if EnvCounterRam_write = '1' then
            assert EnvCounterRam_dataA = counter_expected report "Counter-feed payload differs from source latch" severity failure;
         end if;
         for i in 0 to 3 loop
            if adpcm_ram_wren_a(i) = '1' then
               assert adpcm_ram_data_a(i) = adpcm_expected(i) report "Sample-feed payload differs from source latch" severity failure;
            end if;
         end loop;
         if SS_wren = '1' and ((SS_Adr >= 256 and SS_Adr < 304) or (SS_Adr >= 320 and SS_Adr < 370)) then
            assert envelopeState = ENV_IDLE and save_spu_quiet = '1'
               report "Envelope restore requires idle working state" severity failure;
         end if;
      end if;
   end process;
   -- synthesis translate_on
'''
    return once(text,'   sample_save_scan <=',feed+'   sample_save_scan <=')


def prepare(source,output):
    from run_frozen_pocket_fit import snapshot,hold_guard
    from measure_pocket_seeds import inputs
    source,output=Path(source).resolve(),Path(output).resolve()
    if sha(source/'save-states/spu.vhd')!=PAIRED_SPU_SHA:raise ValueError('Requires paired PXS6 graph')
    result=snapshot(source,source/'fit-inputs.json',output,2)
    target=output/'save-states/spu.vhd';target.write_text(transform(target.read_text()))
    current=inputs(output)
    changed={a['path'] for a,b in zip(result['quartus_sources'],current) if a!=b}
    if len(current)!=len(result['quartus_sources']) or changed!={str(target)}:raise ValueError('Pending feed changed other selected inputs')
    hold_guard(output,current);result['quartus_sources']=current
    (output/'fit-inputs.json').write_text(json.dumps(result,indent=2)+'\n')
    fw=source/'staged-ring.S'
    if fw.is_file():(output/'staged-ring.S').write_bytes(fw.read_bytes())
    report=dict(source=str(source),spu_sha256=sha(target),private_abi_unchanged=True,full_audio_preserved=True,
                selected=False,hardware_verified=False,all_machine_state_covered=False)
    (output/'save-pending-feed.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();print(json.dumps(prepare(a.source,a.output),indent=2))
