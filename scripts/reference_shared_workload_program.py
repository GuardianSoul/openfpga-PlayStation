"""Authored MIPS workload for the real CPU/GPU/SPU/SIO memory-sharing path."""
import json
from pathlib import Path


def write_program(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    words=[];expected=[];labels={};fixups=[]
    def emit(w):words.append(w&0xffffffff)
    def ins(op,rt,rs,imm):emit(op<<26|rs<<21|rt<<16|(imm&65535))
    def setr(r,v):ins(15,r,0,v>>16);ins(13,r,r,v&65535)
    def label(name):
        if name in labels:raise ValueError(name)
        labels[name]=len(words)
    def branch(op,rt,rs,name):fixups.append((len(words),name));ins(op,rt,rs,0);emit(0)
    def store(address,value,half=False):setr(17,address);setr(8,value);ins(41 if half else 43,8,17,0)
    def poll(address,mask,nonzero=False):
        setr(17,address);setr(10,mask);name='poll'+str(len(words));label(name)
        ins(35,9,17,0);emit(0);emit(9<<21|10<<16|9<<11|0x24)
        branch(4 if nonzero else 5,0,9,name)
    def delay(n):
        setr(11,n);name='delay'+str(len(words));label(name)
        ins(9,11,11,-1);branch(5,0,11,name)
    def result(value):
        ins(43,9,16,0);ins(9,16,16,4);expected.append(value)
    def exchange(tx,want=None,port=0):
        store(0xbf801040,tx)
        poll(0xbf801044,2,True);setr(17,0xbf801040)
        ins(35,9,17,0);emit(0);ins(12,9,9,255)
        if want is not None:result(want)
        delay(12);store(0xbf80104a,0x1013|port*0x2000,True)
    def card(port,sector,write=False,rewritten=False):
        store(0xbf80104a,0x1003|port*0x2000,True);delay(8)
        exchange(0x81,port=port);exchange(0x57 if write else 0x52,port=port)
        exchange(0,port=port);exchange(0,port=port)
        exchange(sector>>8,port=port);exchange(sector&255,port=port)
        values=[(i^(0x55+port*0x33))&255 for i in range(128)]
        checksum=(sector>>8)^(sector&255)
        if write:
            for v in values:exchange(v,port=port);checksum^=v
            exchange(checksum,port=port)
            for v in (0x5c,0x5d,0x47):exchange(0,v,port)
        else:
            for v in (0x5c,0x5d,sector>>8,sector&255):exchange(0,v,port)
            for i in range(128):
                v=values[i] if rewritten else (0x6d if i%2 else 0xb7)
                exchange(0,v,port);checksum^=v
            exchange(0,checksum,port);exchange(0,0x47,port)
        store(0xbf80104a,0,True);delay(64)

    setr(16,0xa0001000)
    # 64 constant ADPCM blocks; real channel-4 DMA copies this bank to all voices.
    setr(18,0xa0010000)
    for b in range(64):
        flag=4 if b==0 else 3 if b==63 else 0
        for w in range(4):setr(8,0x77770000|(flag<<8) if w==0 else 0x77777777);ins(43,8,18,b*16+w*4)
    store(0xbf8010f0,0x076d4321)
    store(0xbf801daa,0xc020,True)
    for v in range(24):
        store(0xbf801da6,(v+64)*512,True)
        store(0xbf8010c0,0x10000);store(0xbf8010c4,0x00010100);store(0xbf8010c8,0x01000201)
        poll(0xbf8010c8,0x01000000);poll(0xbf801dae,0x400)
    store(0xbf801d80,0x1000,True);store(0xbf801d82,0x1000,True)
    for v in range(24):
        for off,value in ((0,0x0800),(2,0x7800 if v%2 else 0x0800),(4,0x4000),
                          (6,(v+64)*512),(8,0x00ff),(10,0x1fc0),(14,(v+64)*512)):
            store(0xbf801c00+v*16+off,value,True)
    store(0xbf801daa,0xc000,True);store(0xbf801d88,0xffff,True);store(0xbf801d8a,0xff,True)
    # Testbench releases this authored uncached control word after 512 SPU ticks.
    store(0xa000ff04,0x41554449);poll(0xa000ff08,1,True)
    for value in (0x00000000,0x08000000,0x05000000,0x06c60260,0x07042018,0x03000000):
        store(0xbf801814,value)
    # Four large fills stay queued during the serial-card transactions.
    for unused in range(4):
        for value in (0x020000f8,0,0x01e003f0):poll(0xbf801814,0x04000000,True);store(0xbf801810,value)
    store(0xbf80104a,0x40,True);store(0xbf801048,0x0d,True);store(0xbf80104e,0x88,True)
    for port,sector in ((0,7),(1,1011)):
        card(port,sector);card(port,sector,True);card(port,sector,rewritten=True)
    # Stop new scanout requests before the fixture requests guest pause/drain.
    store(0xbf801814,0x03000001);store(0xa000ff00,0x600df00d)
    name='done';label(name);branch(4,0,0,name)
    for index,name in fixups:
        offset=labels[name]-index-1
        if not -32768<=offset<32768:raise ValueError('Branch out of range')
        words[index]=(words[index]&0xffff0000)|(offset&65535)
    program=directory/'program.hex';program.write_text(''.join(f'{w:08x}\n' for w in words))
    bytefile=directory/'program.hex8';bytefile.write_text(''.join(f'{b:02x}\n' for w in words for b in w.to_bytes(4,'little')))
    results=directory/'expected.hex';results.write_text(''.join(f'{v:08x}\n' for v in expected))
    report=dict(instructions=len(words),expected_checks=len(expected),SPU_voices=24,ADPCM_blocks_per_voice=64,
                SPU_pitch=0x4000,SPU_DMA_words=24*256,GPU_large_fills=4,GPU_fill_size=[1008,480],card_sectors=[7,1011],
                control_word='Author-only bench stimulus at uncached RAM ff08 after 512 SPU ticks; not APF delivery',
                hardware_verified=False,game_verified=False)
    (directory/'program.json').write_text(json.dumps(report,indent=2)+'\n')
    return dict(report,program=program,bytes=bytefile,expected=results)
