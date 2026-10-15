"""Authored reset program checking CD-to-main-RAM DMA through the real CPU."""
from pathlib import Path
import json


def write_program(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    words=[];expected=[]
    def emit(word):words.append(word&0xffffffff)
    def i(op,rt,rs,imm):emit((op<<26)|(rs<<21)|(rt<<16)|(imm&65535))
    def setr(reg,value):i(15,reg,0,value>>16);i(13,reg,reg,value&65535)
    def cd_write(offset,value):setr(8,value);i(40,8,20,0x1800+offset)
    def cd_read(offset):i(36,9,20,0x1800+offset);emit(0)
    def ack():cd_write(0,1);cd_write(3,31);cd_write(0,0)
    def irq(wanted):
        cd_write(0,1);setr(10,wanted);loop=len(words)
        cd_read(3);i(12,9,9,7);i(5,10,9,loop-len(words)-1);emit(0)
    def response():
        loop=len(words);cd_read(0);i(12,9,9,32);i(4,0,9,loop-len(words)-1);emit(0);cd_read(1)
    def command(value):cd_write(0,0);cd_write(1,value)
    def mmio_write(offset,value):setr(8,value);i(43,8,20,offset)
    def result(reg,value):i(43,reg,16,0);i(9,16,16,4);expected.append(value&0xffffffff)
    # Allow the fixture's 400 metadata writes to finish after cold reset.
    for _ in range(2000):emit(0)
    setr(16,0xa0001000);setr(20,0x1f800000)
    cd_write(0,1);cd_write(2,31);ack()
    command(0x0a);irq(3);response();ack();irq(2);response();ack()
    cd_write(2,0);command(0x0e);irq(3);response();ack()
    cd_write(2,0);cd_write(2,2);cd_write(2,0);command(2);irq(3);response();ack()
    command(6);irq(3);response();ack();irq(1);response();ack()
    cd_write(0,0);cd_write(3,0x80)
    loop=len(words);cd_read(0);i(12,9,9,64);i(4,0,9,loop-len(words)-1);emit(0)
    mmio_write(0x10f0,0x00008000) # Enable DMA channel 3.
    mmio_write(0x10f4,0x00880000) # Channel 3 interrupt and master enable.
    mmio_write(0x10b0,0x00010000);mmio_write(0x10b4,512)
    mmio_write(0x10b8,0x11000000) # Manual start, incrementing CD-to-RAM.
    setr(10,0x80000000);loop=len(words)
    i(35,9,20,0x10f4);emit(0);emit((9<<21)|(10<<16)|(9<<11)|0x24)
    i(4,0,9,loop-len(words)-1);emit(0)
    command(9);irq(3);response();ack();irq(2);response();ack()
    setr(17,0xa0010000)
    for index in range(512):
        value=sum((((index*4+byte)*29)&255)<<(byte*8) for byte in range(4))
        i(35,9,17,index*4);emit(0);result(9,value)
    i(35,9,20,0x1070);emit(0);i(12,9,9,12);result(9,12)
    setr(10,0x88000000);i(35,9,20,0x10f4);emit(0)
    emit((9<<21)|(10<<16)|(9<<11)|0x24);result(9,0x88000000)
    setr(10,0x01000000);i(35,9,20,0x10b8);emit(0)
    emit((9<<21)|(10<<16)|(9<<11)|0x24);result(9,0)
    while len(expected)<2648:
        value=(len(expected)*0x1379bdf1)&0xffffffff;setr(8,value);result(8,value)
    setr(17,0xa000ff00);setr(8,0x600df00d);i(43,8,17,0);emit(0x1000ffff);emit(0)
    image=b''.join(word.to_bytes(4,'little') for word in words)
    if len(image)>524288:raise ValueError('Diagnostic exceeds boot ROM')
    bios=directory/'authored-reset.bin';bios.write_bytes(image.ljust(524288,b'\0'))
    table=directory/'expected.hex';table.write_text(''.join(f'{value:08x}\n' for value in expected))
    report={'instructions':len(words),'payload_words_checked':512,'irq_dma_checks':3,
        'sentinel_checks':2133,'mount_settle_nops':2000,'actual_cpu_interrupt_handler_verified':False,
        'game_verified':False,'hardware_verified':False}
    (directory/'program.json').write_text(json.dumps(report,indent=2)+'\n')
    return {**report,'bios':bios,'expected':table}
