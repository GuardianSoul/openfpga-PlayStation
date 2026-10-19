# SPDX-License-Identifier: MIT
"""Authored R3000 program: actual RTPT→COP2/GPR/scratch/RAM→DMA2→GPU."""
from pathlib import Path
import json

def write(directory):
 directory=Path(directory);directory.mkdir(exist_ok=True);words=[];expected=[0x00280026,0x00280052,0x00560026]
 def emit(v):words.append(v&0xffffffff)
 def imm(op,rt,rs,v):emit(op<<26|rs<<21|rt<<16|(v&65535))
 def reg(r,v):imm(15,r,0,v>>16);imm(13,r,r,v)
 def sw(rt,base,offset):imm(43,rt,base,offset)
 def lw(rt,base,offset):imm(35,rt,base,offset);emit(0);emit(0)
 def mtc2(idx,v,control=False):reg(8,v);emit(18<<26|(6 if control else 4)<<21|8<<16|idx<<11)
 def mfc2(rt,idx):emit(18<<26|rt<<16|idx<<11);emit(0);emit(0)
 def mmio(offset,value):reg(8,value);sw(8,20,offset)
 words.extend([0]*20000)
 reg(16,0xa0001000);reg(17,0xa0006000);reg(18,0xa0007000);reg(19,0x1f800000);reg(20,0x1f800000)
 reg(8,0x40000000);emit(16<<26|4<<21|8<<16|12<<11)
 for idx,val in [(0,4096),(1,0),(2,4096),(3,0),(4,4096),(5,0),(6,0),(7,0),(24,32<<16),(25,32<<16),(26,256),(27,0),(28,0)]:mtc2(idx,val,True)
 for idx,val in [(0,17<<16|13),(1,512),(2,17<<16|101),(3,512),(4,109<<16|13),(5,512)]:mtc2(idx,val)
 emit(0x4a080030) # actual RTPT, SF=1; H/Z=1/2 gives both fractions8/16.
 for j in range(3):mfc2(9,12+j);sw(9,16,j*4)
 mfc2(9,12);emit(9<<21|0<<16|10<<11|0x21);sw(10,18,0)
 imm(58,13,18,4) # SWC2 of SXY1 into ordinary main RAM.
 mfc2(9,14);sw(9,19,0) # precision scratchpad transport.
 reg(8,0x201830a0);sw(8,17,0)
 lw(9,18,0);emit(9<<21|0<<16|10<<11|0x25);sw(10,17,4)
 lw(9,18,4);sw(9,17,8)
 lw(9,19,0);sw(9,17,12)
 imm(50,12,18,0);emit(0);emit(0);imm(58,12,17,4) # LWC2/SWC2 provenance.
 emit(18<<26|4<<21|9<<16|14<<11);emit(0);imm(58,14,17,12) # MTC2/SWC2.
 for off,val in [(0x1814,0),(0x1810,0xe1000000),(0x1810,0xe2000000),(0x1810,0xe3000000),(0x1810,0xe401fc7f),(0x1810,0xe5000000),(0x1810,0xe6000000),(0x1810,0x02000000),(0x1810,0),(0x1810,0x00800080),(0x1814,0x04000002),(0x10f0,0x07654b21),(0x10a0,0x00006000),(0x10a4,4),(0x10a8,0x11000001)]:mmio(off,val)
 reg(10,0x01000000);loop=len(words);lw(9,20,0x10a8);emit(9<<21|10<<16|9<<11|0x24);imm(5,0,9,loop-len(words)-1);emit(0)
 reg(8,0x600df00d);reg(17,0xa000ff00);sw(8,17,0);emit(0x1000ffff);emit(0)
 while len(words)%4:emit(0)
 assert len(words)*4<0x20000,'Digital chip fixture ROM window must not alias'
 (directory/'boot.hex32').write_text(''.join(f'{v:08x}\n'for v in words))
 result=dict(words=len(words),expected_coordinates=expected,metadata=0x388,vertices_q4=[(38*16+8,40*16+8),(82*16+8,40*16+8),(38*16+8,86*16+8)],warmup_nops=20000,scope='Authored program only; ordinary GTE projections/CPU transport/DMA2/flatGPU. No game or hardware claim.')
 (directory/'program.json').write_text(json.dumps(result,indent=2)+'\n');return result
