"""Independent DIV/DIVU HI/LO results plus the cached CPU oracle."""
from pathlib import Path
import random
from cpu_cache_program import Program,write_program as base_program
MASK=0xffffffff
def signed(x):return x-(1<<32) if x>>31 else x
def write_program(directory):
 d=Path(directory);base=base_program(d)
 ram={int(a,16):int(b,16) for a,b in [r.split() for r in (d/'ram.hex').read_text().splitlines()]}
 prior=[int(x,16) for x in (d/'expected.hex').read_text().splitlines()]
 p=Program();p.at(0x80018000);p.const(20,0xa0001000);p.const(22,0xa000ff00);p.const(23,0xa0008000)
 rng=random.Random(7331);edges=[0,1,2,0x7fffffff,0x80000000,0xffffffff,0xfffffffe]
 pairs=[(a,b) for a in edges for b in edges]+[(rng.getrandbits(32),rng.getrandbits(32)) for _ in range(96)]
 for index,(a,b) in enumerate(pairs):
  for op in (0x1a,0x1b):
   x,y=(signed(a),signed(b)) if op==0x1a else (a,b)
   if y:q=abs(x)//abs(y);q=-q if (x<0)!=(y<0) else q;rem=x-q*y
   else:q=(1 if x<0 else -1) if op==0x1a else MASK;rem=x
   p.const(8,a);p.const(9,b);p.emit(8<<21|9<<16|op)
   if index%9==0:p.nop(40)
   p.emit(10<<11|0x10);p.check(10,rem,'DIV HI')
   p.emit(10<<11|0x12);p.check(10,q,'DIV LO')
 count=len(p.expected);p.const(20,0xa0001000+count*4);p.emit(0x08000000|((0x80010008>>2)&0x3ffffff));p.nop()
 assert p.pc<0x80020000 and count*4<0x7000
 for addr,word in p.words.items():
  addr&=0x1fffffff;assert addr not in ram;ram[addr]=word
 ram[0x10000]=0x08000000|((0x80018000>>2)&0x3ffffff);ram[0x10004]=0
 (d/'ram.hex').write_text(''.join(f'{a:08x} {v:08x}\n' for a,v in sorted(ram.items())))
 (d/'expected.hex').write_text(''.join(f'{v&MASK:08x}\n' for v in p.expected+prior))
 return dict(expected_checks=count+len(prior),added_checks=count,existing_checks=len(prior),program_words=len(ram),signed_unsigned_DIV_pairs=len(pairs)*2,seed=7331)
