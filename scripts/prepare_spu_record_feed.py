#!/usr/bin/env python3
"""Remove the SPU's redundant103-bit voice-record write-data register.

After VOICE_END the working voice stays stable through the queued RAM
write. During quiet full four-word restore it can serve as the assembly
buffer. Initialization uses the same zero working record before voices run.
Keep a simulation-only copy of the original latch and assert every actual
RAM-write payload matches it. No RAM port, guest cycle or scan field is cut.
"""
import hashlib
import json
from pathlib import Path
import re
from prepare_spu_ring_paired import PAIRED_SPU_SHA,once

FIELDS=[('currentAddr',15,0,'unsigned'),('adpcmLast0',31,16,'signed'),('adpcmLast1',47,32,'signed'),
        ('adpcmSamplePos',67,48,'unsigned'),('adpcmDecodePtr',73,68,'unsigned'),('adsrphase',76,74,'unsigned'),
        ('adsrTicks',100,77,'unsigned'),('firstBlock',101,101,'bit'),('ignoreLoopWB',102,102,'bit')]
ZERO="((others => '0'), (others => '0'), (others => '0'), (others => '0'), (others => '0'), (others => '0'), (others => '0'), '0', '0')"


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def transform(text):
    if hashlib.sha256(text.encode()).hexdigest()!=PAIRED_SPU_SHA:
        raise ValueError('Requires exact paired PXS6 SPU')
    decl="   signal RamVoiceRecord_dataA : std_logic_vector(102 downto 0) := (others => '0');"
    text=once(text,decl,"""   signal RamVoiceRecord_dataA : std_logic_vector(102 downto 0);
   -- synthesis translate_off
   signal record_expected : std_logic_vector(102 downto 0) := (others => '0');
   -- synthesis translate_on""")
    old="               RamVoiceRecord_dataA <= (others => '0');"
    text=once(text,old,"               voice <= "+ZERO+";\n               -- synthesis translate_off\n               record_expected <= (others => '0');\n               -- synthesis translate_on")
    lookup={(str(hi) if hi==lo else f'{hi}downto{lo}'):(name,kind) for name,hi,lo,kind in FIELDS}
    counts=[0,0]
    def rewrite(match):
        indent,part,rhs,comment=match.groups()
        key=re.sub(r'\s','',part);name,kind=lookup[key]
        expected=indent+'record_expected('+part+') <= '+rhs+';'+comment
        if rhs.startswith('SS_DataWrite'):
            counts[0]+=1
            value=rhs if kind=='bit' else kind+'('+rhs+')'
            actual=('tk_mode := 2; tk_val := '+value+';' if name=='adsrTicks' else 'voice.'+name+' <= '+value+';')
            return indent+actual+'\n'+indent+'-- synthesis translate_off\n'+expected+'\n'+indent+'-- synthesis translate_on'
        if 'voice.' not in rhs:raise ValueError('Unexpected record write source '+rhs)
        counts[1]+=1
        return indent+'-- synthesis translate_off\n'+expected+'\n'+indent+'-- synthesis translate_on'
    text=re.sub(r'^([ \t]*)RamVoiceRecord_dataA\(([^)]+)\)\s*<= ([^;]+);([^\n]*)$',rewrite,text,flags=re.M)
    if counts!=[9,9]:raise ValueError('Record latch sites changed '+str(counts))
    pieces=[('voice.'+name) if kind=='bit' else 'std_logic_vector(voice.'+name+')' for name,_,_,kind in reversed(FIELDS)]
    feed='   RamVoiceRecord_dataA <= '+ ' &\n                            '.join(pieces)+';\n'
    feed+='''   -- synthesis translate_off
   process (clk1x)
   begin
      if rising_edge(clk1x) then
         if RamVoiceRecord_write = '1' then
            assert RamVoiceRecord_dataA = record_expected
               report "Record-feed payload differs from source latch" severity failure;
         end if;
         if ss_voice_loading = '1' and RamVoice_addrA < 24 then
            assert state = IDLE or reset = '1'
               report "Record initialization overlaps active voice" severity failure;
         end if;
         if SS_wren = '1' and SS_Adr >= 384 and SS_Adr < 480 then
            assert save_spu_quiet = '1'
               report "Record restore requires quiet SPU" severity failure;
         end if;
      end if;
   end process;
   -- synthesis translate_on
'''
    text=once(text,"   sample_save_scan <=",feed+"   sample_save_scan <=")
    return text


def prepare(source,output):
    from run_frozen_pocket_fit import snapshot,hold_guard
    from measure_pocket_seeds import inputs
    source,output=Path(source).resolve(),Path(output).resolve()
    if sha(source/'save-states/spu.vhd')!=PAIRED_SPU_SHA:raise ValueError('Requires paired PXS6 graph')
    result=snapshot(source,source/'fit-inputs.json',output,2)
    target=output/'save-states/spu.vhd';target.write_text(transform(target.read_text()))
    current=inputs(output)
    changed={a['path'] for a,b in zip(result['quartus_sources'],current) if a!=b}
    if len(current)!=len(result['quartus_sources']) or changed!={str(target)}:
        raise ValueError('Record feed changed other selected inputs')
    hold_guard(output,current);result['quartus_sources']=current
    (output/'fit-inputs.json').write_text(json.dumps(result,indent=2)+'\n')
    fw=source/'staged-ring.S'
    if fw.is_file():(output/'staged-ring.S').write_bytes(fw.read_bytes())
    report=dict(source=str(source),spu_sha256=sha(target),private_abi_unchanged=True,
                full_audio_preserved=True,selected=False,hardware_verified=False,all_machine_state_covered=False)
    (output/'save-record-feed.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();print(json.dumps(prepare(a.source,a.output),indent=2))
