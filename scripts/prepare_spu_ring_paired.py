#!/usr/bin/env python3
"""Pair the own cyclic scalar port with PXS6 firmware; experimental, full audio.

The old area screen relocated rvWork into the firmware's guest-register
staging range. With exactly sixteen scalar words it can stay at48..62.
Only the SPU and its paired firmware ROM change in the frozen PXS5 graph.
"""
import hashlib
import json
from pathlib import Path
from prepare_spu_global_ring import transform as ring, once, BASE

PAIRED_SPU_SHA='5bc07d57e09008c9b527df1493180c30f6c021a414a3c675c7d931a3f0309a73'
FW_SHA='cb11adfb5a56ca350d4f2a6b98aad5beae49a9d634967a9031cef71dbd32add1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def spu_text(text, fault=False):
    result=ring(text,fault=fault)
    old='SS_Adr >= 96 and SS_Adr <= 110'
    if result.count(old)!=3:raise ValueError('Work port relocation changed')
    return result.replace(old,'SS_Adr >= 48 and SS_Adr <= 62')


def require_paired_spu(path,reported_sha):
    if sha(path)!=PAIRED_SPU_SHA or reported_sha!=PAIRED_SPU_SHA:
        raise ValueError('Paired scalar-ring SPU differs')


def firmware_text(text):
    if hashlib.sha256(text.encode()).hexdigest()!=FW_SHA:
        raise ValueError('Requires qualified PXS5 firmware')
    text=once(text,'0x35535850       # "PXS5" (decoded samples, pending writes and audio history)',
                   '0x36535850       # "PXS6" (cyclic scalar port, full PXS5 state)')
    first=text.index('        lw      $t0, SPU_REGS+0x1B8($k0) # current main volume: port word 43 is\n')
    last=text.index('        jal     put_data\n',first)
    block=text[first:last]
    if len(block.splitlines())!=6:raise ValueError('Legacy scalar guest-read fixup changed')
    # The ring itself contains ENDX/current volumes/IRQ. Those old writes
    # would overwrite scalar words37/40/43 and silently corrupt the state.
    text=text[:first]+text[last:]
    text=once(text,'.hword  2, 23, 37, 38, 40, 41, 43, 44, 48, 63, 256, 304, 320, 370, 384, 480, 0, 0',
                   '.hword  7, 8, 32, 63, 256, 304, 320, 370, 384, 480, 0, 0')
    return text


def native_bench(text):
    return once(text,'(address >= 96 and address <= 110)','(address >= 48 and address <= 62)')


def cpu_bench(text):
    """Authored port model. Physical/native SPU is qualified separately.

    SS words32..47 model the actual circulating512-bit state; output dumps
    include all words, samples and RAM. References use this same PXS6 model.
    Guest-side destructive writes intentionally test complete restoration.
    """
    text=once(text,'return (n >= 2 and n <= 22) or (n >= 48 and n <= 62) or n = 37 or n = 40 or n = 43 or',
                   'return n = 7 or (n >= 32 and n <= 62) or')
    # Existing model reads only feed the guest register staging copy. They
    # must not reinterpret ring words as the preceding indexed scalar ABI.
    text=once(text,'if sa = 16#1B8# then spu_q <= ss(43)(15 downto 0); end if;',
                   'if sa = 16#1B8# then spu_q <= ss(47)(9 downto 0) & ss(46)(31 downto 26); end if;')
    text=once(text,'if sa = 16#1BA# then spu_q <= ss(43)(31 downto 16); end if;',
                   'if sa = 16#1BA# then spu_q <= ss(47)(25 downto 10); end if;')
    # These readback-only model values are not consumed by the PXS6 scalar
    # restore, but remain deterministic for the complete dump comparison.
    text=once(text,'if sa = 16#19C# then spu_q <= ss(37)(15 downto 0); end if;',
                   'if sa = 16#19C# then spu_q <= ss(46)(8 downto 0) & ss(45)(31 downto 25); end if;')
    text=once(text,'if sa = 16#19E# then spu_q <= ss(37)(31 downto 16); end if;',
                   'if sa = 16#19E# then spu_q <= ss(46)(24 downto 9); end if;')
    text=once(text,'if sa = 16#1AE# then spu_q <= ss(40)(31 downto 16); end if;',
                   'if sa = 16#1AE# then spu_q <= "000000000" & ss(46)(25) & "000000"; end if;')
    text=text.replace("ss(40)(15 downto 0) := (others => '0');", "ss(47)(31 downto 26) := (others => '0');")
    for old,new,value in ((4,33,'BAD00004'),(3,32,'BAD00003'),(40,46,'BAD00000')):
        text=once(text,f'ss({old}) := x"{value}";',f'ss({new}) := x"{value}";')
    text=once(text,'elsif mux_bus_exp1_dataWrite(1) = \'1\' then ss(ss_adr) := page(ss_adr);',
        '''elsif ss_adr >= 32 and ss_adr <= 47 then
                  -- Fixed low-word tap; every access rotates the real bank.
                  w := ss(32);
                  for k in 32 to 46 loop ss(k) := ss(k+1); end loop;
                  if mux_bus_exp1_dataWrite(1) = '1' then ss(47) := page(ss_adr);
                  else page(ss_adr) := w; ss(47) := w; end if;
               elsif mux_bus_exp1_dataWrite(1) = '1' then ss(ss_adr) := page(ss_adr);''')
    text=once(text,"elsif ss_adr = 37 or ss_adr = 40 or ss_adr = 43 then page(ss_adr) := (others => '0');\n",'')
    text=once(text,'x"35" report "Expected a PXS5 source state"','x"36" report "Expected a PXS6 source state"')
    return once(text,'x"34"; -- preceding incomplete PXS4 format','x"35"; -- preceding indexed PXS5 format')


def prepare(source,output):
    from run_frozen_pocket_fit import snapshot,hold_guard
    from measure_pocket_seeds import inputs
    from build_staged_save_fw import image_parts,rom1,rom2,rom40_hex,SRC
    source,output=Path(source).resolve(),Path(output).resolve()
    if sha(source/'save-states/spu.vhd')!=BASE:raise ValueError('Requires closed expanded PXS5 graph')
    result=snapshot(source,source/'fit-inputs.json',output,2)
    spu=output/'save-states/spu.vhd';spu.write_text(spu_text(spu.read_text()))
    fw=output/'staged-ring.S';fw.write_text(firmware_text(SRC.read_text()))
    image,stage=image_parts(source=fw)
    rom=output/'save-states/psx_save_rom40.hex';rom.write_text(rom40_hex(rom1(image),rom2(image)))
    current=inputs(output)
    changed={a['path'] for a,b in zip(result['quartus_sources'],current) if a!=b}
    if len(current)!=len(result['quartus_sources']) or changed!={str(spu),str(rom)}:
        raise ValueError('Paired ring changed other selected inputs')
    hold_guard(output,current);result['quartus_sources']=current
    (output/'fit-inputs.json').write_text(json.dumps(result,indent=2)+'\n')
    report=dict(source=str(source),firmware_source_sha256=sha(fw),spu_sha256=sha(spu),rom_sha256=sha(rom),
                stage2_bytes=len(stage),firmware_compatible=True,full_audio_preserved=True,
                selected=False,hardware_verified=False,all_machine_state_covered=False)
    (output/'save-ring-paired.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(prepare(a.source,a.output),indent=2))
