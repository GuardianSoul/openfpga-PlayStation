"""Synthetic instruction programs and a byte-memory architectural oracle.

The oracle executes symbolic instructions, not RTL states or decoded bus words.
It uses Python integer multiply/divide rather than the hardware's serial units.
"""
from dataclasses import dataclass
import random

MASK = 0xFFFFFFFF
START = 0xBFC00400
RESET = 0xBFC00000
STATUS = 0x10900000
DATA = 0x4000
FAULT = 0xFFFC
R_OPS = {'sll':0, 'srl':2, 'sra':3, 'sllv':4, 'srlv':6, 'srav':7,
         'jr':8, 'jalr':9, 'syscall':12, 'break':13, 'mfhi':16, 'mthi':17,
         'mflo':18, 'mtlo':19, 'mult':24, 'multu':25, 'div':26, 'divu':27,
         'add':32, 'addu':33, 'sub':34, 'subu':35, 'and':36, 'or':37,
         'xor':38, 'nor':39, 'slt':42, 'sltu':43}
I_OPS = {'beq':4, 'bne':5, 'blez':6, 'bgtz':7, 'addi':8, 'addiu':9,
         'slti':10, 'sltiu':11, 'andi':12, 'ori':13, 'xori':14, 'lui':15,
         'lb':32, 'lh':33, 'lwl':34, 'lw':35, 'lbu':36, 'lhu':37, 'lwr':38,
         'sb':40, 'sh':41, 'swl':42, 'sw':43, 'swr':46, 'lwc2':50, 'swc2':58}


def signed(value):
    return value - 0x100000000 if value & 0x80000000 else value


def encode(name, args):
    if name == 'nop':
        return 0
    if name == 'invalid':
        return 0xFC000000
    if name in R_OPS:
        if name in ('sll', 'srl', 'sra'):
            rd, rt, shift = args; rs = 0
        elif name in ('mfhi', 'mflo'):
            rd, = args; rs = rt = shift = 0
        elif name in ('mthi', 'mtlo', 'jr'):
            rs, = args; rd = rt = shift = 0
        elif name in ('mult', 'multu', 'div', 'divu'):
            rs, rt = args; rd = shift = 0
        elif name in ('syscall', 'break'):
            rs = rt = rd = shift = 0
        elif name == 'jalr':
            rd, rs = args; rt = shift = 0
        else:
            rd, rs, rt = args; shift = 0
        return rs<<21 | rt<<16 | rd<<11 | shift<<6 | R_OPS[name]
    if name in ('bltz', 'bgez', 'bltzal', 'bgezal'):
        rs, displacement = args
        rt = {'bltz':0, 'bgez':1, 'bltzal':16, 'bgezal':17}[name]
        return 1<<26 | rs<<21 | rt<<16 | (displacement & 65535)
    if name in ('j', 'jal'):
        return (2 if name == 'j' else 3)<<26 | (args[0]>>2 & 0x3FFFFFF)
    if name in ('mfc0', 'mtc0', 'mfc2', 'cfc2', 'mtc2', 'ctc2'):
        rt, rd = args
        rs = {'mfc0':0, 'mtc0':4, 'mfc2':0, 'cfc2':2, 'mtc2':4, 'ctc2':6}[name]
        return (16 if name.endswith('0') else 18)<<26 | rs<<21 | rt<<16 | rd<<11
    if name == 'rfe':
        return 0x42000010
    if name == 'cop2':
        return 0x4A000000 | args[0]
    if name == 'cop1':
        return 0x44000000
    if name == 'lui':
        rt, imm = args; rs = 0
    else:
        rt, rs, imm = args
    return I_OPS[name]<<26 | rs<<21 | rt<<16 | (imm & 65535)


class Program:
    def __init__(self, name):
        self.name = name; self.instructions = {RESET:('j',(START,)), RESET+4:('nop',())}
        self.position = START; self.data = {}; self.irq_at = {}; self.assertions = {}

    def emit(self, name, *args):
        self.instructions[self.position] = (name, args); self.position += 4

    def constant(self, reg, value):
        self.emit('lui', reg, (value>>16)&65535); self.emit('ori', reg, reg, value&65535)


@dataclass
class Fault(Exception):
    code: int
    address: int | None = None
    coprocessor: int = 0


class Oracle:
    def __init__(self, program):
        self.program = program; self.regs = [0]*32; self.cp0 = [0]*32
        self.cp0[12] = STATUS; self.cp0[15] = 2
        self.pc = RESET; self.following = RESET+4; self.delay = False; self.origin = 0
        self.pending = None; self.hi = self.lo = self.irq = 0
        self.memory = {}; self.cop2 = [[0]*32 for _ in range(2)]
        for address, word in program.data.items():
            for lane in range(4): self.memory[address+lane] = word>>(lane*8)&255

    def cause(self):
        return (self.cp0[13] & ~0xFC00) | self.irq<<10

    def byte(self, address):
        return self.memory.get(address, (address*17+(address>>8)*23+0x5A)&255)

    def read(self, address):
        address &= 0x1FFFFFFF
        if address & ~3 == FAULT or address >= 0x10000: raise Fault(7)
        return sum(self.byte((address & ~3)+lane)<<(lane*8) for lane in range(4))

    def step(self):
        pc = self.pc; self.irq = self.program.irq_at.get(pc, 0)
        name, args = self.program.instructions.get(pc, ('nop', ()))
        encoded = encode(name, args); writes = {}; new_load = None; branch = False; target = self.following+4
        exception = 0; code = 0
        def write(index, value):
            if index: writes[index] = value & MASK
        def load(index, value):
            nonlocal new_load
            if index: new_load = (index, value & MASK)
        try:
            if self.cp0[12]&1 and (self.cause() & self.cp0[12] & 0xFF00): encoded = 0; raise Fault(0)
            if pc&3 or (self.cp0[12]&2 and pc&0x80000000): encoded = 0; raise Fault(4, pc)
            if pc&0x1FFFFFFF == FAULT: encoded = 0; raise Fault(6)
            if name == 'nop': pass
            elif name in ('invalid', 'syscall', 'break', 'cop1'):
                raise Fault({'invalid':10,'syscall':8,'break':9,'cop1':11}[name], coprocessor=1 if name=='cop1' else 0)
            elif name in ('sll','srl','sra','sllv','srlv','srav'):
                rd, a, b = args
                value, shift = (self.regs[b], self.regs[a]&31) if name.endswith('v') else (self.regs[a], b)
                write(rd, value<<shift if name.startswith('sll') else signed(value)>>shift if name.startswith('sra') else value>>shift)
            elif name in ('add','addu','sub','subu','and','or','xor','nor','slt','sltu'):
                rd, rs, rt = args; a,b = self.regs[rs],self.regs[rt]
                value = {'add':lambda:signed(a)+signed(b), 'addu':lambda:a+b,
                         'sub':lambda:signed(a)-signed(b), 'subu':lambda:a-b,
                         'and':lambda:a&b, 'or':lambda:a|b, 'xor':lambda:a^b,
                         'nor':lambda:~(a|b), 'slt':lambda:int(signed(a)<signed(b)), 'sltu':lambda:int(a<b)}[name]()
                if name in ('add','sub') and not -2**31 <= value < 2**31: raise Fault(12)
                write(rd,value)
            elif name in ('mfhi','mflo'): write(args[0], self.hi if name=='mfhi' else self.lo)
            elif name in ('mthi','mtlo'):
                if name=='mthi': self.hi=self.regs[args[0]]
                else: self.lo=self.regs[args[0]]
            elif name in ('mult','multu','div','divu'):
                a,b = (self.regs[index] for index in args)
                if name in ('mult','div'): a,b=signed(a),signed(b)
                if name.startswith('mult'):
                    product=(a*b)&((1<<64)-1); self.hi=product>>32; self.lo=product&MASK
                elif b==0: self.hi=a&MASK; self.lo=1 if a<0 else MASK
                else:
                    q=abs(a)//abs(b); q=-q if (a<0)!=(b<0) else q
                    self.lo=q&MASK; self.hi=(a-q*b)&MASK
            elif name in ('addi','addiu','slti','sltiu','andi','ori','xori','lui'):
                if name=='lui': write(args[0],args[1]<<16)
                else:
                    rt,rs,imm=args; a=self.regs[rs]; simm=imm&65535; simm=simm-65536 if simm&32768 else simm
                    value = {'addi':lambda:signed(a)+simm, 'addiu':lambda:a+simm,
                             'slti':lambda:int(signed(a)<simm), 'sltiu':lambda:int(a<(simm&MASK)),
                             'andi':lambda:a&(imm&65535), 'ori':lambda:a|(imm&65535), 'xori':lambda:a^(imm&65535)}[name]()
                    if name=='addi' and not -2**31<=value<2**31: raise Fault(12)
                    write(rt,value)
            elif name in ('j','jal','jr','jalr','beq','bne','blez','bgtz','bltz','bgez','bltzal','bgezal'):
                branch=True; taken=True
                if name in ('j','jal'): destination=args[0]
                elif name in ('jr','jalr'): destination=self.regs[args[-1]]
                else:
                    if name in ('beq','bne'):
                        rt,rs,imm=args; taken=(self.regs[rs]==self.regs[rt]) == (name=='beq')
                    else:
                        rs,imm=args if name.startswith(('bltz','bgez')) else (args[1],args[2])
                        a=signed(self.regs[rs]); taken=a<0 if name.startswith('bltz') else a>=0 if name.startswith('bgez') else a<=0 if name=='blez' else a>0
                    simm=imm&65535; simm=simm-65536 if simm&32768 else simm
                    destination=(pc+4+simm*4)&MASK
                if taken: target=destination
                if name in ('jal','bltzal','bgezal'): write(31,pc+8)
                if name=='jalr': write(args[0],pc+8)
            elif name in ('mfc0','mtc0','rfe'):
                if self.cp0[12]&2 and not self.cp0[12]&(1<<28): raise Fault(11)
                if name=='rfe': self.cp0[12]=(self.cp0[12]&~15)|((self.cp0[12]>>2)&15)
                else:
                    rt,rd=args
                    if name=='mfc0': load(rt,self.cause() if rd==13 else self.cp0[rd])
                    elif rd in (3,5,7,9,11): self.cp0[rd]=self.regs[rt]
                    elif rd==12: self.cp0[12]=self.regs[rt]&0xF27FFF3F
                    elif rd==13: self.cp0[13]=(self.cp0[13]&~0x300)|(self.regs[rt]&0x300)
            elif name in ('mfc2','cfc2','mtc2','ctc2','cop2'):
                if not self.cp0[12]&(1<<30): raise Fault(11,coprocessor=2)
                if name=='cop2':
                    if args[0]==0x1FFFFFF: raise Fault(10)
                    self.cop2[0][31]=(args[0]^0xA5A5A5A5)&MASK
                else:
                    rt,rd=args; bank=int(name.startswith(('cfc','ctc')))
                    if name.startswith(('mfc','cfc')): load(rt,self.cop2[bank][rd])
                    else: self.cop2[bank][rd]=self.regs[rt]
            else:
                rt,rs,imm=args; address=(self.regs[rs]+(imm if imm<32768 else imm-65536))&MASK
                store=name.startswith('s'); alignment=2 if name in ('lh','lhu','sh') else 4 if name in ('lw','sw','lwc2','swc2') else 1
                if name in ('lwc2','swc2') and not self.cp0[12]&(1<<30): raise Fault(11,coprocessor=2)
                if address%alignment or (self.cp0[12]&2 and address&0x80000000): raise Fault(5 if store else 4,address)
                physical=address&0x1FFFFFFF; word=self.read(address); offset=physical%4
                if store:
                    data=self.cop2[0][rt] if name=='swc2' else self.regs[rt]
                    if name in ('sw','swc2'): lanes=range(4); first=0
                    elif name=='sb': lanes=range(offset,offset+1); first=0
                    elif name=='sh': lanes=range(offset,offset+2); first=0
                    elif name=='swl': lanes=range(offset+1); first=3-offset
                    else: lanes=range(offset,4); first=0
                    for count,lane in enumerate(lanes): self.memory[(physical&~3)+lane]=data>>((first+count)*8)&255
                elif name=='lwc2': self.cop2[0][rt]=word
                elif name in ('lwl','lwr'):
                    merged=self.pending[1] if self.pending and self.pending[0]==rt else self.regs[rt]
                    values=list(merged.to_bytes(4,'little')); raw=list(word.to_bytes(4,'little'))
                    if name=='lwl': values[3-offset:4]=raw[:offset+1]
                    else: values[:4-offset]=raw[offset:]
                    load(rt,int.from_bytes(bytes(values),'little'))
                else:
                    width=1 if name in ('lb','lbu') else 2 if name in ('lh','lhu') else 4
                    value=word>>(offset*8)&((1<<(width*8))-1)
                    if name in ('lb','lh') and value&(1<<(width*8-1)): value-=1<<(width*8)
                    load(rt,value)
            if self.pending and self.pending[0] not in writes and (not new_load or new_load[0]!=self.pending[0]):
                self.regs[self.pending[0]]=self.pending[1]
            for index,value in writes.items(): self.regs[index]=value
            self.pending=new_load
            self.pc=self.following; self.following=target&MASK
            self.delay=branch
            if branch: self.origin=pc
        except Fault as fault:
            exception=1; code=fault.code
            if self.pending: self.regs[self.pending[0]]=self.pending[1]
            self.pending=None
            self.cp0[13]=(int(self.delay)<<31)|(fault.coprocessor<<28)|(self.irq<<10)|(self.cp0[13]&0x300)|(code<<2)
            self.cp0[14]=self.origin if self.delay else pc
            if fault.address is not None: self.cp0[8]=fault.address
            vector=0xBFC00180 if self.cp0[12]&(1<<22) else 0x80000080
            self.cp0[12]=(self.cp0[12]&~63)|((self.cp0[12]&15)<<2)
            self.pc=vector; self.following=vector+4; self.delay=False
        return [self.irq,pc,encoded,exception,code,self.pc,self.hi,self.lo,self.cp0[12],self.cause(),self.cp0[14],self.cp0[8],*self.regs]


def programs():
    result=[]
    def new(name):
        p=Program(name); result.append(p); return p
    p=new('alu-random'); rng=random.Random(0x50535807)
    for reg in range(1,32): p.constant(reg,rng.getrandbits(32))
    for _ in range(1000):
        name=rng.choice(('addu','subu','and','or','xor','nor','slt','sltu','sllv','srlv','srav','sll','srl','sra','addiu','slti','sltiu','andi','ori','xori'))
        rd,rs,rt=(rng.randrange(32) for _ in range(3))
        if name in ('sll','srl','sra'): p.emit(name,rd,rs,rng.randrange(32))
        elif name in I_OPS: p.emit(name,rd,rs,rng.randrange(65536))
        else: p.emit(name,rd,rs,rt)
    p=new('load-delay'); p.data[DATA]=0x80FF7F01; p.data[DATA+4]=0x12345678
    p.constant(1,DATA); p.constant(5,0xAA)
    p.emit('lw',5,1,0); p.emit('addu',6,5,0); p.emit('nop')
    p.emit('lw',5,1,0); p.emit('addiu',5,0,0xBB); p.emit('nop')
    p.emit('lw',5,1,0); p.emit('lw',5,1,4); p.emit('addu',7,5,0); p.emit('nop')
    p.assertions={5:0x12345678,6:0xAA,7:0xBB}
    p=new('memory-lanes'); p.constant(1,DATA); p.data[DATA]=0x80FF7F01; p.data[DATA+4]=0x87654321
    for name in ('lb','lbu','lh','lhu','lw'):
        for offset in range(0,4,2 if name in ('lh','lhu') else 4 if name=='lw' else 1): p.emit(name,2,1,offset); p.emit('nop')
    for offset in range(4):
        p.constant(3,0xAABBCCDD); p.emit('lwl',3,1,offset+3); p.emit('lwr',3,1,offset); p.emit('nop')
        p.emit('sw',3,1,0x100+offset*4); p.emit('lw',4,1,0x100+offset*4); p.emit('nop')
    for name in ('sb','sh','swl','swr'):
        for offset in range(0,4,2 if name=='sh' else 1):
            p.constant(2,0xB17E4AC9); p.emit(name,2,1,offset); p.emit('lw',3,1,0); p.emit('nop')
    p=new('multiply-divide'); values=[0,1,MASK,0x80000000,0x7FFFFFFF,2,0xFFFFFFFE]
    pairs=[(a,b) for a in values for b in values]+[(rng.getrandbits(32),rng.getrandbits(32)) for _ in range(50)]
    for a,b in pairs:
        p.constant(1,a); p.constant(2,b)
        for name in ('mult','multu','div','divu'): p.emit(name,1,2); p.emit('mfhi',3); p.emit('mflo',4)
    p.emit('mthi',1); p.emit('mtlo',2); p.emit('mfhi',5); p.emit('mflo',6)
    p=new('branches'); p.constant(18,0); p.constant(19,0)
    for name in ('beq','bne','blez','bgtz','bltz','bgez','bltzal','bgezal'):
        for value in (1,MASK,0):
            p.constant(1,value)
            if name in ('bltz','bgez','bltzal','bgezal'): p.emit(name,1,2)
            else: p.emit(name,0,1,2)
            p.emit('addiu',19,19,1); p.emit('addiu',18,18,1); p.emit('nop')
    for name in ('j','jal'):
        target=p.position+12; p.emit(name,target); p.emit('addiu',19,19,1); p.emit('invalid'); p.emit('nop')
    for name in ('jr','jalr'):
        target=p.position+20; p.constant(1,target)
        p.emit(name,1) if name=='jr' else p.emit(name,5,1)
        p.emit('addiu',19,19,1); p.emit('invalid'); p.emit('nop')
    p.assertions={19:28}
    p=new('cop0'); p.emit('mfc0',1,15); p.emit('addu',2,1,0); p.emit('nop'); p.constant(3,MASK); p.emit('mtc0',3,15)
    p.emit('mfc0',4,15); p.emit('nop'); p.emit('mtc0',3,13); p.emit('mfc0',5,13); p.emit('nop')
    p.constant(6,0x14); p.emit('mtc0',6,12); p.emit('rfe'); p.emit('nop'); p.assertions={1:2,2:0,4:2,5:0x300}
    p=new('cop2-transport'); p.constant(1,STATUS|0x40000000); p.emit('mtc0',1,12); p.constant(2,0xAABBCCDD)
    p.emit('mtc2',2,3); p.emit('ctc2',2,4); p.emit('mfc2',5,3); p.emit('addu',6,5,0); p.emit('nop')
    p.emit('cfc2',7,4); p.emit('nop'); p.constant(1,DATA); p.data[DATA]=0x87654321
    p.emit('lwc2',8,1,0); p.emit('swc2',8,1,4); p.emit('lw',9,1,4); p.emit('nop')
    p.emit('cop2',0x123456); p.emit('mfc2',10,31); p.emit('nop'); p.assertions={5:0xAABBCCDD,6:0,7:0xAABBCCDD,9:0x87654321,10:0xA5B791F3}
    for name in ('syscall','break','invalid','cop1','mfc2'):
        p=new('exception-'+name); p.emit(name,1,2) if name=='mfc2' else p.emit(name)
    for name,a,b in (('add',0x7FFFFFFF,1),('sub',0x80000000,1),('addi',0x7FFFFFFF,1)):
        p=new('overflow-'+name); p.constant(1,a); p.constant(2,b)
        p.emit(name,3,1,1) if name=='addi' else p.emit(name,3,1,2)
    for name in ('lh','lhu','lw','sh','sw'):
        p=new('misaligned-'+name); p.constant(1,DATA); p.emit(name,2,1,1)
    for name in ('lw','sw'):
        p=new('bus-error-'+name); p.constant(1,FAULT); p.emit(name,2,1,0)
    for address in (START+1,0x80000000+FAULT):
        p=new(f'fetch-error-{address:x}'); p.constant(1,address); p.emit('jr',1); p.emit('nop')
    for taken in (True,False):
        p=new('delay-exception-'+str(taken)); p.constant(1,1); p.emit('beq',1 if taken else 0,1,2); p.emit('syscall'); p.emit('nop')
    p=new('bev-exception'); p.constant(1,STATUS|0x400000); p.emit('mtc0',1,12); p.emit('break')
    p=new('irq'); p.constant(1,STATUS|0x401); p.emit('mtc0',1,12); p.irq_at[p.position]=1; p.emit('nop')
    p=new('software-irq'); p.constant(1,0x100); p.emit('mtc0',1,13); p.constant(1,STATUS|0x101); p.emit('mtc0',1,12); p.emit('nop')
    p=new('cop2-error'); p.constant(1,STATUS|0x40000000); p.emit('mtc0',1,12); p.emit('cop2',0x1FFFFFF)
    return result


def slot(address):
    physical=address&0x1FFFFFFF
    return physical//4 if physical<0x10000 else 16384+(physical-0x1FC00000)//4


def write_programs(directory):
    index=[]; total=0
    for number,program in enumerate(programs()):
        oracle=Oracle(program); traces=[]
        for _ in range(5000):
            traces.append(oracle.step())
            if traces[-1][3] or oracle.pc==program.position: break
        else: raise AssertionError(f'{program.name}: program did not finish')
        for reg,value in program.assertions.items():
            assert oracle.regs[reg]==value, (program.name,reg,hex(oracle.regs[reg]),hex(value))
        name=f'case-{number}'
        memory=dict(program.data)
        for address,(operation,args) in program.instructions.items(): memory[address]=encode(operation,args)
        (directory/(name+'.hex')).write_text(''.join(f'@{slot(address):x}\n{word:08x}\n' for address,word in sorted(memory.items())))
        (directory/(name+'.trace')).write_text(''.join(' '.join(f'{value:x}' for value in trace)+'\n' for trace in traces))
        index.append((name,program.name,len(traces))); total+=len(traces)
    (directory/'index.txt').write_text(''.join(f'{name} {label} {count}\n' for name,label,count in index))
    return len(index),total


def write_smoke_program(directory):
    """A generated reset ROM for the real CPU/CDC/controller/pin simulation."""
    p=Program('pocket-reset-smoke')
    p.constant(1,0x1000); p.constant(2,0xCAFEBABE); p.emit('sw',2,1,0)
    for base,loaded in ((3,4),(6,7),(8,9)):
        p.constant(base,{3:0x80001000,6:0xA0001000,8:0x00601000}[base]); p.emit('lw',loaded,base,0)
        p.emit('addu',5,4,0) if base==3 else p.emit('nop')
        p.emit('nop')
    p.constant(10,0x1F800000); p.emit('sw',2,10,0); p.emit('lb',11,10,3); p.emit('nop')
    p.emit('lhu',12,10,2); p.emit('nop'); p.constant(13,0x55); p.emit('sb',13,1,1)
    p.emit('lw',14,1,0); p.emit('nop'); p.constant(15,0x8899); p.emit('sh',15,1,2)
    p.constant(16,0x22334455); p.emit('swl',16,1,1); p.emit('swr',16,1,2); p.emit('lw',17,1,0); p.emit('nop')
    p.constant(18,0xDEADBEEF); p.constant(19,0x1F801080); p.emit('sh',18,19,0)
    p.constant(19,0x1F801070); p.emit('lw',19,19,0); p.emit('nop')
    p.constant(20,0xFFFE0130); p.constant(21,0x804); p.emit('sw',21,20,0); p.emit('lw',21,20,0); p.emit('nop')
    p.constant(22,START); p.emit('sw',2,22,0) # BIOS writes must leave the loaded ROM intact.
    p.constant(23,0x1F802040); p.constant(24,0xD00DFEED); p.emit('sw',24,23,0)
    expected=[0]*32
    for reg,value in {1:0x1000,2:0xCAFEBABE,3:0x80001000,4:0xCAFEBABE,5:0,
                      6:0xA0001000,7:0xCAFEBABE,8:0x00601000,9:0xCAFEBABE,
                      10:0x1F800000,11:0xFFFFFFCA,12:0xCAFE,13:0x55,14:0xCAFE55BE,
                      15:0x8899,16:0x22334455,17:0x44552233,18:0xDEADBEEF,
                      19:0x0BADF00D,20:0xFFFE0130,21:0x804,22:START,23:0x1F802040,24:0xD00DFEED}.items():
        expected[reg]=value
    count=len(p.instructions);done_pc=p.position-4
    lines=[f'{count} {done_pc:x} {encode(*p.instructions[START]):x}\n']
    for address,instruction in sorted(p.instructions.items()):
        lines.append(f'{0x800000+(address-RESET)//2:x} {encode(*instruction):x}\n')
    (directory/'smoke.txt').write_text(''.join(lines))
    (directory/'smoke-regs.hex').write_text(''.join(f'{value:08x}\n' for value in expected))
    return count
