"""Generated GTE probes using integer formulas, independent of the RTL pipeline.

This is a limited reference assessment, not a complete PSX GTE oracle.
"""
import random


def write_vectors(path):
    operations = []
    commands = checks = 0

    def write(index, value):
        operations.append(f'W {index:02x} {value & 0xFFFFFFFF:08x}\n')

    def read(index, value):
        nonlocal checks
        operations.append(f'R {index:02x} {value & 0xFFFFFFFF:08x}\n')
        checks += 1

    def command(value):
        nonlocal commands
        operations.append(f'C 00 {value:08x}\n')
        commands += 1

    def summarized_flags(value):
        return value | (0x80000000 if value & 0x7F87E000 else 0)

    # Signed/unsigned register readback, FIFO push and leading-sign count.
    for index, value, expected in ((1,0x12348000,0xFFFF8000), (7,0x1234FFFF,65535),
                                    (9,0x1234FFFF,0xFFFFFFFF), (36,0x12348000,0xFFFF8000)):
        write(index,value); read(index,expected)
    for index,value in ((12,0x00020001),(13,0x00040003),(14,0x00060005),(15,0x00080007)):
        write(index,value)
    for index,value in ((12,0x00040003),(13,0x00060005),(14,0x00080007),(15,0x00080007)):
        read(index,value)
    for value in (0,0xFFFFFFFF,1,0xFFFFFFFE,0x80000000,0x7FFFFFFF,0x01000000,0xFF000000):
        write(30,value)
        magnitude = (~value & 0xFFFFFFFF) if value & 0x80000000 else value
        read(31,32-magnitude.bit_length())
    write(28,0x7FFF)
    for index in (9,10,11): read(index,31<<7)
    read(29,0x7FFF)

    rng = random.Random(0x475445)
    # NCLIP: signed triangle determinant. Bound coordinates so intermediate
    # accumulation never overflows; those overflow timings need separate tests.
    triangles = [((0,0),(1,0),(0,1)), ((0,0),(0,1),(1,0)), ((-512,-512),(511,511),(0,0))]
    triangles += [tuple((rng.randrange(-512,512),rng.randrange(-512,512)) for _ in range(3)) for _ in range(125)]
    for (x0,y0),(x1,y1),(x2,y2) in triangles:
        for index,(x,y) in enumerate(((x0,y0),(x1,y1),(x2,y2)),12): write(index,(y & 65535)<<16 | (x & 65535))
        command(0x1400006)
        read(24,x0*(y1-y2)+x1*(y2-y0)+x2*(y0-y1)); read(63,0)

    # SQR: square three signed components, optional 12-bit shift, IR saturation.
    values = [(0,1,-1),(181,182,-182),(-32768,32767,0),(0,0,32767)]
    values += [tuple(rng.randrange(-32768,32768) for _ in range(3)) for _ in range(28)]
    for shift in (0,12):
        for limit in (0,1):
            for components in values:
                for index,value in enumerate(components,9): write(index,value)
                command(0xA00028 | (0x80000 if shift else 0) | limit<<10)
                flags = 0
                for number,value in enumerate(components):
                    squared = value*value >> shift
                    read(25+number,squared); read(9+number,min(squared,32767))
                    if squared > 32767: flags |= 1<<(24-number)
                read(63,summarized_flags(flags))

    # AVSZ3/4: unsigned depths with signed scale and OTZ saturation. Bound the
    # scale to avoid MAC0 overflow so only OTZ saturation contributes flags.
    depths = [([0,1,2,3],4096),([65535]*4,4096),([65535]*4,-4096),([1]*4,-1)]
    depths += [([rng.randrange(65536) for _ in range(4)],rng.randrange(-4096,4097)) for _ in range(60)]
    for count in (3,4):
        for coordinates,scale in depths:
            for index,value in enumerate(coordinates,16): write(index,value)
            write(61 if count==3 else 62,scale); command(0x158002D if count==3 else 0x168002E)
            product = scale*sum(coordinates[4-count:]); depth = product>>12
            read(24,product); read(7,max(0,min(depth,65535)))
            read(63,summarized_flags(1<<18 if not 0<=depth<=65535 else 0))

    # RTPS/RTPT: identity rotation, zero translation and H=SZ at powers of two.
    # The divider is exact at these points (factor 65536), so this oracle does
    # not approximate the hardware's general UNR algorithm. Keep all values
    # away from saturation/overflow and verify FIFO shifts plus depth cueing.
    def packed_xy(x,y): return (y & 65535)<<16 | (x & 65535)
    for triple in (False,True):
        for case in range(32):
            depth=1<<(9+case%5)
            offset=(rng.randrange(-128,129),rng.randrange(-128,129))
            vertices=[(rng.randrange(-256,257),rng.randrange(-256,257),depth) for _ in range(3)]
            for index,value in ((32,4096),(33,0),(34,4096),(35,0),(36,4096),
                                (37,0),(38,0),(39,0),(56,offset[0]<<16),(57,offset[1]<<16),
                                (58,depth),(59,1),(60,0x4000)):
                write(index,value)
            for index,(x,y,z) in enumerate(vertices):
                write(index*2,packed_xy(x,y)); write(index*2+1,z)
            previous_xy=[packed_xy(1,2),packed_xy(3,4),packed_xy(5,6)]
            for index,value in enumerate(previous_xy,12):write(index,value)
            for index,value in enumerate((4,8,12,16),16):write(index,value)
            command(0x80030 if triple else 0x80001)
            transformed=[packed_xy(x+offset[0],y+offset[1]) for x,y,_ in vertices]
            expected_xy=transformed if triple else previous_xy[1:]+transformed[:1]
            expected_z=[16,depth,depth,depth] if triple else [8,12,16,depth]
            for index,value in enumerate(expected_xy,12):read(index,value)
            read(15,expected_xy[-1])
            for index,value in enumerate(expected_z,16):read(index,value)
            x,y,z=vertices[2 if triple else 0]
            for index,value in ((9,x),(10,y),(11,z),(25,x),(26,y),(27,z),
                                (24,0x14000),(8,20),(63,0)):
                read(index,value)
    path.write_text(''.join(operations))
    return len(operations), commands, checks
