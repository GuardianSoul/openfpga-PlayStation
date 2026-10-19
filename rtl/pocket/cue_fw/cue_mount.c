// SPDX-License-Identifier: MIT
// Firmware for psx_cue_mount (SERV, RV32I): play a .cue or .bin picked from
// the launch list or with "Load disc" (docs/pocket-cue-loading.md).
//
// The hardware (rtl/pocket/psx_cue_mount.sv) gives this program:
//   - APF target commands (read, getfile, openfile) with wired fields,
//   - the path struct (0x2000) and a 512-byte read window (0x2200) in RAM,
//   - the stream's held target request (id, offset, length) and its done,
//   - slot updates for the stream, its allcomplete, and the size it sees for
//     the disc and table slots,
//   - bridge writes into the stream's window (the generated table file).
// Built by scripts/build_cue_fw.py into rtl/pocket/psx_cue_fw.hex.
//
// A pick is sniffed with a 4-byte read: from the launch list '{' or nothing
// readable is a game JSON, left to APF. A raw sector starts with 0x00: a
// plain image, passed on unchanged from Load disc, or from the launch list
// measured and read through LAUNCH_SLOT. Anything else is a cue: getfile
// gives its folder; FILE / TRACK / INDEX / PREGAP are parsed (non-raw track
// types, more than 99 files or tracks fail); every FILE is opened into
// FILE_SLOT and measured with 4-byte reads (APF reports no size for a
// core-opened file and fails a read past the end); the layout is checked as
// make_pocket_instances.py one_file_plan() does. The disc is then the files
// back to back, with the track table of track_table().
//
// Multi-disc games: a cue whose path names "(Disc d)" (redump) has siblings
// with the other numbers. With the core menu's Disc list (prepare
// --disc-menu), every disc slot shows this program's disc; when the menu
// asks for disc k, the stream's table lookups wait (menu_hold) while the
// sibling cue is opened into CUE2_SLOT and mounted in place of the current
// one. A disc the game doesn't have keeps the current one. The disc picked
// at launch wins over the Disc remembered from an earlier game.
//
// A .m3u playlist (as DuckStation reads them: one disc per line, relative
// to the playlist, # comments) is a multi-disc game with one save: its
// first entry is mounted, and the menu's Disc k is entry k. Entries are
// cues or plain images, opened into CUE2_SLOT.
//
// Cheats (prepare --cheats): a game JSON's slot 98 holds a GameShark code
// list as text (the plugin and make_pocket_instances.py put it there). Its
// codes are read at launch and, while the Cheats setting is on, applied to
// main RAM once per frame through psx_cue_mount's main RAM port. Builds
// without the port have no slot 98, so the list stays empty.

typedef unsigned int u32;
typedef unsigned char u8;

#define IO      ((volatile u32 *)0x80000000)
#define INJ     ((volatile u32 *)0xC0000000)   // word w of the stream's window
#define PATH    ((volatile u8 *)0x2000)
#define PATHW   ((volatile u32 *)0x2000)
#define CHUNK   ((volatile u8 *)0x2200)

// IO writes
enum { W_CMD, W_IDLEN, W_OFF, W_DONE, W_UPD, W_FLAGS, W_DISC, W_CLR };
// IO reads
// R_STATUS: status in bits 7:0, the menu's Disc in 9:8, the stream's slot
// in 18:10 and length in 30:19, bit 31 set when LibCrypt is on (ST_LC)
enum { R_STATUS, R_UOFF, R_CHEAT };
// R_CHEAT (prepare --cheats): the last main RAM read in bits 15:0, the
// Cheats setting in bit 29, a toggle per frame in bit 30, an access running
// in bit 31
#define CH_ON   (1u << 29)
#define CH_BUSY 0x80000000u
#define CLR_CHEAT 0x80000000u   // W_CLR: start the main RAM access W_IDLEN and W_OFF describe
#define ST_ERR   7u
#define ST_CMD   8u      // a command is running
#define ST_U     16u     // the stream's read is waiting
#define ST_UPD   32u     // a Load disc pick is waiting
#define ST_BOOT  64u     // allcomplete rose: the launch list's file
#define ST_MENU  128u    // the menu's Disc is not the one loaded (stream lookups wait)
#define ST_LC    0x80000000u   // read the cue's .sbi (prepare --cue-libcrypt)
#define CLR_MENU 128u    // W_CLR: forget that the menu was open (a later DISC_SLOT update is no pick)
enum { K_READ = 0, K_GETFILE = 1, K_OPEN = 2, K_USEU = 4 };   // K_USEU: into the stream's window
#define F_ALLC    1u     // the stream's allcomplete
#define F_MOUNTED 2u     // a cue is the disc
#define F_FAILED  4u     // the last cue did not mount
#define F_VDISC   8u     // the stream sees DISC_SLOT at W_DISC bytes
#define F_VTABLE  16u    // the stream sees TABLE_SLOT at 1,600 bytes
#define F_IDLE    32u    // the stream may start a read
#define F_ACCEPT  64u    // any DISC_SLOT update is a pick (not only after the menu)
#define F_DISC    0x300u // the menu Disc the disc was loaded for (bits 9:8)

#define DISC_SLOT   101u
#define LAUNCH_SLOT 0u
#define TABLE_SLOT  102u
#define FILE_SLOT   103u
#define CUE_SLOT    100u    // a game JSON's cue (Pocket Sync, pupdate, make_pocket_instances.py)
#define CUE2_SLOT   104u    // another disc's cue, opened by this program
#define MAXF 99u
#define MAXT 99u
#define NONE 0xffu

#define BIG __attribute__((section(".big")))
#define NOINLINE __attribute__((noinline))   // code space: 8 KiB
static u32 fname[MAXF] BIG;        // name offset in the cue | length << 16 | PREGAP sectors before it << 24
static u32 fsec[MAXF + 1] BIG;     // first sector; fsec[nfiles] = total
// Segments: a PREGAP in the middle of a file (ImgBurn-style merged cues:
// TRACK 02 AUDIO / PREGAP 00:02:00, the gap not in the .bin) splits it, so
// "files" are segments of the cue's FILEs: physical file << 24 | the
// segment's first sector in it.
static u32 fseg[MAXF];              // in .bss (HIGH): BIG is full
static u32 tstart[MAXT] BIG;       // parse: sectors in the file | file << 24; then absolute
static u32 ti1[MAXT] BIG;          // INDEX 01, as tstart (| audio << 31 while parsing)
static u8 base[256] BIG;           // the picked cue's path, for its sibling discs
static u32 marks[4], nmarks;       // where base names its disc number
static u32 fb;                     // '0' + the disc of the cue base names; 0: a playlist entry
static u32 cur;                    // its disc number d from "(Disc d)", 0 for none
static u32 lcmask;                 // the disc's LibCrypt mask (table word 3)
static u32 lead;                   // sectors before track 1's INDEX 01 in the first file, not on the disc

static u32 flags, nfiles, ntracks, total, open_file, dirlen, pick, cue_size, bin, nphys;
static u32 rd = FILE_SLOT;         // where file k is read: FILE_SLOT, or (DISC_SLOT) a game JSON's slot 101 + k
static u32 json;                   // the cue came from a game JSON (its .bin is in DISC_SLOT)
static u32 cbase, clen, cpos;      // read window: cue bytes [cbase, cbase+clen); next byte
static int c;                      // the byte being parsed, -1 at the end

// RV32I has no multiply; the compiler calls this.
u32 __mulsi3(u32 a, u32 b) { u32 r = 0; while (b) { if (b & 1) r += a; a <<= 1; b >>= 1; } return r; }
// The compiler turns the counting loops below into divisions.
u32 __udivsi3(u32 n, u32 d) {
    u32 q = 0, r = 0;
    for (int i = 31; i >= 0; i--) { r = r << 1 | (n >> i & 1); if (r >= d) { r -= d; q |= 1u << i; } }
    return q;
}
u32 __umodsi3(u32 n, u32 d) { return n - __udivsi3(n, d) * d; }
static u32 sectors_to_bytes(u32 n) { return (n << 11) + (n << 8) + (n << 5) + (n << 4); }   // n * 2352

static void setf(u32 f) { flags = f; IO[W_FLAGS] = f; }

// Probe words (psx_cue_mount DEBUG=1 shows them; release ignores the
// stores): W_DBG+0 the line of the last fail() and the last open's error (opn), +1 the
// last command and +2 the last getfile or open that failed (kind | slot << 4
// | error << 16 | command count << 20), +3 and +4 the first 8 path bytes
// after the first getfile, +5 and +6 the last 8 path bytes of that failed
// getfile or open (first byte in bits 31:24), +7 the LibCrypt mask.
#define W_DBG 8
static u32 ncmd, dbg_got;
NOINLINE static u32 pack4(u32 i) { return (u32)PATH[i] << 24 | (u32)PATH[i + 1] << 16 | (u32)PATH[i + 2] << 8 | PATH[i + 3]; }

// One APF target command; its error (0 = done).
NOINLINE static u32 cmd(u32 kind, u32 id, u32 off, u32 len) {
    IO[W_IDLEN] = id | len << 16;
    IO[W_OFF] = off;
    IO[W_CMD] = kind;
    u32 s;
    while ((s = IO[R_STATUS]) & ST_CMD) ;
    s &= ST_ERR;
    u32 d = kind | id << 4 | s << 16 | ++ncmd << 20;
    IO[W_DBG + 1] = d;
    if (kind == K_GETFILE && !dbg_got) { dbg_got = 1; IO[W_DBG + 3] = pack4(0); IO[W_DBG + 4] = pack4(4); }
    if (s && (kind == K_GETFILE || kind == K_OPEN)) {
        u32 i = 0;
        while (i < 255 && PATH[i]) i++;
        IO[W_DBG + 2] = d;
        if (i >= 8) { IO[W_DBG + 5] = pack4(i - 8); IO[W_DBG + 6] = pack4(i - 4); }
    }
    return s;
}

// Open the path struct's file into slot (its error; W_DBG+0 bits 14:12).
static u32 oerr;
NOINLINE static u32 opn(u32 slot) {
    oerr = cmd(K_OPEN, slot, 0, 0);
    IO[W_DBG] = oerr << 12;
    return oerr;
}

// The largest n < 2^(k+1) whose word ending at n*unit reads (n >= 1, or 0).
static u32 probe(u32 slot, u32 unit, int k) {
    u32 n = 0;
    for (; k >= 0; k--) {
        u32 t = n | 1u << k, end = t * unit;
        if (end >= 4 && !cmd(K_READ, slot, end - 4, 4)) n = t;
    }
    return n;
}

// ---------------- cue text ----------------
NOINLINE static int getb(void) {
    if (cpos >= cue_size) return -1;
    if (cpos < cbase || cpos >= cbase + clen) {
        u32 base = cpos & ~3u, left = cue_size - base;
        u32 len = left >= 256 ? 256 : left & ~3u;
        if (!len) { base = cue_size - 4; len = 4; }   // under a word left: the last word
        clen = 0;
        if (cmd(K_READ, pick, base, len)) return -2;
        cbase = base; clen = len;
    }
    return CHUNK[cpos++ - cbase];
}
NOINLINE static void adv(void) { if (c >= 0) c = getb(); }
static int space(int x) { return x == ' ' || x == '\t' || x >= 0x80; }   // high bytes: a UTF-8 BOM
static int eol(int x) { return x == '\n' || x == '\r' || x < 0; }
static int upper(int x) { return x >= 'a' && x <= 'z' ? x - 32 : x; }
static int digit(int x) { return x >= '0' && x <= '9'; }
static void skipsp(void) { while (c >= 0 && space(c)) adv(); }
static u32 number(int *ok) {
    u32 v = 0; *ok = digit(c);
    while (digit(c)) { v = v * 10 + (u32)(c - '0'); adv(); }
    return v;
}

// The current track's INDEX 00/01 and PREGAP into tstart and ti1; 0 if
// it can't mount.
static int finish(int has0, int has1, u32 i0, u32 i1, int audio, u32 pg) {
    u32 r = ntracks - 1, f = nfiles - 1;
    if (!has1) return 0;
    if (pg) {
        if (has0) return 0;
        if (r && tstart[r - 1] >> 24 == f) {
            // Inside a file that already has a track: a new segment of the
            // same file from this track's INDEX 01, the gap before it.
            if (nfiles == MAXF || i1 <= (fseg[f] & 0xffffff)) return 0;
            fname[nfiles] = (fname[f] & 0xffffff) | pg << 24;
            fseg[nfiles] = (fseg[f] & 0xff000000) | i1;
            f = nfiles++;
        } else if (i1) return 0;   // before a file's first track, at its first sector
        else fname[f] |= pg << 24;
    }
    // Indexes count from the segment's start.
    u32 b = fseg[f] & 0xffffff;
    if ((has0 ? i0 : i1) < b) return 0;
    tstart[r] = ((has0 ? i0 : i1) - b) | f << 24;
    ti1[r] = (i1 - b) | (u32)audio << 31;
    return 1;
}

// MM:SS:FF in frames; 0 with *ok clear on bad syntax.
static u32 frames(int *ok) {
    u32 mm, ss, ff;
    mm = number(ok); if (!*ok || c != ':') return *ok = 0; adv();
    ss = number(ok); if (!*ok || c != ':') return *ok = 0; adv();
    ff = number(ok);
    return (mm * 60 + ss) * 75 + ff;
}

// FILE / TRACK / INDEX / PREGAP into fname, tstart and ti1; 0 on a cue that
// can't mount. A PREGAP (silence not in the files) is taken for the first
// track of a file starting at its first sector: those sectors read as zeros
// before the file.
NOINLINE static int parse(void) {
    int ht = 0, has0 = 0, has1 = 0, audio = 0, ok;
    u32 i0 = 0, i1 = 0, pg = 0;
    nfiles = ntracks = nphys = 0;
    cbase = clen = cpos = 0;
    c = getb();
    for (;;) {
        while (c >= 0 && (eol(c) || space(c))) adv();
        if (c == -2) return 0;
        if (c < 0) break;
        int k0 = upper(c), k1 = 0;
        adv();
        if (!eol(c) && !space(c)) k1 = upper(c);
        while (!eol(c) && !space(c)) adv();
        skipsp();
        // Keywords by their first two letters (FIle, TRack, INdex, PRegap;
        // the others differ).
        if ((k0 == 'F' && k1 == 'I') || (k0 == 'T' && k1 == 'R')) {
            if (ht) {
                if (!finish(has0, has1, i0, i1, audio, pg)) return 0;
            }
            ht = 0;
            if (eol(c)) return 0;
            if (k0 == 'F') {
                if (nfiles == MAXF) return 0;
                u32 len = 0, off;
                if (c == '"') { adv(); off = cpos - 1; while (!eol(c) && c != '"') { len++; adv(); } }
                else { off = cpos - 1; while (!eol(c) && c != ' ' && c != '\t') { len++; adv(); } }
                if (!len || len > 255) return 0;
                fseg[nfiles] = nphys++ << 24;
                fname[nfiles++] = off | len << 16;
            } else {
                if (!nfiles || ntracks == MAXT) return 0;
                number(&ok);
                if (!ok || !space(c)) return 0;
                skipsp();
                // AUDIO, or a raw data type (MODE1/2352, MODE2/2352)
                int first = upper(c), t0 = 0, t1 = 0;
                while (!eol(c) && !space(c)) { t0 = t1; t1 = c; adv(); }
                audio = first == 'A';
                if (!audio && !(t0 == '5' && t1 == '2')) return 0;
                ntracks++; ht = 1; has0 = has1 = 0; i0 = i1 = pg = 0;
            }
        } else if (k0 == 'I' && k1 == 'N') {
            if (!ht || eol(c)) return 0;
            u32 n = number(&ok);
            if (!ok || !space(c)) return 0;
            skipsp();
            u32 f = frames(&ok);
            if (!ok) return 0;
            if (n == 0) { i0 = f; has0 = 1; }
            else if (n == 1) { i1 = f; has1 = 1; }
        } else if (k0 == 'P' && k1 == 'R') {
            if (!ht || has0 || has1) return 0;
            pg = frames(&ok);
            if (!ok || pg > 255) return 0;
        }
        while (!eol(c)) adv();
        if (c == -2) return 0;
    }
    if (ht) {
        if (!finish(has0, has1, i0, i1, audio, pg)) return 0;
    }
    return nfiles && ntracks;
}

// Build "<cue folder><name of file k>" in the path struct and open it into
// FILE_SLOT; its error (0 = open).
NOINLINE static u32 open(u32 k) {
    u32 off = fname[k] & 0xffff, len = fname[k] >> 16 & 0xff, at = off & 3, err;
    if (rd == DISC_SLOT) { open_file = k; return 0; }
    open_file = NONE;
    if (dirlen + len > 255) return 1;
    clen = 0;
    if ((err = cmd(K_READ, pick, off & ~3u, (len + at + 3) & ~3u))) return err;
    for (u32 i = 0; i < len; i++) { u32 x = CHUNK[at + i]; PATH[dirlen + i] = x == '\\' ? '/' : x; }
    PATH[dirlen + len] = 0;
    err = opn(FILE_SLOT);
    // A one-file cue whose .bin was renamed along with it: "<cue name>.bin".
    if (err && nphys == 1 && fb) {
        u32 i = 0;
        for (; i < 255 && base[i]; i++) PATH[i] = base[i];
        for (u32 m = 0; m < nmarks; m++) PATH[marks[m]] = fb;
        if (i > 4 && PATH[i - 4] == '.') {
            PATH[i - 3] = 'b'; PATH[i - 2] = 'i'; PATH[i - 1] = 'n'; PATH[i] = 0;
            err = opn(FILE_SLOT);
        }
    }
    IO[W_CLR] = CLR_MENU;
    if (err) return err;
    open_file = k;
    return 0;
}

// ---------------- track table (make_pocket_instances.py track_table) ----------------
// Divisions by constants as multiplies (exact for v < 1024 and r < 4500):
// SERV's are slow and the stream waits for the table.
// v / unit by subtraction (no divide on RV32I), v keeps the remainder
NOINLINE static u32 cnt(u32 *v, u32 unit) { u32 n = 0; while (*v >= unit) { *v -= unit; n++; } return n; }
static u32 bcd(u32 v) { u32 t = cnt(&v, 10); return t << 4 | v; }
static u32 msf(u32 v) {   // bcd(min) << 8 | bcd(sec) of v sectors, one BCD digit per count
    u32 r = cnt(&v, 45000) << 12; r |= cnt(&v, 4500) << 8;
    return r | cnt(&v, 750) << 4 | cnt(&v, 75);
}
NOINLINE static u32 table_word(u32 w) {
    if (w == 0) return bcd(ntracks) << 8 | ntracks;
    if (w == 1) return total + 150;
    if (w == 2) return msf(total + 150);
    if (w == 3) return lcmask;   // region (set by the stream), LibCrypt mask
    u32 r = (w - 4) >> 2;
    if (r >= ntracks) return 0;
    switch (w & 3) {
    case 0: return r ? tstart[r] + 150 : 0;
    case 1: return (r + 1 < ntracks ? tstart[r + 1] : total) + 149;
    case 2: return (r ? 1u << 16 : 0) | msf(ti1[r] + 150);
    default: return 0;
    }
}

// ---------------- mount ----------------
// How a mount reports: a Load disc pick sends the stream a slot update, a
// launch pick releases its allcomplete, a menu Disc does neither.
enum { M_PICK, M_LAUNCH, M_MENU, PRESET = 4 };   // PRESET: the path struct holds the cue's path

static u32 bin_slot;                     // a plain image's slot (bin)
static u32 m3u_n, m3u_slot, m3u_ent[4];  // playlist entries: offset | length << 16

static int is_disc(u32 id) { return id == 101 || id == 201 || id == 301 || id == 401; }

// Launch tries left after this one: a launch mount that fails is tried up
// to three times, a pause before each retry, before the stream (and the
// BIOS) sees no disc. retry_wait (start.S, at 0x10 so the bench can shorten
// it) is the pause in loop turns.
static u32 retry;
extern const u32 retry_wait;

#define fail(mode) fail_at(mode, __LINE__)
NOINLINE static void fail_at(int mode, u32 line) {
    IO[W_DBG] = line | oerr << 12;
    bin = 0;
    setf((flags & ~(F_MOUNTED | F_VDISC | F_VTABLE)) | F_FAILED | (mode == M_LAUNCH && !retry ? F_ALLC : 0));
    if (mode == M_PICK) IO[W_UPD] = 0;
}

static void cue(u32 slot, u32 size, int mode);

// A game JSON: APF loads its slots. One from make_pocket_instances.py has
// the track table in TABLE_SLOT; Pocket Sync's and pupdate's have only the
// cue in CUE_SLOT and its files, so the cue is read as if it had been picked.
static void game_json(void) {
    // A track table is 1,600 bytes; a 0.5.2 JSON has track 2's .bin there.
    // No cue readable: a cold card's failed read is tried again; after the
    // last try it is left to APF.
    if (!cmd(K_READ, TABLE_SLOT, 0, 4) && cmd(K_READ, TABLE_SLOT, 1600, 4)) goto apf;
    if (cmd(K_READ, CUE_SLOT, 0, 4)) {
        if (retry) { fail(M_LAUNCH); return; }
    apf:
        setf((flags & ~F_FAILED) | F_ALLC); return;
    }
    bin = 0; open_file = NONE;
    json = 1;
    cue(CUE_SLOT, probe(CUE_SLOT, 1, 13), M_LAUNCH);
    json = 0;
}

static void mount(u32 slot) {
    int mode = slot == LAUNCH_SLOT ? M_LAUNCH : M_PICK;
    pick = slot; cur = 0; m3u_n = 0;
    if (cmd(K_READ, slot, 0, 4)) {
        if (mode == M_LAUNCH) game_json();   // nothing readable: APF's game JSON
        else fail(mode);
        return;
    }
    u32 c0 = CHUNK[0];
    bin = 0; open_file = NONE;
    setf(flags & ~(F_MOUNTED | F_VDISC | F_VTABLE));
    if (mode == M_LAUNCH && c0 == '{') { game_json(); return; }
    if (c0 == 0) {
        // A raw sector: an ordinary disc image, found by size. Picked with
        // Load disc it goes to the stream; from the launch list it is read
        // through LAUNCH_SLOT.
        u32 n = probe(slot, 2352, 18);
        if (!n) { fail(M_LAUNCH); return; }
        if (mode == M_PICK) { setf(flags & ~F_FAILED); IO[W_UPD] = sectors_to_bytes(n); return; }
        IO[W_DISC] = sectors_to_bytes(n);
        bin = 1; bin_slot = LAUNCH_SLOT;
        setf((flags & ~F_FAILED) | F_VDISC | F_ALLC);
        return;
    }
    cue(slot, probe(slot, 1, 13), mode);
}

// Where base names its disc number ("(Disc d)" in the folder and the file
// name, at most four), and that number in cur.
NOINLINE static void find_marks(void) {
    static const char pat[] = "(Disc ";
    nmarks = 0; cur = 0;
    for (u32 i = 0; i + 7 < 256 && base[i]; i++) {
        u32 j = 0;
        while (j < 6 && base[i + j] == (u8)pat[j]) j++;
        if (j == 6 && base[i + 7] == ')' && base[i + 6] >= '1' && base[i + 6] <= '9' && nmarks < 4) {
            if (!cur) cur = base[i + 6] - '0';
            if (base[i + 6] - '0' == cur) marks[nmarks++] = i + 6;
        }
    }
}

// Open disc k's cue (base with its number) into CUE2_SLOT; its error.
static u32 open_disc(u32 k) {
    u32 i = 0;
    for (; i < 255 && base[i]; i++) PATH[i] = base[i];
    PATH[i] = 0;
    for (u32 m = 0; m < nmarks; m++) PATH[marks[m]] = '0' + k;
    fb = '0' + k;
    PATHW[64] = 0; PATHW[65] = 0;
    u32 err = opn(CUE2_SLOT);
    IO[W_CLR] = CLR_MENU;
    open_file = NONE;
    return err;
}

// The 16 LibCrypt sector addresses (BCD MM:SS:FF) behind mask bits 15..0
// (make_pocket_instances.py LIBCRYPT_MSF).
static const u32 lc_msf[16] = {0x030805, 0x030956, 0x031310, 0x031429, 0x031524, 0x031849, 0x032056, 0x032155,
                               0x032317, 0x032412, 0x032503, 0x032828, 0x033219, 0x033356, 0x033451, 0x033542};

// LibCrypt mask from the redump .sbi next to the cue (same name; path in
// the path struct up to n). 4-byte header, 14-byte entries; 0 if none.
// A game JSON's .sbi is read from its slot 99 when it has one
// (make_pocket_instances.py and the plugin put it there), loaded by APF:
// no open by path, which the Pocket refused (Ape Escape EU got no mask
// from the list). Older game JSONs still look beside the cue.
#define SBI_SLOT 99u
NOINLINE static u32 sbi_mask(u32 n) {
    u32 slot = SBI_SLOT, mask = 0;
    if (!json || cmd(K_READ, SBI_SLOT, 0, 4)) {
        if (n < 4 || PATH[n - 4] != '.') return 0;
        PATH[n - 3] = 's'; PATH[n - 2] = 'b'; PATH[n - 1] = 'i';
        u32 err = opn(FILE_SLOT);
        IO[W_CLR] = CLR_MENU;
        open_file = NONE;
        if (err) return 0;
        slot = FILE_SLOT;
    }
    u32 save_pick = pick, save_size = cue_size;
    pick = slot; cue_size = probe(slot, 1, 11);
    clen = 0;
    for (cpos = 4; cpos + 3 <= cue_size; cpos += 11) {
        int a = getb(), b = getb(), c3 = getb();
        if (c3 < 0) break;
        u32 msf = (u32)a << 16 | (u32)b << 8 | (u32)c3;
        for (u32 i = 0; i < 16; i++) if (msf == lc_msf[i]) mask |= 0x8000u >> i;
    }
    pick = save_pick; cue_size = save_size; clen = 0;
    return mask;
}

// A cue (at most 16 KiB) in slot: its folder, its text, its files. For a
// menu Disc the path struct already holds its path.
static void playlist(u32 slot, int mode);
static void cue(u32 slot, u32 size, int mode) {
    int preset = mode & PRESET;
    mode &= 3;
    pick = slot;
    if (!size || size > 0x3fff) { fail(mode); return; }
    cue_size = size;
    if (!preset && cmd(K_GETFILE, slot, 0, 0)) {
        if (!json) { fail(mode); return; }
        PATH[0] = 0;   // a game JSON's cue with no path: its .bin is in DISC_SLOT
    }
    u32 i = 0;
    dirlen = 0;
    for (; i < 255 && PATH[i]; i++) {
        if (PATH[i] == '/') dirlen = i + 1;
        if (!preset) base[i] = PATH[i];
    }
    if (i == 255) { fail(mode); return; }
    if (!preset) {
        base[i] = 0;
        if (i > 4 && PATH[i - 4] == '.' && upper(PATH[i - 3]) == 'M' && PATH[i - 2] == '3' && upper(PATH[i - 1]) == 'U') {
            playlist(slot, mode);
            return;
        }
        find_marks();
        fb = '0' + cur;
    }
    PATHW[64] = 0; PATHW[65] = 0;   // openfile flags and size: open an existing file (the .sbi too)
    lcmask = IO[R_STATUS] & ST_LC ? sbi_mask(i) : 0;
    IO[W_DBG + 7] = lcmask;
    if (!parse()) { fail(mode); return; }
    total = 0;
    // A game JSON whose slots 101, 102, ... hold the cue's files (pupdate,
    // Pocket Sync; 0.5.2's layout for several files, at most 19): APF has
    // loaded them, so file k is read from slot 101 + k with no path to
    // resolve (re-opening by the cue's folder failed on hardware).
    rd = DISC_SLOT;
    for (u32 k = 0; k < nfiles; k++)
        if (!json || fseg[k] >> 24 >= 19 || cmd(K_READ, DISC_SLOT + (fseg[k] >> 24), 0, 4)) rd = FILE_SLOT;
    // A first file holding track 1's pregap (INDEX 01 past its start, as
    // some rippers write): the disc starts at INDEX 01.
    lead = ti1[0] & 0xfffff;
    u32 psize = 0;
    for (u32 k = 0; k < nfiles; k++) {
        u32 b = fseg[k] & 0xffffff, n;
        if (!b) {   // a file's first segment: its size
            if (rd == FILE_SLOT && open(k)) { fail(mode); return; }
            psize = probe(rd == FILE_SLOT ? rd : rd + (fseg[k] >> 24), 2352, 18);
        }
        // up to the next segment of the same file, else its end
        n = k + 1 < nfiles && fseg[k + 1] >> 24 == fseg[k] >> 24 ? fseg[k + 1] & 0xffffff : psize;
        n = n > b ? n - b : 0;
        if (!k) n = n > lead ? n - lead : 0;
        total += fname[k] >> 24;   // PREGAP
        if (!n || total + n > 524138) { fail(mode); return; }   // 19-bit LBAs with the pregap
        fsec[k] = total;
        total += n;
    }
    fsec[nfiles] = total;
    // Files back to back; a track starts at INDEX 00 (else 01); track 1 is
    // data at 00:00:00, later tracks are audio, in order, inside the disc.
    u32 prev = 0;
    for (u32 r = 0; r < ntracks; r++) {
        u32 f = tstart[r] >> 24, s = tstart[r] & 0xfffff, x = ti1[r] & 0xfffff, a = ti1[r] >> 31;
        if (!s) s = fsec[f] - (fname[f] >> 24);   // a PREGAP starts the track
        else s += fsec[f];
        if (!f) { s = s > lead ? s - lead : 0; x = x > lead ? x - lead : 0; }
        x += fsec[f];
        if (r == 0 ? (a || s || x) : (!a || s <= prev)) { fail(mode); return; }
        if (s > x || x >= total) { fail(mode); return; }
        tstart[r] = s; ti1[r] = x; prev = x;
    }
    u32 bytes = sectors_to_bytes(total);
    IO[W_DISC] = bytes;
    setf((flags & ~F_FAILED) | F_MOUNTED | F_VDISC | F_VTABLE | (mode == M_LAUNCH ? F_ALLC : 0));
    if (mode == M_PICK) IO[W_UPD] = bytes;
}

// Open playlist entry k (1-4) into CUE2_SLOT: the playlist's folder
// (base up to dirlen) and the entry, or the entry alone if it starts with
// '/'. Its error.
static u32 m3u_dir;
NOINLINE static u32 open_entry(u32 k) {
    u32 off = m3u_ent[k - 1] & 0xffff, len = m3u_ent[k - 1] >> 16, at = off & 3, d = 0, err;
    clen = 0;
    if ((err = cmd(K_READ, m3u_slot, off & ~3u, (len + at + 3) & ~3u))) return err;
    if (CHUNK[at] != '/') d = m3u_dir;
    fb = 0;
    if (d + len > 255) return 1;
    for (u32 i = 0; i < d; i++) PATH[i] = base[i];
    for (u32 i = 0; i < len; i++) { u32 x = CHUNK[at + i]; PATH[d + i] = x == '\\' ? '/' : x; }
    PATH[d + len] = 0;
    PATHW[64] = 0; PATHW[65] = 0;
    err = opn(CUE2_SLOT);
    IO[W_CLR] = CLR_MENU;
    open_file = NONE;
    return err;
}

// Mount playlist entry k: a cue, or a plain image read through CUE2_SLOT.
static void entry(u32 k, int mode) {
    if (open_entry(k) || cmd(K_READ, CUE2_SLOT, 0, 4)) { fail(mode); return; }
    if (CHUNK[0] == 0) {
        u32 n = probe(CUE2_SLOT, 2352, 18), bytes = sectors_to_bytes(n);
        if (!n) { fail(mode); return; }
        bin = 1; bin_slot = CUE2_SLOT;
        IO[W_DISC] = bytes;
        setf((flags & ~(F_FAILED | F_MOUNTED | F_VTABLE)) | F_VDISC | (mode == M_LAUNCH ? F_ALLC : 0));
        if (mode == M_PICK) IO[W_UPD] = bytes;
        return;
    }
    bin = 0;
    cue(CUE2_SLOT, probe(CUE2_SLOT, 1, 13), mode | PRESET);
}

// A playlist in slot (base holds its path): up to four entries, then the
// first one.
static void playlist(u32 slot, int mode) {
    m3u_slot = slot; m3u_dir = dirlen; m3u_n = 0; nmarks = 0;
    cbase = clen = cpos = 0;
    c = getb();
    while (c >= 0 && m3u_n < 4) {
        while (c >= 0 && (eol(c) || space(c))) adv();
        if (c < 0) break;
        u32 off = cpos - 1, len = 0, keep = 0;
        int comment = c == '#';
        while (!eol(c)) { len++; if (!space(c)) keep = len; adv(); }
        if (!comment && keep < 256) m3u_ent[m3u_n++] = off | keep << 16;
    }
    if (c == -2 || !m3u_n) { fail(mode); return; }
    cur = 1;
    entry(1, mode);
}

// The menu asks for Disc k (1-4): load that sibling of the current cue or
// that playlist entry, if the game has it (a disc that fails to mount
// brings the current one back). The menu Disc is recorded either way.
static void menu_disc(u32 k) {
    if (m3u_n) {
        if (k > m3u_n || k == cur) return;
        entry(k, M_MENU);
        if (flags & F_FAILED) { k = cur; entry(k, M_MENU); }
        cur = k;
        return;
    }
    if (!cur || k == cur || !(flags & F_MOUNTED) || open_disc(k)) return;
    cue(CUE2_SLOT, probe(CUE2_SLOT, 1, 13), M_MENU | PRESET);
    if (!(flags & F_MOUNTED)) k = cur;
    if (!(flags & F_MOUNTED) && !open_disc(k)) cue(CUE2_SLOT, probe(CUE2_SLOT, 1, 13), M_MENU | PRESET);
    cur = k;
    for (u32 m = 0; m < nmarks; m++) base[marks[m]] = '0' + k;
}

// ---------------- the stream's reads ----------------
static void serve(void) {
    u32 il = IO[R_STATUS], id = il >> 10 & 0x1ff, len = il >> 19 & 0xfff, off = IO[R_UOFF], err = 0;
    if ((flags & F_MOUNTED) && is_disc(id - 1)) {
        for (u32 w = 0; w < 400; w++) INJ[w] = table_word(w);
    } else if ((flags & F_MOUNTED) && is_disc(id)) {
        u32 k = 0;
        while (k + 1 < nfiles && off >= sectors_to_bytes(fsec[k + 1] - (fname[k + 1] >> 24))) k++;
        if (off < sectors_to_bytes(fsec[k])) {
            for (u32 w = 0; w < len >> 2; w++) INJ[w] = 0;   // a PREGAP: silence
        } else {
            if (open_file != k) err = open(k);
            if (!err) err = cmd(K_READ | K_USEU, rd == FILE_SLOT ? rd : rd + (fseg[k] >> 24),
                                off - sectors_to_bytes(fsec[k] - (k ? 0 : lead) - (fseg[k] & 0xffffff)), len);
        }
    } else {
        err = cmd(K_READ | K_USEU, bin && is_disc(id) ? bin_slot : id, off, len);
    }
    IO[W_DONE] = err;
}

static u32 menu_now(void) { return IO[R_STATUS] >> 8 & 3; }

// ---------------- cheats ----------------
// GameShark codes: type << 24 | address, and the value.
#define CHT_SLOT 98u
#define MAXC 32u
static u32 ch_a[MAXC] BIG;
static unsigned short ch_v[MAXC] BIG;
static u32 ncheat, vbl_seen;

// Codes from the text in CHT_SLOT: an 8-digit hex word followed by a 4-digit
// one ("80012345 0063", libretro's "80012345+0063"); lines starting with #
// or ; are comments. A libretro .cht keeps a cheat's codes only when its
// "cheatN_enable = true" line follows them (a line naming "enable" without
// "true" drops the codes since the previous such line); a plain list has no
// such lines and keeps every code.
static int hexval(int x) { x = upper(x); return digit(x) ? x - '0' : x >= 'A' && x <= 'F' ? x - 'A' + 10 : -1; }
NOINLINE static void load_cheats(void) {
    ncheat = 0;
    if (cmd(K_READ, CHT_SLOT, 0, 4)) return;
    pick = CHT_SLOT; cue_size = probe(CHT_SLOT, 1, 14);
    cbase = clen = cpos = 0;
    u32 run = 0, val = 0, a8 = 0, have = 0, last4 = 0, en = 0, mark = 0;
    int line = 1;
    for (;;) {
        if (IO[R_STATUS] & ST_U) serve();   // the game may already read the disc
        int x = getb(), h = x < 0 ? -1 : hexval(x);
        if (line && (x == '#' || x == ';')) { while (x >= 0 && !eol(x)) x = getb(); }
        line = eol(x);
        last4 = last4 << 8 | (x | 0x20);
        if (last4 == 0x61626c65) en |= 1;        // "able"
        if (last4 == 0x74727565) en |= 2;        // "true"
        if (line) { if (en & 1) { if (!(en & 2)) ncheat = mark; mark = ncheat; } en = 0; }
        if (h >= 0) { val = val << 4 | h; run++; continue; }
        if (run == 8) { a8 = val; have = 1; }
        else if (run == 4 && have && ncheat < MAXC) { ch_a[ncheat] = a8; ch_v[ncheat++] = val; have = 0; }
        else if (run) have = 0;
        run = val = 0;
        if (x < 0) break;
    }
}

// One main RAM access (byte, else halfword); a read's value.
NOINLINE static u32 ram(u32 a, u32 rnw, u32 byte, u32 v) {
    IO[W_IDLEN] = (a & 0x1ff) | ((a >> 9 & 0xfff) | rnw << 12) << 16;
    IO[W_OFF] = (v & 0xffff) | byte << 16;
    IO[W_CLR] = CLR_CHEAT;
    u32 q;
    while ((q = IO[R_CHEAT]) & CH_BUSY) ;
    q &= 0xffff;
    return byte ? q >> ((a & 1) << 3) & 0xff : q;
}

// One frame of codes: writes (80 16-bit, 30 8-bit), increments (10, 11
// 16-bit; 20, 21 8-bit), conditions on the next code (D0-D3 16-bit, E0-E3
// 8-bit: equal, not equal, less, greater) and the serial 50 code. Others
// are ignored. The stream's reads go first.
NOINLINE static void apply_cheats(void) {
    for (u32 i = 0; i < ncheat; i++) {
        if (IO[R_STATUS] & ST_U) serve();
        u32 a = ch_a[i], t = a >> 24, v = ch_v[i], byte = 0;
        a &= 0x1fffff;
        switch (t) {
        case 0x30: byte = 1; /* fall through */
        case 0x80: ram(a, 0, byte, v); break;
        case 0x20: case 0x21: byte = 1; /* fall through */
        case 0x10: case 0x11: {
            u32 x = ram(a, 1, byte, 0);
            ram(a, 0, byte, t & 1 ? x - v : x + v);
            break;
        }
        case 0xE0: case 0xE1: case 0xE2: case 0xE3: byte = 1; v &= 0xff; /* fall through */
        case 0xD0: case 0xD1: case 0xD2: case 0xD3: {
            u32 x = ram(a, 1, byte, 0), k = t & 3;
            if (!(k == 0 ? x == v : k == 1 ? x != v : k == 2 ? x < v : x > v)) i++;
            break;
        }
        case 0x50: {
            if (i + 1 >= ncheat) break;
            u32 n = a >> 8 & 0xff, step = a & 0xff, b = ch_a[++i], w = ch_v[i];
            byte = b >> 24 == 0x30;
            b &= 0x1fffff;
            for (u32 k = 0; k < n; k++, b += step, w += v) ram(b, 0, byte, w);
            break;
        }
        }
    }
}

void main(void) {
    nfiles = ntracks = total = dirlen = cue_size = bin = cur = ncheat = 0;
    cbase = clen = cpos = 0;
    open_file = NONE; pick = DISC_SLOT;
    setf(F_IDLE | F_ACCEPT);
    for (;;) {
        u32 s = IO[R_STATUS];
        if (s & ST_U) { serve(); continue; }
        if (!(s & (ST_BOOT | ST_UPD | ST_MENU))) {
            if (ncheat) {
                u32 cs = IO[R_CHEAT], v = cs >> 30 & 1;
                if (v != vbl_seen) { vbl_seen = v; if (cs & CH_ON) apply_cheats(); }
            }
            continue;
        }
        // A pick or a menu Disc: stop the stream's reads (serve one that
        // slipped in).
        setf(flags & ~(F_IDLE | F_ACCEPT));
        if (IO[R_STATUS] & ST_U) serve();
        u32 m = menu_now();
        if (s & ST_BOOT) {
            // A cold first launch (after a restart or card writes) can fail a
            // read; a later try reads cached data.
            IO[W_CLR] = ST_BOOT | CLR_MENU;
            setf(flags & ~F_FAILED);
            for (retry = 2;; retry--) {
                mount(LAUNCH_SLOT);
                if (!(flags & F_FAILED) || !retry) break;
                for (u32 n = retry_wait; n; n--) __asm__ volatile("");
            }
        }
        else if (s & ST_UPD) { IO[W_CLR] = ST_UPD | CLR_MENU; ncheat = 0; mount(DISC_SLOT); }
        else menu_disc(m + 1);
        setf((flags & ~F_DISC) | m << 8 | F_IDLE | (flags & F_MOUNTED ? 0 : F_ACCEPT));
        // The launched game's cheat list, read while the stream may run.
        if (s & ST_BOOT) load_cheats();
    }
}
