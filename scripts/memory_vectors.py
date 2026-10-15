"""Deterministic PSX memory bridge vectors with a byte-addressed software oracle."""
import random


def initial_byte(address):
    return (address * 17 + (address >> 8) * 23 + 0x5A) & 0xFF


def vectors(seed=0x50535802):
    rng = random.Random(seed)
    memory = {}

    def transaction(address, count, write=False, data=0, mask=0xFFFF):
        error = count < 1 or count > 8 or address + count > (1 << 25)
        result = 0
        if not error:
            for byte in range(count * 2):
                physical = address * 2 + byte
                if write and mask & (1 << byte):
                    memory[physical] = (data >> (byte * 8)) & 0xFF
                if not write:
                    result |= memory.get(physical, initial_byte(physical)) << (byte * 8)
        return address, count, int(write), data, mask, int(error), result

    # Every length/alignment, each byte lane, zero masks and boundary spans.
    for base in (0, 0x100000, 0x800000):
        for count in range(1, 9):
            for offset in (0, 1):
                address = base + count * 32 + offset
                yield transaction(address, count)
                for byte in range(count * 2):
                    yield transaction(address, count, True, rng.getrandbits(128), 1 << byte)
                    yield transaction(address, count)
                yield transaction(address, count, True, rng.getrandbits(128), 0)
                yield transaction(address, count)
        for boundary in (1023, 2047, 3071, 4095, 5119, 6143, 7167):
            address = base + boundary
            yield transaction(address, 8)
            yield transaction(address, 8, True, rng.getrandbits(128))
            yield transaction(address, 8)
    for _ in range(1000):
        address = rng.choice((0, 0x100000, 0x800000)) + rng.randrange(8000)
        count = rng.randrange(1, 9)
        yield transaction(address, count, True, rng.getrandbits(128), rng.getrandbits(16))
        yield transaction(address, count)
    for address, count in ((0, 0), (0, 9), (0, 15), (0x1FFFFFE, 3),
                           (0x1FFFFFF, 2), (0x1FFFFFF, 1), (0x1FFFFF8, 8)):
        yield transaction(address, count)
        yield transaction(address, count, True, rng.getrandbits(128))
        yield transaction(address, count)


def write_vectors(path):
    cases = list(vectors())
    with path.open('w') as output:
        for case in cases:
            output.write(' '.join(f'{field:x}' for field in case) + '\n')
    return len(cases)
