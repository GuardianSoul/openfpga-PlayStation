"""DC-only MDEC probes with exact rank-one IDCT arithmetic and byte packing."""
from pathlib import Path


def pixels(depth, dc, scale, bit15):
    # One nonzero coefficient; both matrix passes have only one contributing term.
    coefficient = max(-1024,min(1023,dc*2))
    luminance = max(-128,min(127,(coefficient*scale*scale)//(1<<32))) + 128
    if depth==0:
        data=bytes([(luminance>>4)*17])*32
    elif depth==1:
        data=bytes([luminance])*64
    elif depth==2:
        data=bytes([luminance])*768
    else:
        channel=min(31,(luminance+4)//8)
        pixel=channel*(1+32+1024)+(bit15<<15)
        data=pixel.to_bytes(2,'little')*256
    return [int.from_bytes(data[i:i+4],'little') for i in range(0,len(data),4)]


def write_vectors(path: Path):
    lines=['B 04 80000000','B 04 60000000','B 00 40000001']
    lines += ['D 00 02020202']*32
    commands=0;words=0
    for scale in (16384,-32768):
        for depth in range(4):
            for qscale in (0,1):
                lines.append('B 00 60000000')
                table=[scale]*8+[0]*56
                for i in range(0,64,2):
                    lines.append(f'D 00 {((table[i]&65535)|((table[i+1]&65535)<<16)):08X}')
                bit15=qscale if depth==3 else 0
                values=(-384,96) if qscale==0 else (40,-216)
                parameters=[]
                for dc in values:
                    parameters.append(0xFE00FE00) # legal padding before each block group
                    for coefficient in ([0,0,dc,dc,dc,dc] if depth>=2 else [dc]):
                        parameters.append(0xFE000000|(qscale<<10)|(coefficient&1023))
                command=(1<<29)|(depth<<27)|(bit15<<25)|len(parameters)
                lines.append(f'B 00 {command:08X}')
                lines += [f'D 00 {value:08X}' for value in parameters]
                # Hold the first output while the second macroblock is queued.
                lines.append('H 00 00001000')
                for dc in values:
                    expected=pixels(depth,dc,scale,bit15)
                    lines += [f'E 00 {value:08X}' for value in expected]
                    words+=len(expected)
                lines.append('F 00 00000000') # no unconsumed output may remain
                commands+=1
    path.write_text('\n'.join(lines)+'\n')
    return commands,commands*2,words
