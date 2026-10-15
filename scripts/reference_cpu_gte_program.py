"""Translate the mathematical GTE probes to a synthetic PSX reset program.

This is authored diagnostic code, not BIOS/game data. The CPU must execute COP2
transfers and commands, stall for results and store independently known values.
"""
from pathlib import Path
from reference_gte_vectors import write_vectors


def write_program(directory, write_gap=2, memory_checks=False):
    if write_gap not in (0,2): raise ValueError('Diagnostic write gap must be zero or two instructions')
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    vectors=directory/'gte-vectors.txt'; counts=write_vectors(vectors)
    code=[]; expected=[]
    def emit(word): code.append(word & 0xffffffff)
    def constant(reg,value):
        value &= 0xffffffff
        emit(0x3c000000 | reg<<16 | value>>16)
        emit(0x34000000 | reg<<21 | reg<<16 | value&65535)
    constant(8,0x40000000); emit(0x40886000); emit(0); emit(0) # enable COP2
    constant(20,0xa0001000)
    for line in vectors.read_text().splitlines():
        op,addr,value=line.split(); addr=int(addr,16); value=int(value,16)
        if op=='W':
            constant(8,value)
            emit(0x48000000 | (6 if addr&32 else 4)<<21 | 8<<16 | (addr&31)<<11)
            for _ in range(write_gap): emit(0) # isolate the transfer from following GTE access
        elif op=='C': emit(0x4a000000 | value & 0x1ffffff)
        elif op=='R':
            emit(0x48000000 | (2 if addr&32 else 0)<<21 | 9<<16 | (addr&31)<<11)
            emit(0) # MFC2/CFC2 load delay
            emit(0xae890000 | len(expected)*4)
            expected.append(value)
        else: raise ValueError('Unknown GTE operation')
    if memory_checks:
        # Actual scratchpad memory is independent of the optional turbo cache.
        constant(21,0x1f800000); constant(22,0x12345678); emit(0xaeb60000)
        constant(22,0xbeef); emit(0xa6b60002) # SH at +2
        constant(22,0xa5); emit(0xa2b60001) # SB at +1
        for opcode,offset,value in ((0x8c000000,0,0xbeefa578),(0x84000000,2,0xffffbeef),
                                    (0x90000000,1,0xa5),(0x94000000,2,0xbeef)):
            emit(opcode | 21<<21 | 8<<16 | offset); emit(0)
            emit(0xae880000 | len(expected)*4); expected.append(value)
    # Memory COP2 transfers also have a delayed completion path in the CPU.
    constant(21,0xa000f000); constant(8,0xffff8001)
    emit(0xaea80000) # SW r8,0(r21)
    emit(0xcaa10000) # LWC2 VZ0,0(r21)
    emit(0); emit(0)
    emit(0xeaa10004) # SWC2 VZ0,4(r21) -> sign extended
    constant(21,0xa000ff00); constant(8,0x600df00d)
    emit(0xaea80000); emit(0x1000ffff); emit(0)
    program=directory/'cpu-gte-program.hex'; program.write_text(''.join(f'{word:08x}\n' for word in code))
    values=directory/'cpu-gte-expected.hex'; values.write_text(''.join(f'{word:08x}\n' for word in expected))
    return {'program':program,'expected':values,'instructions':len(code),'gte_commands':counts[1],
            'mathematical_checks':counts[2],'expected_checks':len(expected),'scratchpad_checks':4 if memory_checks else 0}
