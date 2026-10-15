"""Authored connected-system RAM/cache-address execution diagnostic."""
from pathlib import Path
import json


def write_program(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    words=[];expected=[]

    def emit(w):words.append(w&0xffffffff)
    def i(op,rt,rs,imm):emit((op<<26)|(rs<<21)|(rt<<16)|(imm&65535))
    def setr(r,v):i(15,r,0,v>>16);i(13,r,r,v&65535)
    def result(r,v):i(43,r,16,0);i(9,16,16,4);expected.append(v&0xffffffff)
    setr(16,0xa0001000)
    # Word/byte/halfword read-after-write over cached/uncached aliases.
    for trial in range(300):
     addr=0xa0010000+(trial%16)*16
     setr(17,addr if trial%2==0 else addr^0x20000000)
     setr(18,addr^0x20000000 if trial%3 else addr)
     value=(trial*0x13a79bdf+0xbeefa578)&0xffffffff
     setr(8,value);i(43,8,17,0)
     i(35,9,18,0);emit(0);result(9,value)
     offset=trial%4;byte=(trial*71+37)&255
     setr(8,byte);i(40,8,17,offset)
     value=(value&~(255<<(offset*8)))|(byte<<(offset*8))
     i(35,9,18,0);emit(0);result(9,value)
     i(36,9,18,offset);emit(0);result(9,byte)
     i(32,9,18,offset);emit(0);result(9,byte if byte<128 else byte-256)
     offset=(trial%2)*2;half=(trial*15767+0x8123)&65535
     setr(8,half);i(41,8,17,offset)
     value=(value&~(65535<<(offset*8)))|(half<<(offset*8))
     i(35,9,18,0);emit(0);result(9,value)
     i(37,9,18,offset);emit(0);result(9,half)
     i(33,9,18,offset);emit(0);result(9,half if half<32768 else half-65536)
    # BIOS-like cached stack counter. A stuck read-after-write would never finish.
    for cached in (False,True):
     setr(29,0x801fffa0 if cached else 0xa01fffa0)
     i(43,0,29,4);loop=len(words)
     i(35,25,29,4);emit(0);i(9,8,25,1);i(43,8,29,4)
     i(35,9,29,4);emit(0);i(10,1,9,240)
     branch=len(words);i(5,1,0,loop-(branch+1));emit(0)
     result(9,240)
    # Install and call a tiny cached-RAM function with a 240-iteration loop.
    code=[]
    def ci(op,rt,rs,imm):code.append((op<<26)|(rs<<21)|(rt<<16)|(imm&65535))
    ci(15,29,0,0x801f);ci(13,29,29,0xffa0);ci(43,0,29,4);start=len(code)
    ci(35,25,29,4);code.append(0);ci(9,8,25,1);ci(43,8,29,4)
    ci(35,9,29,4);code.append(0);ci(10,1,9,240)
    b=len(code);ci(5,1,0,start-b-1);code.append(0)
    code.append((9<<21)|(2<<11)|0x21);code.append((31<<21)|8);code.append(0)
    setr(17,0xa0018000)
    for offset,word in enumerate(code):setr(8,word);i(43,8,17,offset*4)
    setr(17,0x80018000);emit((17<<21)|(31<<11)|9);emit(0);result(2,240)
    # Remaining slots are immediate-store sentinels for the shared fixed-count monitor.
    while len(expected)<2648:
     v=(len(expected)*0x2173e69d)&0xffffffff
     setr(8,v);result(8,v)
    setr(17,0xa000ff00);setr(8,0x600df00d);i(43,8,17,0)
    emit((2<<26)|(((0xbfc00000+len(words)*4)>>2)&0x3ffffff));emit(0)
    program=directory/'program.hex';program.write_text(''.join(f'{w:08x}\n' for w in words))
    results=directory/'expected.hex';results.write_text(''.join(f'{w:08x}\n' for w in expected))
    report={'program':str(program),'expected':str(results),'instructions':len(words),
        'expected_checks':len(expected),'main_ram_checks':2103,'sentinel_checks':545,
        'cpu_cache_hits_verified':False,'game_verified':False,'hardware_verified':False,
        'coverage':['Cached/uncached RAM aliases','Immediate read-after-write','Byte/halfword masks and signed loads',
            '240-iteration stack loops at both aliases','CPU-installed function executed from cached RAM address']}
    (directory/'program.json').write_text(json.dumps(report,indent=2)+'\n')
    return {**report,'program':program,'expected':results}
