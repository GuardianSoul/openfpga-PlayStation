#!/usr/bin/env python3
"""cd-rewrite-061: prove from cd_xa.vhd that XaRingBufferL/R are never read and written on the same clock, so
moving them to MLAB (no old-data read-during-write) cannot change a value read.

Rule checked: every assignment to XaRingBuffer?(...) and every read of it lies inside a `when` branch of the
single `case (ResampleState) is`, and no branch both writes and reads the ring. Also: the read is registered
(assigned with <= inside the clocked process), so the read latency is unchanged.

usage: cd_rewrite_061_xaring_check.py cd_xa.vhd [--mutants]"""
import re, sys


def branches(text):
    i = text.index('case (ResampleState) is')
    depth, out, cur, name = 0, [], [], None
    for line in text[i:].split('\n')[1:]:
        s = line.strip().lower()
        if re.match(r'^case\b', s): depth += 1
        if s.startswith('end case'):
            if depth == 0: break
            depth -= 1
        m = re.match(r'^when\s+(\w+)\s*=>', s)
        if depth == 0 and m:
            if name: out.append((name, cur))
            name, cur = m.group(1), []
            continue
        cur.append(line)
    if name: out.append((name, cur))
    return out


def check(text):
    code = re.sub(r'--[^\n]*', '', text)
    uses = len(re.findall(r'XaRingBuffer[LR]\s*\(', code))
    seen, writes, reads = 0, set(), set()
    for name, lines in branches(code):
        body = '\n'.join(lines)
        w = len(re.findall(r'XaRingBuffer[LR]\s*\([^;]*\)\s*<=', body))
        r = len(re.findall(r'<=\s*XaRingBuffer[LR]\s*\(', body))
        seen += w + r
        if w: writes.add(name)
        if r: reads.add(name)
        if w and r:
            raise AssertionError(f'{name}: ring read and written in one state')
    if seen != uses:
        raise AssertionError(f'{uses - seen} ring access(es) outside the ResampleState case')
    if not writes or not reads:
        raise AssertionError('no ring writes or reads found')
    return sorted(writes), sorted(reads)


if __name__ == '__main__':
    t = open(sys.argv[1]).read()
    print('ok: writes in', *check(t))
    if '--mutants' in sys.argv:
        muts = {
            'read in WRITERING': t.replace("ResampleState                 <= RESAMPLE_IDLE;\n",
                                           "ResampleState                 <= RESAMPLE_IDLE;\n                  ringOut <= XaRingBufferL(to_integer(XaRing_readpointer));\n", 1),
            'write outside the case': t.replace('case (ResampleState) is',
                                                'XaRingBufferL(0) <= (others => \'0\');\n            case (ResampleState) is', 1),
        }
        for k, m in muts.items():
            assert m != t, k
            try:
                check(m); print('MUTANT NOT CAUGHT:', k)
            except AssertionError as e:
                print('mutant caught:', k, '-', e)
