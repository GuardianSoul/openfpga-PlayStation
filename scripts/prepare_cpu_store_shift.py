#!/usr/bin/env python3
"""Share the selected CPU execute shifter with its store-data formatters."""
import argparse
import hashlib
import json
import re
from pathlib import Path

from measure_pocket_seeds import inputs
from prepare_cpu_load_lanes import CPU_SHA
import re as _re

# The release recipe's CPU differs from the qualified one only in comments (a
# licence credit, a reworded fetch-error note). Code lines are what must match.
CPU_CODE_SHA = '2f2650737bcfe49e83d0b0e1cd8e00797cabdba0eabcce2e07b911c555123746'


def code_only(text):
    lines = (_re.sub(r'\s*--.*$', '', l).rstrip() for l in text.splitlines())
    return '\n'.join(l for l in lines if l.strip())


# The same CPU with the clean-room MULT/MULTU product (scripts/cpu_shared_product.py, PR #95): its code
# lines differ from the qualified CPU's only in the multiplier (9 changed code lines, all mul*/HILOCALC_MULT),
# outside the store block and the shifter this step edits; the store block text is identical.
CPU_CODE_SHA_SHARED_PRODUCT = '07efc6a14007d9579749b3829a3cb36003b5ffd81047e7ba4b06df3823965ada'


def qualified_cpu(text):
    return (hashlib.sha256(text.encode()).hexdigest() == CPU_SHA or
            hashlib.sha256(code_only(text).encode()).hexdigest() in (CPU_CODE_SHA, CPU_CODE_SHA_SHARED_PRODUCT))
from prepare_spu_ring_paired import once
from run_frozen_pocket_fit import hold_guard,load_source,snapshot

SHIFT='''      executeShamt := decodeShamt;
      if (decodeFunct(2) = '1') then
         executeShamt := value1(4 downto 0);
      end if;
      sharedShift := pocket_shift(value2,executeShamt,decodeFunct);'''
SHARED='''      executeShamt := decodeShamt;
      if (decodeFunct(2) = '1') then
         executeShamt := value1(4 downto 0);
      end if;
      shiftFunct := decodeFunct;
      if decodeOP = 16#28# or decodeOP = 16#2E# then
         executeShamt := calcMemAddr(1 downto 0) & "000";
         shiftFunct := "000000";
      elsif decodeOP = 16#29# then
         executeShamt := calcMemAddr(1) & "0000";
         shiftFunct := "000000";
      elsif decodeOP = 16#2A# then
         executeShamt := (not calcMemAddr(1 downto 0)) & "000";
         shiftFunct := "000010";
      end if;
      sharedShift := pocket_shift(value2,executeShamt,shiftFunct);'''


def store_block(text):
    start=text.index('            when 16#28# => -- SB\n',text.index('variable calcMemAddr'))
    end=text.index('            when 16#30# => -- LWC0:',start)
    return text[start:end]


def transform(text):
    if not qualified_cpu(text):
        raise ValueError('Exact selected CPU required')
    block=store_block(text)
    replacement,count=re.subn(r'EXEMemWriteData\s*<=\s*[^;]+;',
                               'EXEMemWriteData <= sharedShift;',block)
    if count!=14:raise ValueError('Store data assignment inventory changed')
    text=once(text,block,replacement)
    text=once(text,'      variable sharedShift : unsigned(31 downto 0);',
              '      variable sharedShift : unsigned(31 downto 0);\n      variable shiftFunct : unsigned(5 downto 0);')
    return once(text,SHIFT,SHARED)


def prepare(source,output):
    source,output=source.resolve(),output.resolve()
    manifest=load_source(source,source/'fit-inputs.json')
    rows=[r for r in manifest['quartus_sources'] if Path(r['path']).name=='cpu.vhd']
    if len(rows)!=1 or not qualified_cpu(Path(rows[0]['path']).read_text()):
        raise ValueError('Selected CPU source changed')
    manifest=snapshot(source,source/'fit-inputs.json',output,2)
    before=inputs(output);cpu=next(Path(r['path']) for r in before if Path(r['path']).name=='cpu.vhd')
    cpu.write_text(transform(cpu.read_text()))
    after=inputs(output);a={r['path']:r for r in after};b={r['path']:r for r in before}
    if a.keys()!=b.keys() or {p for p in b if a[p]!=b[p]}!={str(cpu)}:
        raise ValueError('Expected one CPU change')
    hold_guard(output,after)
    manifest['quartus_sources']=after
    (output/'fit-inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
    load_source(output,output/'fit-inputs.json')
    return cpu


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(prepare(a.source,a.output))
