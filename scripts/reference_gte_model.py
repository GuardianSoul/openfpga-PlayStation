"""Independent PSX GTE model written from the psx-spx GTE description.

Register file is the 64 GTE registers (0-31 data, 32-63 control) as the CPU
sees them through MFC2/CFC2 and MTC2/CTC2. execute(command) runs one GTE
command. Arithmetic: MAC1-3 are checked for 44-bit overflow after every
addition step and wrap to 44 bits; MAC0 is checked for 32-bit overflow;
IR, colour, SXY, SZ/OTZ and IR0 saturate with their FLAG bits; the
perspective divide uses the hardware's UNR reciprocal.
"""


def s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def s32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def sext(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


UNR = [max(0, (0x40000 // (i + 0x100) + 1) // 2 - 0x101) for i in range(0x101)]


def clz16(v):
    v &= 0xFFFF
    n = 0
    while n < 16 and not (v & 0x8000):
        v <<= 1; n += 1
    return n


def leading_count(v):
    v &= 0xFFFFFFFF
    if v & 0x80000000:
        v = ~v & 0xFFFFFFFF
    n = 0
    while n < 32 and not (v & 0x80000000):
        v <<= 1; n += 1
    return n


class GTE:
    def __init__(self):
        self.r = [0] * 64  # raw stored values (32-bit)
        self.flag = 0

    # ---- CPU register interface ----
    def write(self, index, value):
        value &= 0xFFFFFFFF
        if index == 15:  # SXYP pushes
            self.r[12], self.r[13], self.r[14] = self.r[13], self.r[14], value
        elif index == 28:  # IRGB
            self.r[9] = (value & 0x1F) << 7
            self.r[10] = ((value >> 5) & 0x1F) << 7
            self.r[11] = ((value >> 10) & 0x1F) << 7
        elif index in (29, 31):  # ORGB, LZCR read-only
            pass
        elif index == 30:
            self.r[30] = value
            self.r[31] = leading_count(value)
        elif index == 63:
            self.flag = value & 0x7FFFF000
        elif index in (1, 3, 5, 7, 8, 9, 10, 11, 16, 17, 18, 19):
            self.r[index] = value & 0xFFFF
        elif index in (36, 44, 52, 58, 59, 61, 62):
            self.r[index] = value & 0xFFFF
        else:
            self.r[index] = value

    def read(self, index):
        r = self.r
        if index in (1, 3, 5, 8, 9, 10, 11, 36, 44, 52, 58, 59, 61, 62):
            return s16(r[index]) & 0xFFFFFFFF
        if index in (7, 16, 17, 18, 19):
            return r[index] & 0xFFFF
        if index == 15:
            return r[14]
        if index in (28, 29):
            def c(v): return max(0, min(0x1F, s16(v) >> 7))
            return c(r[9]) | (c(r[10]) << 5) | (c(r[11]) << 10)
        if index == 63:
            f = self.flag & 0x7FFFF000
            return f | (0x80000000 if f & 0x7F87E000 else 0)
        return r[index]

    # ---- helpers ----
    def ir(self, i): return s16(self.r[8 + i])
    def set_ir(self, i, v): self.r[8 + i] = v & 0xFFFF
    def mac(self, i): return s32(self.r[24 + i])
    def set_mac(self, i, v): self.r[24 + i] = v & 0xFFFFFFFF

    def vec(self, n):
        return (s16(self.r[2 * n]), s16(self.r[2 * n] >> 16), s16(self.r[2 * n + 1]))

    def matrix(self, base):
        c = self.r
        vals = [s16(c[base]), s16(c[base] >> 16), s16(c[base + 1]), s16(c[base + 1] >> 16), s16(c[base + 2]),
                s16(c[base + 2] >> 16), s16(c[base + 3]), s16(c[base + 3] >> 16), s16(c[base + 4])]
        return [vals[0:3], vals[3:6], vals[6:9]]

    def rgbc(self):
        v = self.r[6]
        return v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, v >> 24

    MAC_FLAGS = {1: (30, 27), 2: (29, 26), 3: (28, 25)}
    IR_FLAGS = {1: 24, 2: 23, 3: 22}

    def check44(self, i, v):
        pos, neg = self.MAC_FLAGS[i]
        if v > (1 << 43) - 1: self.flag |= 1 << pos
        elif v < -(1 << 43): self.flag |= 1 << neg
        return sext(v, 44)

    def acc(self, i, terms):
        """Sum terms with a 44-bit overflow check and wrap after every addition."""
        total = self.check44(i, terms[0]) if len(terms) == 1 else terms[0]
        for t in terms[1:]:
            total = self.check44(i, total + t)
        return total

    def set_mac_ir(self, i, value, shift, lm):
        value = self.check44(i, value)
        m = value >> shift
        self.set_mac(i, m)
        self.set_ir(i, self.sat_ir(i, s32(m), lm))

    def sat_ir(self, i, v, lm):
        lo = 0 if lm else -0x8000
        if v < lo: self.flag |= 1 << self.IR_FLAGS[i]; return lo
        if v > 0x7FFF: self.flag |= 1 << self.IR_FLAGS[i]; return 0x7FFF
        return v

    def set_mac0(self, v):
        if v > 0x7FFFFFFF: self.flag |= 1 << 16
        elif v < -0x80000000: self.flag |= 1 << 15
        self.r[24] = v & 0xFFFFFFFF

    def set_ir0(self, v):
        if v < 0: self.flag |= 1 << 12; v = 0
        elif v > 0x1000: self.flag |= 1 << 12; v = 0x1000
        self.r[8] = v & 0xFFFF

    def push_sz(self, v):
        if v < 0: self.flag |= 1 << 18; v = 0
        elif v > 0xFFFF: self.flag |= 1 << 18; v = 0xFFFF
        self.r[16], self.r[17], self.r[18], self.r[19] = self.r[17], self.r[18], self.r[19], v

    def push_sxy(self, x, y):
        if x < -0x400: self.flag |= 1 << 14; x = -0x400
        elif x > 0x3FF: self.flag |= 1 << 14; x = 0x3FF
        if y < -0x400: self.flag |= 1 << 13; y = -0x400
        elif y > 0x3FF: self.flag |= 1 << 13; y = 0x3FF
        self.r[12], self.r[13] = self.r[13], self.r[14]
        self.r[14] = (x & 0xFFFF) | ((y & 0xFFFF) << 16)

    def push_rgb(self):
        out = []
        for i, bit in ((1, 21), (2, 20), (3, 19)):
            c = self.mac(i) >> 4
            if c < 0: self.flag |= 1 << bit; c = 0
            elif c > 0xFF: self.flag |= 1 << bit; c = 0xFF
            out.append(c)
        code = self.r[6] >> 24
        self.r[20], self.r[21] = self.r[21], self.r[22]
        self.r[22] = out[0] | (out[1] << 8) | (out[2] << 16) | (code << 24)

    def mul_mat_vec(self, m, t, v, shift, lm):
        for i in range(3):
            terms = [t[i] << 12] if t is not None else [0]
            terms += [m[i][0] * v[0], m[i][1] * v[1], m[i][2] * v[2]]
            self.set_mac_ir(i + 1, self.acc(i + 1, terms), shift, lm)

    def mul_mat_vec_buggy(self, m, t, v, shift, lm):
        """MVMVA with the far colour vector: the first product is only used for flags."""
        for i in range(3):
            first = self.acc(i + 1, [t[i] << 12, m[i][0] * v[0]])
            self.sat_ir(i + 1, s32(first >> shift), False)
            self.set_mac_ir(i + 1, self.acc(i + 1, [m[i][1] * v[1], m[i][2] * v[2]]), shift, lm)

    def control_vec(self, base):
        return [s32(self.r[base]), s32(self.r[base + 1]), s32(self.r[base + 2])]

    def interpolate(self, in_mac, shift, lm):
        fc = self.control_vec(53)
        for i in range(3):
            self.set_mac_ir(i + 1, (fc[i] << 12) - in_mac[i], shift, False)
        ir0 = s16(self.r[8])
        for i in range(3):
            self.set_mac_ir(i + 1, self.ir(i + 1) * ir0 + in_mac[i], shift, lm)

    def divide(self):
        h = self.r[58] & 0xFFFF
        sz3 = self.r[19] & 0xFFFF
        if h < sz3 * 2:
            z = clz16(sz3)
            n = h << z
            d = sz3 << z
            u = UNR[(d - 0x7FC0) >> 7] + 0x101
            d = (0x2000080 - d * u) >> 8
            d = (0x0000080 + d * u) >> 8
            return min(0x1FFFF, (n * d + 0x8000) >> 16)
        self.flag |= 1 << 17
        return 0x1FFFF

    def rtp(self, v, shift, lm, last):
        rt = self.matrix(32)
        tr = self.control_vec(37)
        z = 0
        for i in range(3):
            total = self.acc(i + 1, [tr[i] << 12, rt[i][0] * v[0], rt[i][1] * v[1], rt[i][2] * v[2]])
            self.set_mac(i + 1, total >> shift)
            if i < 2:
                self.set_ir(i + 1, self.sat_ir(i + 1, s32(total >> shift), lm))
            else:
                z = total
                # IR3 saturates from MAC3, but its flag follows MAC3 >> 12 (psx-spx).
                m3 = s32(total >> shift)
                lo = 0 if lm else -0x8000
                self.set_ir(3, max(lo, min(0x7FFF, m3)))
                if not -0x8000 <= (total >> 12) <= 0x7FFF:
                    self.flag |= 1 << 22
        self.push_sz(z >> 12)
        n = self.divide()
        ofx, ofy = s32(self.r[56]), s32(self.r[57])
        sx = n * self.ir(1) + ofx
        self.set_mac0(sx)
        sy = n * self.ir(2) + ofy
        self.set_mac0(sy)
        self.push_sxy(sx >> 16, sy >> 16)
        if last:
            dq = n * s16(self.r[59]) + s32(self.r[60])
            self.set_mac0(dq)
            self.set_ir0(dq >> 12)

    def nc(self, v, shift, lm, mode):
        self.mul_mat_vec(self.matrix(40), None, v, shift, lm)
        irv = [self.ir(1), self.ir(2), self.ir(3)]
        self.mul_mat_vec(self.matrix(48), self.control_vec(45), irv, shift, lm)
        if mode == 'nc':
            pass
        else:
            r, g, b, _ = self.rgbc()
            in_mac = [(r * self.ir(1)) << 4, (g * self.ir(2)) << 4, (b * self.ir(3)) << 4]
            if mode == 'ncc':
                for i in range(3):
                    self.set_mac_ir(i + 1, in_mac[i], shift, lm)
            else:  # ncd
                self.interpolate(in_mac, shift, lm)
        self.push_rgb()

    def execute(self, command):
        self.flag = 0
        op = command & 0x3F
        shift = 12 if command & (1 << 19) else 0
        lm = bool(command & (1 << 10))
        if op == 0x01:  # RTPS
            self.rtp(self.vec(0), shift, lm, True)
        elif op == 0x30:  # RTPT
            for n in range(3):
                self.rtp(self.vec(n), shift, lm, n == 2)
        elif op == 0x06:  # NCLIP
            x0, y0 = s16(self.r[12]), s16(self.r[12] >> 16)
            x1, y1 = s16(self.r[13]), s16(self.r[13] >> 16)
            x2, y2 = s16(self.r[14]), s16(self.r[14] >> 16)
            self.set_mac0(x0 * y1 + x1 * y2 + x2 * y0 - x0 * y2 - x1 * y0 - x2 * y1)
        elif op == 0x0C:  # OP
            d1, d2, d3 = s16(self.r[32]), s16(self.r[34]), s16(self.r[36])
            i1, i2, i3 = self.ir(1), self.ir(2), self.ir(3)
            self.set_mac_ir(1, self.acc(1, [i3 * d2, -(i2 * d3)]), shift, lm)
            self.set_mac_ir(2, self.acc(2, [i1 * d3, -(i3 * d1)]), shift, lm)
            self.set_mac_ir(3, self.acc(3, [i2 * d1, -(i1 * d2)]), shift, lm)
        elif op in (0x10, 0x2A):  # DPCS, DPCT
            for n in range(3 if op == 0x2A else 1):
                if op == 0x2A:
                    c = self.r[20]
                    r, g, b = c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF
                else:
                    r, g, b, _ = self.rgbc()
                self.interpolate([r << 16, g << 16, b << 16], shift, lm)
                self.push_rgb()
        elif op == 0x11:  # INTPL
            self.interpolate([self.ir(1) << 12, self.ir(2) << 12, self.ir(3) << 12], shift, lm)
            self.push_rgb()
        elif op == 0x12:  # MVMVA
            mx, vx, tx = (command >> 17) & 3, (command >> 15) & 3, (command >> 13) & 3
            if mx == 0: m = self.matrix(32)
            elif mx == 1: m = self.matrix(40)
            elif mx == 2: m = self.matrix(48)
            else:
                r = self.rgbc()[0]
                rt13, rt22 = s16(self.r[33]), s16(self.r[34])
                m = [[-(r << 4), r << 4, self.ir(0)], [rt13] * 3, [rt22] * 3]
            v = self.vec(vx) if vx < 3 else (self.ir(1), self.ir(2), self.ir(3))
            if tx == 2:
                self.mul_mat_vec_buggy(m, self.control_vec(53), v, shift, lm)
            else:
                t = self.control_vec(37) if tx == 0 else self.control_vec(45) if tx == 1 else None
                self.mul_mat_vec(m, t, v, shift, lm)
        elif op == 0x13:  # NCDS
            self.nc(self.vec(0), shift, lm, 'ncd')
        elif op == 0x14:  # CDP
            irv = [self.ir(1), self.ir(2), self.ir(3)]
            self.mul_mat_vec(self.matrix(48), self.control_vec(45), irv, shift, lm)
            r, g, b, _ = self.rgbc()
            self.interpolate([(r * self.ir(1)) << 4, (g * self.ir(2)) << 4, (b * self.ir(3)) << 4], shift, lm)
            self.push_rgb()
        elif op == 0x16:  # NCDT
            for n in range(3): self.nc(self.vec(n), shift, lm, 'ncd')
        elif op == 0x1B:  # NCCS
            self.nc(self.vec(0), shift, lm, 'ncc')
        elif op == 0x1C:  # CC
            irv = [self.ir(1), self.ir(2), self.ir(3)]
            self.mul_mat_vec(self.matrix(48), self.control_vec(45), irv, shift, lm)
            r, g, b, _ = self.rgbc()
            for i, c in enumerate((r, g, b)):
                self.set_mac_ir(i + 1, (c * self.ir(i + 1)) << 4, shift, lm)
            self.push_rgb()
        elif op == 0x1E:  # NCS
            self.nc(self.vec(0), shift, lm, 'nc')
        elif op == 0x20:  # NCT
            for n in range(3): self.nc(self.vec(n), shift, lm, 'nc')
        elif op == 0x28:  # SQR
            for i in range(3):
                self.set_mac_ir(i + 1, self.ir(i + 1) * self.ir(i + 1), shift, lm)
        elif op == 0x29:  # DCPL
            r, g, b, _ = self.rgbc()
            self.interpolate([(r * self.ir(1)) << 4, (g * self.ir(2)) << 4, (b * self.ir(3)) << 4], shift, lm)
            self.push_rgb()
        elif op in (0x2D, 0x2E):  # AVSZ3, AVSZ4
            sz = [self.r[16 + i] & 0xFFFF for i in range(4)]
            if op == 0x2D:
                v = s16(self.r[61]) * (sz[1] + sz[2] + sz[3])
            else:
                v = s16(self.r[62]) * (sz[0] + sz[1] + sz[2] + sz[3])
            self.set_mac0(v)
            o = v >> 12
            if o < 0: self.flag |= 1 << 18; o = 0
            elif o > 0xFFFF: self.flag |= 1 << 18; o = 0xFFFF
            self.r[7] = o
        elif op == 0x3D:  # GPF
            ir0 = self.ir(0)
            for i in range(3):
                self.set_mac_ir(i + 1, ir0 * self.ir(i + 1), shift, lm)
            self.push_rgb()
        elif op == 0x3E:  # GPL
            ir0 = self.ir(0)
            for i in range(3):
                self.set_mac_ir(i + 1, self.acc(i + 1, [self.mac(i + 1) << shift, ir0 * self.ir(i + 1)]), shift, lm)
            self.push_rgb()
        elif op == 0x3F:  # NCCT
            for n in range(3): self.nc(self.vec(n), shift, lm, 'ncc')
        # Other opcodes: no operation (FLAG cleared).
