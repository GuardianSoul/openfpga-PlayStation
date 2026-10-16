"""Authored MIPS arithmetic/shift/overflow prelude plus existing cache oracle.

No BIOS/game assets. The original cached body and exception handler remain
byte-exact beyond its first two r20 setup words; result addresses advance.
"""
from pathlib import Path
import random
from cpu_cache_program import Program,write_program as cached_program
MASK=0xffffffff
def signed(x):return x-(1<<32) if x>>31 else x
def write_program(directory):
 directory=Path(directory);old=cached_program(directory);ram={int(a,16):int(b,16) for a,b in [r.split() for r in (directory/'ram.hex').read_text().splitlines()]}
 prior=[int(x,16) for x in (directory/'expected.hex').read_text().splitlines()]
 p=Program();p.at(0x80018000);p.const(20,0xa0001000);p.const(22,0xa000ff00);p.const(23,0xa0008000)
 census={};rng=random.Random(9127)
 def emit_case(word,result,name,overflow=False,epc=None):
  if overflow:p.const(10,0x13579bdf)
  pc=p.pc;p.emit(word)
  if overflow:
   p.nop(3);p.check(10,0x13579bdf,name+' destination suppressed')
   p.lw(11,23);p.nop();p.emit(0x316b007c);p.check(11,0x30,name+' overflow Cause')
   p.lw(11,23,4);p.nop();p.check(11,pc if epc is None else epc,name+' overflow EPC')
  else:p.check(10,result,name)
  census[name]=census.get(name,0)+1
 edges=[0,1,2,0x7fffffff,0x80000000,0xffffffff,0x80000001,0x7ffffffe]
 pairs=[(a,b) for a in edges for b in edges]+[(rng.getrandbits(32),rng.getrandbits(32)) for _ in range(24)]
 for a,b in pairs:
  p.const(8,a);p.const(9,b)
  for fn,name,value in [(0x20,'ADD',signed(a)+signed(b)),(0x21,'ADDU',a+b),(0x22,'SUB',signed(a)-signed(b)),(0x23,'SUBU',a-b),(0x2a,'SLT',int(signed(a)<signed(b))),(0x2b,'SLTU',int(a<b))]:
   overflow=name in ('ADD','SUB') and not -(1<<31)<=value<(1<<31)
   emit_case(8<<21|9<<16|10<<11|fn,value&MASK,name,overflow)
 for a in edges:
  p.const(8,a)
  for imm in (0,1,0x7fff,0x8000,0xffff):
   simm=imm-(1<<16) if imm>>15 else imm
   for op,name,value in [(8,'ADDI',signed(a)+simm),(9,'ADDIU',a+simm),(10,'SLTI',int(signed(a)<simm)),(11,'SLTIU',int(a<(simm&MASK)))]:
    emit_case(op<<26|8<<21|10<<16|imm,value&MASK,name,name=='ADDI' and not -(1<<31)<=value<(1<<31))
 for a in (0,1,0x80000000,0xffffffff,0x12345678,0x87654321):
  p.const(9,a)
  for amount in range(32):
   p.const(8,0xabc00000|amount)
   for fn,name,value in [(0,'SLL',a<<amount),(2,'SRL',a>>amount),(3,'SRA',signed(a)>>amount),(4,'SLLV',a<<amount),(6,'SRLV',a>>amount),(7,'SRAV',signed(a)>>amount)]:
    word=(9<<16|10<<11|amount<<6|fn) if fn<4 else (8<<21|9<<16|10<<11|fn)
    emit_case(word,value&MASK,name)
 # Keep the existing cached/IRQ/exception/coherency program byte-exact after
 # its first two r20 setup words; advance its result base past new checks.
 count=len(p.expected);p.const(20,0xa0001000+count*4);p.emit(0x08000000|((0x80010008>>2)&0x3ffffff));p.nop()
 assert p.pc<0x80020000 and count*4<0x7000
 for addr,word in p.words.items():
  addr&=0x1fffffff
  assert addr not in ram
  ram[addr]=word
 ram[0x10000]=0x08000000|((0x80018000>>2)&0x3ffffff);ram[0x10004]=0
 (directory/'ram.hex').write_text(''.join(f'{a:08x} {v:08x}\n' for a,v in sorted(ram.items())))
 (directory/'expected.hex').write_text(''.join(f'{v&MASK:08x}\n' for v in p.expected+prior))
 return dict(expected_checks=count+len(prior),added_checks=count,existing_checks=len(prior),instruction_census=census,program_words=len(ram),seed=9127)
