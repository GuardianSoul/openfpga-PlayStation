"""Byte-memory oracle for the pinned pgate memory-facing port, not its CPU."""
import random


class ByteMemory:
    def __init__(self):
        self.written = {}

    def byte(self, address):
        return self.written.get(address, (address * 17 + (address >> 8) * 23 + 0x5A) & 255)

    def read(self, address, count):
        return sum(self.byte(address + lane) << (8 * lane) for lane in range(count))

    def write(self, address, count, value):
        for lane in range(count):
            self.written[address + lane] = (value >> (lane * 8)) & 255


def system_vectors():
    memory = ByteMemory()
    cases = []

    def add(kind, address, count, data=0):
        # Define memory effects in bytes independently of the halfword bridge.
        bios = kind == 2
        base = 0x1000000 if bios else 0
        local = address & (0x7FFFF if bios else 0x1FFFFF)
        aligned = local & (~1 if count in (1, 2) else ~3)
        fetched = 2 if count in (1, 2) else 4 if bios else count
        valid = count in ((1, 2, 4) if kind in (1, 2) else (1, 2, 4, 8, 12, 16))
        valid = valid and aligned + fetched <= (0x80000 if bios else 0x200000)
        expected = 0
        if valid:
            if kind == 1:
                destination = local if count == 1 else local & ~1 if count == 2 else local & ~3
                memory.write(base + destination, count, data)
            else:
                expected = memory.read(base + aligned, fetched)
                if count in (1, 2) and local & 2:
                    expected <<= 16  # R3000 retains its own A[1:0] lane selection.
        halfwords = fetched // 2 if valid else 0
        mask = (2 if local & 1 else 1) if count == 1 else 3 if count == 2 else 15
        payload = data << 8 if count == 1 and local & 1 else data
        cases.append((kind, address, count, data, not valid, expected,
                      (base + aligned) // 2, halfwords, mask, payload))

    addresses = [0, 1, 2, 3, 1022, 1023, 1024, 2046, 2047, 2048, 4092, 8191,
                 0x1FFFF0, 0x1FFFF1, 0x1FFFF2, 0x1FFFF3, 0x1FFFFE, 0x1FFFFF]
    for address in addresses:
        for count in (1, 2, 4, 8, 12, 16):
            add(0, address, count)
        for count in (1, 2, 4):
            add(1, address, count, 0xA17EC35B ^ address)
            add(0, address & ~3, 4)
    for address in [0, 1, 2, 3, 1022, 2047, 8191, 0x7FFFE, 0x7FFFF, 0x1FFFFF]:
        for count in (1, 2, 4):
            add(2, address, count)
    rng = random.Random(0x50535806)
    for _ in range(500):
        address = rng.randrange(0x3F00)
        count = rng.choice((1, 2, 4))
        add(1, address, count, rng.getrandbits(32))
        add(0, address, count)
        add(0, address, rng.choice((4, 8, 12, 16)))
        add(2, rng.randrange(0x3F00), rng.choice((1, 2, 4)))
    for kind in (0, 1, 2):
        for count in (0, 3, 5, 7, 9, 15, 17, 31):
            add(kind, 3, count, 0xDEADBEEF)
    return cases


def gpu_vectors():
    memory = ByteMemory()
    cases = []

    def add(kind, address, count=1, data=0):
        base = 0x200000
        valid = kind != 0 or (1 <= count <= 4 and address + count <= 0x80000)
        expected = 0
        if valid and kind == 0:
            expected = memory.read(base + address * 2, count * 2)
        elif kind in (1, 3):  # single pixel, optionally accompanied by flush
            memory.write(base + address * 2, 2, data)
        cases.append((kind, address, count, data, not valid, expected,
                      (base // 2) + address, (count if kind == 0 else 1) if valid else 0))

    for address in (0, 1, 2, 1023, 1024, 2047, 3071, 4095, 0x7FFFC, 0x7FFFF):
        for count in (1, 2, 3, 4):
            add(0, address, count)
        add(1, address, data=0xC35A ^ (address & 0xFFFF))
        add(0, address)
        add(2, address)  # flush-only must produce no memory request
    add(1, 123, data=0xBEEF)
    add(1, 123, data=0xBEEF)  # same fields on two distinct requests
    add(0, 123)
    rng = random.Random(0x50535807)
    for index in range(600):
        address = rng.randrange(0x1F00)
        add(3 if index % 7 == 0 else 1, address, data=rng.getrandbits(16))
        add(0, address, rng.randrange(1, 5))
    for count in (0, 5, 6, 7):
        add(0, 1, count)
    return cases


def write_vectors(directory):
    programs = (system_vectors(), gpu_vectors())
    for name, cases in zip(('system', 'gpu'), programs):
        (directory / f'{name}.txt').write_text(
            ''.join(' '.join(f'{int(field):x}' for field in case) + '\n' for case in cases))
    return tuple(len(cases) for cases in programs)
