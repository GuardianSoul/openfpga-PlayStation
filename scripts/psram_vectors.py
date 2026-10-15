"""Deterministic PSRAM transaction vectors with a byte-addressed software oracle.

Same row format as memory_vectors.py, limited to the PSRAM's 16 M halfwords
(32 MiB, two dies). Requests that end past it must complete with an error.
"""
import random

from memory_vectors import initial_byte

HALFWORDS = 1 << 24


def vectors(seed=0x50535250, random_cases=600):
    rng = random.Random(seed)
    memory = {}

    def transaction(address, count, write=False, data=0, mask=0xFFFF):
        error = count < 1 or count > 8 or address + count > HALFWORDS
        result = 0
        if not error:
            for byte in range(count * 2):
                physical = address * 2 + byte
                if write and mask & (1 << byte):
                    memory[physical] = (data >> (byte * 8)) & 0xFF
                if not write:
                    result |= memory.get(physical, initial_byte(physical)) << (byte * 8)
        return address, count, int(write), data, mask, int(error), result

    # Both dies, every length/alignment and byte lane at the start of each.
    for base in (0, 0x800000):
        for count in range(1, 9):
            for offset in (0, 1):
                address = base + count * 32 + offset
                yield transaction(address, count)
                for byte in range(count * 2):
                    yield transaction(address, count, True, rng.getrandbits(128), 1 << byte)
                    yield transaction(address, count)
                yield transaction(address, count, True, rng.getrandbits(128), 0)
                yield transaction(address, count)
    # Spans across the die boundary and up to the end of the space.
    for address in (0x7FFFF9, 0x7FFFFC, 0x7FFFFF, 0xFFFFF8, 0xFFFFFE, 0xFFFFFF):
        count = min(8, HALFWORDS - address)
        yield transaction(address, count)
        yield transaction(address, count, True, rng.getrandbits(128))
        yield transaction(address, count)
    for _ in range(random_cases):
        address = rng.choice((0, 0x7FF000, 0xFFE000)) + rng.randrange(0x1000)
        count = rng.randrange(1, 9)
        yield transaction(address, count, True, rng.getrandbits(128), rng.getrandbits(16))
        yield transaction(address, count)
    # Malformed and out-of-range requests never touch memory.
    for address, count in ((0, 0), (0, 9), (0xFFFFFC, 8), (0xFFFFFF, 2),
                           (0x1000000, 1), (0x1FFFFF8, 8)):
        yield transaction(address, count, True, rng.getrandbits(128))
        yield transaction(address, count)
    yield transaction(0xFFFFF8, 8)


def write_vectors(path):
    cases = list(vectors())
    with path.open('w') as output:
        for case in cases:
            output.write(' '.join(f'{field:x}' for field in case) + '\n')
    return len(cases)
