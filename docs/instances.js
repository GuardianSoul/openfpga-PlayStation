// SPDX-License-Identifier: MIT
// Game files for the PlayStation core on an Analogue Pocket SD card.
//
// A browser port of scripts/make_pocket_instances.py (keep the two, and
// tools/pocket-plugin, in step; tests/test_sd_card_page.py runs both on the
// same card and compares). It
// works on File System Access API directory handles, so the page can write
// straight to the SD card in Chrome or Edge. CHD images still need the
// script or the plugin, because they have to be decompressed.
(function (exports) {
  'use strict';

  const SECTOR = 2352;
  const PREGAP = 150;
  const LIBCRYPT_SLOT = 99;
  const CHEAT_SLOT = 98;
  const CARD_SLOT = 16;
  const DISC = / \(Disc \d+\)/;
  const DISC_N = /\s*\(Disc (\d+)\)/i;
  const DISC_N_ALL = /\s*\(Disc (\d+)\)/gi;
  // Files a cue-reader core reads from the game file's slots 101+ (disc 1).
  const CUE_FILE_SLOTS = 19;
  // Other tags in parentheses (region, release, languages) can differ between
  // a game's discs, so grouping uses the title without them.
  const RELEASE = /\s*\([^()]*\)/g;

  // ---- paths: arrays of names below the card root -------------------------

  const stem = (name) => { const i = name.lastIndexOf('.'); return i > 0 ? name.slice(0, i) : name; };
  const comparePaths = (a, b) => {
    for (let i = 0; i < Math.min(a.length, b.length); i++) {
      if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
    }
    return a.length - b.length;
  };
  const posix = (segs) => segs.length ? segs.join('/') : '.';

  async function dirAt(root, segs, create = false) {
    let dir = root;
    for (const name of segs) {
      try { dir = await dir.getDirectoryHandle(name, { create }); } catch (e) { return null; }
    }
    return dir;
  }

  async function fileAt(root, segs) {
    const dir = await dirAt(root, segs.slice(0, -1));
    if (!dir) return null;
    try { return await (await dir.getFileHandle(segs[segs.length - 1])).getFile(); } catch (e) { return null; }
  }

  async function writeFile(root, segs, data) {
    const dir = await dirAt(root, segs.slice(0, -1), true);
    const handle = await dir.getFileHandle(segs[segs.length - 1], { create: true });
    const out = await handle.createWritable();
    try {
      for (const part of Array.isArray(data) ? data : [data]) await out.write(part);
    } finally { await out.close(); }
  }

  // Space kept free on the card while a large file is written, as the plugin does.
  const RESERVE = 256 << 20;
  const mb = (bytes) => Math.ceil(bytes / (1 << 20));

  // Write a large file (a split track, a joined image) and read it back. The
  // file first claims its size plus RESERVE, so a card that is too full fails
  // before anything is written; whatever fails, the half-written file goes.
  async function writeChecked(root, segs, parts) {
    const blobs = parts.map((p) => (p instanceof Blob ? p : new Blob([p])));
    const size = blobs.reduce((n, b) => n + b.size, 0);
    try {
      const dir = await dirAt(root, segs.slice(0, -1), true);
      const out = await (await dir.getFileHandle(segs[segs.length - 1], { create: true })).createWritable();
      try {
        await out.truncate(size + RESERVE);
        await out.seek(0);
        for (const blob of blobs) await out.write(blob);
        await out.truncate(size);
      } catch (error) { try { await out.abort(); } catch (e) { /* already closed */ } throw error; }
      await out.close();
      const written = await fileAt(root, segs);
      if (!written || written.size !== size) throw new Error('the copy on the card has the wrong size');
      let offset = 0;
      for (const blob of blobs) {
        for (let at = 0; at < blob.size; at += 8 << 20) {
          const to = Math.min(at + (8 << 20), blob.size);
          const [a, b] = await Promise.all([blob.slice(at, to), written.slice(offset + at, offset + to)]
            .map(async (x) => new Uint8Array(await x.arrayBuffer())));
          if (a.length !== b.length || a.some((v, i) => v !== b[i])) throw new Error('the copy on the card does not read back the same');
        }
        offset += blob.size;
      }
    } catch (error) {
      await removeFile(root, segs);
      if (error.name === 'QuotaExceededError' || /space|quota/i.test(error.message)) {
        throw new Error(`not enough room on the SD card for ${segs[segs.length - 1]} (${mb(size)} MB, and ${mb(RESERVE)} MB kept free)`);
      }
      throw error;
    }
  }

  async function removeFile(root, segs) {
    const dir = await dirAt(root, segs.slice(0, -1));
    if (!dir) return;
    try { await dir.removeEntry(segs[segs.length - 1]); } catch (e) { /* missing is fine */ }
  }

  async function list(dir) {
    const out = [];
    for await (const [name, handle] of dir.entries()) out.push([name, handle.kind]);
    return out;
  }

  // Files under root whose name passes test, as paths below root, sorted.
  async function walk(root, test, base = []) {
    const found = [];
    for (const [name, kind] of await list(root)) {
      if (kind === 'directory') {
        found.push(...await walk(await root.getDirectoryHandle(name), test, [...base, name]));
      } else if (test(name) && !name.startsWith('._')) {
        found.push([...base, name]);
      }
    }
    return found.sort(comparePaths);
  }

  // A cue's FILE names are relative to its folder.
  const join = (folder, name) => [...folder, ...name.split('/').filter((s) => s && s !== '.')];

  // ---- cue sheets ----------------------------------------------------------

  // [[filename, [[track, type, {index: frames}]]]] in cue order.
  function parseCue(text) {
    const files = [];
    for (const line of text.split(/\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/)) {
      const words = line.trim();
      let m;
      if ((m = /^FILE\s+"(.+)"\s+\S+/.exec(words) || /^FILE\s+(\S+)\s+\S+/.exec(words))) {
        files.push([m[1], []]);
      } else if ((m = /^TRACK\s+(\d+)\s+(\S+)/.exec(words))) {
        if (!files.length) throw new Error('TRACK before FILE');
        files[files.length - 1][1].push([parseInt(m[1], 10), m[2].toUpperCase(), {}]);
      } else if ((m = /^(INDEX\s+(\d+)|PREGAP)\s+(\d+):(\d+):(\d+)/.exec(words))) {
        const tracks = files.length ? files[files.length - 1][1] : [];
        if (!tracks.length) throw new Error(m[1].split(/\s/)[0] + ' before TRACK');
        const key = m[2] !== undefined ? parseInt(m[2], 10) : 'pregap';
        tracks[tracks.length - 1][2][key] = (parseInt(m[3], 10) * 60 + parseInt(m[4], 10)) * 75 + parseInt(m[5], 10);
      }
    }
    return files;
  }

  async function readCue(card, path) {
    const file = await fileAt(card, path);
    return parseCue(new TextDecoder('utf-8').decode(await file.arrayBuffer()));
  }

  const rawTrack = (kind) => kind === 'AUDIO' || kind.endsWith('/2352') || kind.endsWith('/RAW');

  // Reason a cue-reader core cannot open this cue's files, or null.
  async function cueCheck(card, files, folder) {
    if (!files.length) return 'no FILE entries';
    for (const [name] of files) {
      const file = await fileAt(card, join(folder, name.replace(/\\/g, '/')));
      if (!file) return `${name}: file missing`;
      if (file.size % SECTOR) return `${name}: not whole 2352-byte sectors`;
    }
    return null;
  }

  // Whether every disc's cue is disc 1's path with "(Disc 1)" renumbered, as
  // the core's cue reader finds them.
  const siblingPaths = (cue1, others) =>
    [...others].every(([n, cue]) => cue1.join('/').replace(/\(Disc 1\)/g, `(Disc ${n})`) === cue.join('/'));

  // Reason the core cannot play this layout, or null.
  async function check(card, files, folder) {
    if (!files.length) return 'no FILE entries';
    for (let n = 0; n < files.length; n++) {
      const [name, tracks] = files[n];
      if (tracks.length !== 1) return `${name}: ${tracks.length} tracks in one file (merged image)`;
      const [number, kind, index] = tracks[0];
      const file = await fileAt(card, join(folder, name));
      if (!file) return `${name}: file missing`;
      if (file.size % SECTOR) return `${name}: not whole 2352-byte sectors`;
      if (n === 0) {
        if (kind === 'AUDIO' || index[1] !== 0) return 'track 1 must be data with INDEX 01 at 00:00:00';
      } else if (kind !== 'AUDIO' || (index[0] ?? 0) !== 0 || index[1] !== 150) {
        return `track ${number}: expected AUDIO with INDEX 00 00:00:00 and INDEX 01 00:02:00`;
      }
    }
    return null;
  }

  // Write cue's single merged image as one file per track in the layout
  // check() accepts: track 1 data from INDEX 01 00:00:00, every later track
  // starting with its 150-sector pregap (zeros when the cue's PREGAP has no
  // data in the image). Returns the new cue's path, or throws.
  async function splitCue(card, cue, dest) {
    const files = await readCue(card, cue);
    if (files.length !== 1) throw new Error(`${files.length} FILE entries (expected one merged image)`);
    const [name, tracks] = files[0];
    const folder = cue.slice(0, -1);
    const source = await fileAt(card, join(folder, name));
    if (!source) throw new Error(`${name}: file missing`);
    if (source.size % SECTOR) throw new Error(`${name}: not whole 2352-byte sectors`);
    const sectors = source.size / SECTOR;
    if (!tracks.length || tracks[0][1] === 'AUDIO' || tracks[0][2][1] !== 0 || tracks[0][2].pregap) {
      throw new Error('track 1 must be data with INDEX 01 at 00:00:00');
    }
    const plan = tracks.map(([number, kind, index], k) => {
      if (!rawTrack(kind)) throw new Error(`track ${number}: ${kind} is not 2352-byte sectors`);
      if (!(1 in index)) throw new Error(`track ${number}: no INDEX 01`);
      const start = k ? (0 in index ? index[0] : index[1]) : 0;
      if (k) {
        const stored = index[1] - start;
        if (stored < 0 || stored + (index.pregap || 0) !== PREGAP) throw new Error(`track ${number}: pregap is not 00:02:00`);
        if (kind !== 'AUDIO') throw new Error(`track ${number}: data after track 1`);
      }
      return { number, kind, start, zeros: k ? PREGAP - (index[1] - start) : 0 };
    });
    plan.forEach((entry, k) => {
      entry.end = k + 1 < plan.length ? plan[k + 1].start : sectors;
      if (!(entry.start < entry.end && entry.end <= sectors)) {
        throw new Error(`track ${entry.number}: indexes out of order or past the image`);
      }
    });
    const base = stem(cue[cue.length - 1]);
    const lines = [];
    for (const { number, kind, start, zeros, end } of plan) {
      const two = String(number).padStart(2, '0');
      const track = plan.length > 1 ? `${base} (Track ${two}).bin` : `${base}.bin`;
      await writeChecked(card, [...dest, track], [new Uint8Array(zeros * SECTOR), source.slice(start * SECTOR, end * SECTOR)]);
      lines.push(`FILE "${track}" BINARY`, `  TRACK ${two} ${kind}`);
      lines.push(...(number !== plan[0].number ? ['    INDEX 00 00:00:00', '    INDEX 01 00:02:00'] : ['    INDEX 01 00:00:00']));
    }
    const out = [...dest, base + '.cue'];
    await writeFile(card, out, lines.join('\n') + '\n');
    return out;
  }

  // ---- one-file discs (cores packaged with --one-file-disc) ------------------

  // {sources: [[path, sectors]], tracks: [[start, index01]]} for a disc
  // played as one image (its files back to back), or a reason string.
  async function oneFilePlan(card, files, folder) {
    if (!files.length) return 'no FILE entries';
    const sources = [], tracks = [];
    let offset = 0;
    for (const [name, entries] of files) {
      const path = join(folder, name);
      const file = await fileAt(card, path);
      if (!file) return `${name}: file missing`;
      if (file.size % SECTOR || !file.size) return `${name}: not whole 2352-byte sectors`;
      for (const [number, kind, index] of entries) {
        if (!rawTrack(kind)) return `track ${number}: ${kind} is not 2352-byte sectors`;
        if (!(1 in index)) return `track ${number}: no INDEX 01`;
        if (index.pregap) return `track ${number}: PREGAP outside the image`;
        if (tracks.length && kind !== 'AUDIO') return `track ${number}: data after track 1`;
        tracks.push([offset + (0 in index ? index[0] : index[1]), offset + index[1]]);
      }
      sources.push([path, file.size / SECTOR]); offset += file.size / SECTOR;
    }
    if (!tracks.length || files[0][1][0][1] === 'AUDIO' || tracks[0][0] !== 0 || tracks[0][1] !== 0) {
      return 'track 1 must be data with INDEX 01 at 00:00:00';
    }
    if (tracks.length > 99) return `${tracks.length} tracks (a CD has at most 99)`;
    for (let k = 0; k + 1 < tracks.length; k++) {
      if (!(tracks[k][1] < tracks[k + 1][0]) || !(tracks[k][0] <= tracks[k][1])) return 'indexes out of order or past the image';
    }
    if (!(tracks[tracks.length - 1][1] < offset)) return 'indexes out of order or past the image';
    if (offset + PREGAP > 524288) return "longer than the core's 19-bit LBAs";
    return { sources, tracks };
  }

  // The 16 LibCrypt sector addresses (BCD MM:SS:FF) behind mask bits 15..0.
  const LIBCRYPT_MSF = [0x030805, 0x030956, 0x031310, 0x031429, 0x031524, 0x031849, 0x032056, 0x032155,
                        0x032317, 0x032412, 0x032503, 0x032828, 0x033219, 0x033356, 0x033451, 0x033542];

  async function libcryptMask(card, sbi) {
    const data = new Uint8Array(await (await fileAt(card, sbi)).arrayBuffer());
    let mask = 0;
    for (let o = 4; o < data.length - 2; o += 14) {
      const k = LIBCRYPT_MSF.indexOf((data[o] << 16) | (data[o + 1] << 8) | data[o + 2]);
      if (k >= 0) mask |= 1 << (15 - k);
    }
    return mask;
  }

  const bcd = (v) => Math.floor(v / 10) * 16 + v % 10;

  // The core's 400 track-info words as little-endian bytes.
  function trackTable(tracks, sectors, mask, region = 2) {
    const words = new Uint32Array(400);
    const msf = (lba) => { const s = Math.floor(lba / 75); return (bcd(Math.floor(s / 60)) << 8) | bcd(s % 60); };
    words.set([(bcd(tracks.length) << 8) | tracks.length, sectors + PREGAP, msf(sectors + PREGAP), ((region << 16) | mask) >>> 0]);
    tracks.forEach(([start, index01], k) => {
      const last = (k + 1 < tracks.length ? tracks[k + 1][0] : sectors) + PREGAP - 1;
      words.set([k ? start + PREGAP : 0, last, ((k ? 1 : 0) << 16) | msf(index01 + PREGAP), 0], 4 * k + 4);
    });
    const bytes = new Uint8Array(1600), view = new DataView(bytes.buffer);
    words.forEach((w, i) => view.setUint32(4 * i, w, true));
    return bytes;
  }

  // Write the one-file image (when the cue lists several files) and its
  // .trk table: [image path, table path, tracks] or a reason string.
  async function writeOneFile(card, cue, files, sbi) {
    const folder = cue.slice(0, -1);
    const plan = await oneFilePlan(card, files, folder);
    if (typeof plan === 'string') return plan;
    const total = plan.sources.reduce((n, [, s]) => n + s, 0);
    let image = plan.sources[0][0];
    if (plan.sources.length > 1) {
      image = [...folder, `${stem(cue[cue.length - 1])} (one file).bin`];
      const existing = await fileAt(card, image);
      if (!(existing && existing.size === total * SECTOR)) {
        const parts = [];
        for (const [path] of plan.sources) parts.push(await fileAt(card, path));
        try { await writeChecked(card, image, parts); } catch (error) { return error.message; }
      }
    }
    const table = [...image.slice(0, -1), stem(image[image.length - 1]) + '.trk'];
    await writeFile(card, table, trackTable(plan.tracks, total, sbi ? await libcryptMask(card, sbi) : 0));
    return [image, table, plan.tracks.length];
  }

  // Split merged single-.bin cues under common into <name>/ folders next to
  // them. With removeOriginals the merged .bin and its cue are deleted once
  // every split track has been written and read back, so a small card needs
  // room for one game at a time. A failed split leaves the original as it was.
  // CHD images are reported, not converted.
  // One-file cores play merged images as they are: nothing is split.
  async function convert(card, common, log, removeOriginals = false, oneFile = false) {
    let done = 0, failed = 0;
    const images = [...await walk(await dirAt(card, common), (n) => /\.chd$/i.test(n)),
                    ...(oneFile ? [] : await walk(await dirAt(card, common), (n) => /\.cue$/i.test(n)))];
    for (const rel of images) {
      const image = [...common, ...rel];
      const folder = image.slice(0, -1), base = stem(image[image.length - 1]);
      const dest = [...folder, base];
      if (folder[folder.length - 1] === base || await fileAt(card, [...dest, base + '.cue'])) continue;
      const shown = rel.join('/');
      if (/\.chd$/i.test(image[image.length - 1])) {
        log(`skip ${shown}: CHD images need converting on a computer first (see the README)`); failed++; continue;
      }
      let files;
      try { files = await readCue(card, image); } catch (e) { continue; }
      if (files.length !== 1 || files[0][1].length < 2) continue;
      if (await fileAt(card, [...folder, base + '.chd'])) continue;
      try {
        const out = await splitCue(card, image, dest);
        log(`converted ${shown} -> ${out.slice(common.length).join('/')}`); done++;
        if (removeOriginals) {
          await removeFile(card, join(folder, files[0][0]));
          await removeFile(card, image);
          log(`removed the original ${files[0][0]} and ${image[image.length - 1]}`);
        }
      } catch (error) {
        const parent = await dirAt(card, folder);
        try { await parent.removeEntry(base, { recursive: true }); } catch (e) { /* nothing written */ }
        log(`skip ${shown}: ${error.message}`); failed++;
      }
    }
    return [done, failed];
  }

  // ---- game files ----------------------------------------------------------

  // 'Game (USA) (Disc 2)' -> 'Game (USA).sav'; null for single-disc games.
  const sharedCardName = (s) => (DISC.test(s) ? s.replace(DISC, '') + '.sav' : null);

  // Python's json.dumps(value, indent=2): non-ASCII as \uXXXX.
  const jsonDumps = (value) => JSON.stringify(value, null, 2)
    .replace(/[\u0080-\uffff]/g, (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));

  // The disc's .sbi: <cue name>.sbi, else the only .sbi in its folder.
  async function findSbi(card, cue) {
    const folder = cue.slice(0, -1);
    const same = stem(cue[cue.length - 1]) + '.sbi';
    if (await fileAt(card, [...folder, same])) return same;
    const found = (await list(await dirAt(card, folder)))
      .filter(([n, kind]) => kind === 'file' && n.endsWith('.sbi')).map(([n]) => n).sort();
    return found.length === 1 ? found[0] : null;
  }

  // The game's cheat list: <cue name>.cht, <cue file name>.cht, or the name without its "(Disc n)" tag with .cht.
  async function findCht(card, cue) {
    const folder = cue.slice(0, -1), name = cue[cue.length - 1], s = stem(name);
    for (const c of [s + '.cht', name + '.cht', s.replace(DISC_N_ALL, '').trim() + '.cht']) {
      if (await fileAt(card, [...folder, c])) return c;
    }
    return null;
  }

  // Start Saves/<platform>/common/<name> from the newest per-disc card. Never overwrites.
  async function seedSharedCard(card, platform, core, stems, name) {
    const shared = ['Saves', platform, 'common', name];
    if (await fileAt(card, shared)) return null;
    let newest = null;
    for (const s of stems) {
      const file = await fileAt(card, ['Saves', platform, core, s + '.sav']);
      if (file && (!newest || file.lastModified > newest[1].lastModified)) newest = [s + '.sav', file];
    }
    if (!newest) return null;
    await writeFile(card, shared, newest[1]);
    return newest[0];
  }

  // Cores on the card that run PlayStation games: [{id, platform}].
  async function findCores(card) {
    const cores = await dirAt(card, ['Cores']);
    if (!cores) return [];
    const found = [];
    for (const [name, kind] of await list(cores)) {
      if (kind !== 'directory') continue;
      const file = await fileAt(card, ['Cores', name, 'core.json']);
      let platform = null;
      try { platform = JSON.parse(await file.text()).core.metadata.platform_ids[0]; } catch (e) { continue; }
      if (platform === 'playstation' || platform === 'psx') found.push({ id: name, platform });
    }
    return found.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  }

  // Write Assets/<platform>/<core>/<game>.json for every playable disc under
  // Assets/<platform>/common. options: {core: 'GuardianSoul.PlayStation',
  // platform: 'playstation', convert: bool, removeOriginals: bool}. log(line)
  // gets the report.
  // ---- .bin files without a cue --------------------------------------------

  // The cue written for a .bin that came without one: one data track.
  const loneBinCue = (binName) => `FILE "${binName}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n`;

  // .bin files under common that no cue in their folder names and that have
  // no <name>.cue: [[path, reason or null]]. A reason means it is skipped: a
  // "(Track n)" file is part of a disc whose cue is missing.
  async function loneBins(card, common) {
    const found = [];
    for (const rel of await walk(await dirAt(card, common), (n) => /\.bin$/i.test(n))) {
      const bin = [...common, ...rel], folder = bin.slice(0, -1), name = bin[bin.length - 1];
      if (name.endsWith(' (one file).bin') || await fileAt(card, [...folder, stem(name) + '.cue'])) continue;
      const named = new Set();
      let broken = false;
      for (const [cueName, kind] of await list(await dirAt(card, folder))) {
        if (kind !== 'file' || !/\.cue$/i.test(cueName) || cueName.startsWith('._')) continue;
        try {
          for (const [f] of await readCue(card, [...folder, cueName])) named.add(join(folder, f.replace(/\\/g, '/')).join('/'));
        } catch (e) { broken = true; }
      }
      if (broken || named.has(bin.join('/'))) continue;
      const file = await fileAt(card, bin);
      const reason = /\(Track \d+\)/i.test(stem(name)) ? 'a track file whose .cue is missing'
        : file.size % SECTOR ? 'not whole 2352-byte sectors' : null;
      found.push([bin, reason]);
    }
    return found;
  }

  // Give each lone .bin a one-track <name>.cue so it gets a game file.
  // Returns how many were skipped. The write keeps RESERVE free.
  async function writeLoneCues(card, common, say) {
    let skipped = 0;
    for (const [bin, reason] of await loneBins(card, common)) {
      const shown = bin.slice(common.length).join('/');
      if (reason) { say(`skip ${shown}: ${reason}`); skipped++; continue; }
      const name = bin[bin.length - 1];
      const cue = [...bin.slice(0, -1), stem(name) + '.cue'];
      try {
        await writeChecked(card, cue, [new TextEncoder().encode(loneBinCue(name))]);
        say(`wrote ${cue.slice(common.length).join('/')} (one data track; a game with CD music needs its own .cue)`);
      } catch (e) {
        say(`skip ${shown}: ${/not enough room/.test(e.message) ? 'not enough free space for its .cue' : e.message}`);
        skipped++;
      }
    }
    return skipped;
  }

  async function makeInstances(card, options, log = () => {}) {
    const { core, platform = 'playstation' } = options;
    let declared = null;
    try { declared = JSON.parse(await (await fileAt(card, ['Cores', core, 'data.json'])).text()).data.data_slots; } catch (e) { declared = null; }
    if (!Array.isArray(declared)) declared = null;
    const ids = (declared || []).map((s) => s && s.id).filter(Number.isInteger);
    const maxTracks = declared === null ? 8 : ids.filter((id) => id >= 101 && id <= 199).length;
    const sharedCards = declared === null || ids.includes(CARD_SLOT);
    const libcrypt = declared !== null && ids.includes(LIBCRYPT_SLOT);
    const cheats = declared !== null && ids.includes(CHEAT_SLOT);
    // --one-file-disc cores: one .bin plus a .trk table per disc (slots 100n+1, 100n+2).
    // --cue-mount cores read the cue themselves (their launch slot takes .cue files).
    const cueReader = declared !== null && declared.some((s) => s && s.id === 0 && (s.extensions || []).includes('cue'));
    // Cue-reader cores read .m3u playlists (up to four discs); slot 100 taking
    // them means a game file can name one.
    const playlistSlot = declared !== null && declared.some((s) => s && s.id === 100 && (s.extensions || []).includes('m3u'));
    const oneFile = !cueReader && declared !== null && declared.some((s) => s && s.id === 102 && (s.extensions || []).includes('trk'));
    const common = ['Assets', platform, 'common'];
    const out = ['Assets', platform, core];
    const commonDir = await dirAt(card, common);
    if (!commonDir) throw new Error(`no ${common.join('/')} folder on this card`);
    const summary = { written: 0, skipped: 0, converted: 0, convertFailed: 0, shared: 0, lines: [] };
    const say = (line) => { summary.lines.push(line); log(line); };
    if (options.convert) {
      [summary.converted, summary.convertFailed] = await convert(card, common, say, Boolean(options.removeOriginals), oneFile || cueReader);
      say(`${summary.converted} images converted, ${summary.convertFailed} failed`);
    }
    summary.skipped += await writeLoneCues(card, common, say);
    const discTracks = {};
    for (let n = 2; n < 10; n++) discTracks[n] = ids.filter((id) => id >= 100 * n + 1 && id <= 100 * n + 99).length;
    let discsDeclared = 1;
    for (let n = 2; n < 10; n++) if (discTracks[n]) discsDeclared++;
    const discs = new Map();   // shared card name -> stems of its discs
    const games = new Map();   // game name -> Map(disc number -> [[cue, files, tags]])
    const writeInstance = async (name, slots, prefix) => {
      const instance = { instance: { magic: 'APF_VER_1', data_path: prefix, data_slots: slots } };
      await writeFile(card, [...out, name + '.json'], jsonDumps(instance) + '\n');
    };
    // Write cue's own game file (sharedCard: its game's shared card name, if set).
    const single = async (cue, files, trackCount, sharedCard = null) => {
      const folder = cue.slice(0, -1), cueName = cue[cue.length - 1], cueStem = stem(cueName);
      const dataPath = posix(folder.slice(common.length));
      const slots = [{ id: 100, filename: cueName }, ...files.map(([n], k) => ({ id: 101 + k, filename: n }))];
      let sbi = null;
      if (cueReader) sbi = await fileAt(card, [...folder, cueStem + '.sbi']) ? cueStem + '.sbi' : null;
      else if (libcrypt || oneFile) sbi = await findSbi(card, cue);
      // Cue-reader cores take a game JSON's .sbi from slot 99 (its cue has no path on the Pocket).
      if (sbi && (cueReader ? libcrypt : !oneFile)) slots.push({ id: LIBCRYPT_SLOT, filename: sbi });
      const cht = cheats ? await findCht(card, cue) : null;
      if (cht) slots.push({ id: CHEAT_SLOT, filename: cht });
      let prefix = dataPath !== '.' ? dataPath + '/' : '';
      const shared = sharedCards ? sharedCard || sharedCardName(cueStem) : null;
      if (shared) {
        for (const s of slots) s.filename = prefix + s.filename;
        slots.push({ id: CARD_SLOT, filename: shared });
        if (!discs.has(shared)) discs.set(shared, []);
        discs.get(shared).push(cueStem);
        prefix = '';
      }
      await writeInstance(cueStem, slots, prefix);
      const count = oneFile || cueReader ? trackCount : files.length;
      const kind = oneFile ? ', one file' : cueReader ? ', cue' : '';
      say(`wrote ${cueStem}.json (${count} track${count > 1 ? 's' : ''}${kind}${sbi ? ', LibCrypt ' + sbi : ''}${cht ? ', cheats ' + cht : ''})`);
    };
    for (const rel of await walk(commonDir, (n) => /\.cue$/i.test(n))) {
      const cue = [...common, ...rel];
      const folder = cue.slice(0, -1), cueName = cue[cue.length - 1], cueStem = stem(cueName);
      let files = null, reason = null, trackCount = 0;
      try {
        files = await readCue(card, cue);
        reason = cueReader ? await cueCheck(card, files, folder) : oneFile ? null : await check(card, files, folder);
      } catch (e) { reason = e.message; }
      if ((oneFile || cueReader) && folder[folder.length - 1] === cueStem && await fileAt(card, [...folder.slice(0, -1), cueName])) continue;   // split copy of a merged cue
      if (!reason && oneFile) {
        const made = await writeOneFile(card, cue, files, await findSbi(card, cue).then((s) => s && [...folder, s]));
        if (typeof made === 'string') reason = made;
        else { files = made.slice(0, 2).map((p) => [p.slice(folder.length).join('/')]); trackCount = made[2]; }
      }
      if (!reason && cueReader) {   // disc 1 boots from its slots, the rest by path
        trackCount = files.reduce((n, [, tracks]) => n + tracks.length, 0);
        if (files.length > Math.min(CUE_FILE_SLOTS, maxTracks)) files = [];   // only slots the core declares
      }
      if (!reason && !oneFile && !cueReader && files.length > maxTracks) reason = `${files.length} track files (core takes ${maxTracks})`;
      if (reason && await fileAt(card, [...folder, cueStem, cueStem + '.cue'])) continue;   // converted copy is listed
      if (reason) { say(`skip ${rel.join('/')}: ${reason}`); summary.skipped++; continue; }
      const disc = DISC_N.exec(cueStem);
      if (disc && discsDeclared > 1) {
        const name = cueStem.replace(DISC_N_ALL, '');
        const game = name.replace(RELEASE, '').trim();
        if (!games.has(game)) games.set(game, new Map());
        const found = games.get(game), number = parseInt(disc[1], 10);
        if (!found.has(number)) found.set(number, []);
        found.get(number).push([cue, files, name.match(RELEASE) || [], trackCount]);
        continue;
      }
      await single(cue, files, trackCount);
      summary.written++;
    }
    for (const game of [...games.keys()].sort()) {
      const found = games.get(game);
      const firstTags = JSON.stringify(found.has(1) ? found.get(1)[0][2] : []);
      const parts = new Map();
      for (const [n, candidates] of found) {
        const pick = candidates.find((c) => JSON.stringify(c[2]) === firstTags) || candidates[0];
        parts.set(n, pick);
      }
      const numbers = [...parts.keys()].sort((a, b) => a - b);
      if (numbers.some((n, i) => n !== i + 1) || numbers.length > discsDeclared) {
        say(`skip ${game}: discs [${numbers.join(', ')}] (core takes discs 1-${discsDeclared})`); summary.skipped++; continue;
      }
      if (cueReader && !siblingPaths(parts.get(1)[0], numbers.slice(1).map((n) => [n, parts.get(n)[0]]))) {
        // The core can't find the other discs by name: a playlist lists them.
        const stem1 = stem(parts.get(1)[0].slice(-1)[0]), name = stem1.replace(DISC_N_ALL, '').trim();
        const folders = numbers.map((n) => parts.get(n)[0].slice(0, -1));
        let depth = 0;
        while (folders.every((f) => depth < f.length && f[depth] === folders[0][depth])) depth++;
        const root = folders[0].slice(0, depth), m3u = [...root, name + '.m3u'];
        const shown = m3u.slice(common.length).join('/');
        const shared = sharedCardName(stem1);
        if (numbers.length <= 4) {
          await writeFile(card, m3u, numbers.map((n) => parts.get(n)[0].slice(root.length).join('/') + '\n').join(''));
        }
        if (numbers.length <= 4 && playlistSlot) {
          const dataPath = posix(root.slice(common.length)), prefix = dataPath !== '.' ? dataPath + '/' : '';
          const useShared = sharedCards && shared;
          const [cue1, files1] = parts.get(1);
          const slots = [{ id: 100, filename: name + '.m3u' },
                         ...files1.map(([f], k) => ({ id: 101 + k, filename: join(cue1.slice(0, -1), f).slice(root.length).join('/') }))];
          if (useShared) {
            for (const s of slots) s.filename = prefix + s.filename;
            slots.push({ id: CARD_SLOT, filename: shared });
            if (!discs.has(shared)) discs.set(shared, []);
            discs.get(shared).push(...numbers.map((n) => stem(parts.get(n)[0].slice(-1)[0])));
          }
          await writeInstance(name, slots, useShared ? '' : prefix);
          for (const n of numbers) {
            const s = stem(parts.get(n)[0].slice(-1)[0]);
            if (s !== name) await removeFile(card, [...out, s + '.json']);
          }
          say(`wrote ${name}.json (${numbers.length} discs, playlist ${shown})`);
          summary.written++;
          continue;
        }
        if (numbers.length <= 4) {
          say(`${game}: the discs' names differ by more than their number: wrote the playlist ${shown} ` +
              "(pick it in the core's file list to swap discs); each disc also gets its own game file");
        } else {
          say(`${game}: the discs' names differ by more than their number, so each disc gets its own game file`);
        }
        for (const n of numbers) { const [cue, files, , count] = parts.get(n); await single(cue, files, count, shared); summary.written++; }
        continue;
      }
      const tooLong = oneFile || cueReader ? [] : numbers.slice(1).filter((n) => parts.get(n)[1].length > discTracks[n]);
      if (tooLong.length) {
        say(`skip ${game}: disc ${tooLong[0]} has more track files than the core declares`); summary.skipped++; continue;
      }
      // The folder all discs sit under.
      const folders = numbers.map((n) => parts.get(n)[0].slice(0, -1));
      let depth = 0;
      while (folders.every((f) => depth < f.length && f[depth] === folders[0][depth])) depth++;
      const root = folders[0].slice(0, depth);
      const rel = (cue, name) => join(cue.slice(0, -1), name).slice(root.length).join('/');
      const cue1 = parts.get(1)[0], cue1Name = cue1[cue1.length - 1], cue1Stem = stem(cue1Name);
      const slots = [{ id: 100, filename: rel(cue1, cue1Name) }];
      for (const n of cueReader ? numbers.slice(0, 1) : numbers) {   // the cue reader finds the other discs by path
        const [cue, files] = parts.get(n);
        files.forEach(([name], k) => slots.push({ id: 100 * n + 1 + k, filename: rel(cue, name) }));
      }
      const sbi = !libcrypt ? null : cueReader ? (await fileAt(card, [...cue1.slice(0, -1), cue1Stem + '.sbi']) ? cue1Stem + '.sbi' : null)
        : await findSbi(card, cue1);
      if (sbi) slots.push({ id: LIBCRYPT_SLOT, filename: rel(cue1, sbi) });
      const cht = cheats ? await findCht(card, cue1) : null;
      if (cht) slots.push({ id: CHEAT_SLOT, filename: rel(cue1, cht) });
      const dataPath = posix(root.slice(common.length));
      let prefix = dataPath !== '.' ? dataPath + '/' : '';
      const shared = sharedCards ? sharedCardName(cue1Stem) : null;
      if (shared) {   // the same shared card as the per-disc files would use
        for (const s of slots) s.filename = prefix + s.filename;
        slots.push({ id: CARD_SLOT, filename: shared });
        if (!discs.has(shared)) discs.set(shared, []);
        discs.get(shared).push(...numbers.map((n) => stem(parts.get(n)[0].slice(-1)[0])));
        prefix = '';
      }
      // Named after disc 1 without its disc tag ("Final Fantasy VII (USA).json").
      const name = cue1Stem.replace(DISC_N_ALL, '').trim();
      await writeInstance(name, slots, prefix);
      for (const n of numbers) {   // per-disc files from earlier runs
        const s = stem(parts.get(n)[0].slice(-1)[0]);
        if (s + '.json' !== name + '.json') await removeFile(card, [...out, s + '.json']);
      }
      say(`wrote ${name}.json (${numbers.length} disc${numbers.length > 1 ? 's' : ''})`);
      summary.written++;
    }
    // The release's "how to add games" placeholder (package_release.py).
    if (summary.written) await removeFile(card, [...out, '! How to add games (see README).json']);
    for (const name of [...discs.keys()].sort()) {
      const seeded = await seedSharedCard(card, platform, core, discs.get(name), name);
      if (seeded) say(`shared card ${name} starts from ${seeded}`);
    }
    summary.shared = discs.size;
    say(`${summary.written} instance files in ${out.join('/')}, ${summary.skipped} skipped`
        + (discs.size ? `, ${discs.size} multi-disc games share a card` : ''));
    return summary;
  }

  // Cores before 0.5.3 (neither a cue reader nor one-file) need merged
  // images split into one .bin per track; the page only offers it to them.
  async function needsSplit(card, core) {
    let declared = null;
    try { declared = JSON.parse(await (await fileAt(card, ['Cores', core, 'data.json'])).text()).data.data_slots; } catch (e) { declared = null; }
    if (!Array.isArray(declared)) return true;
    const takes = (id, ext) => declared.some((s) => s && s.id === id && (s.extensions || []).includes(ext));
    return !takes(0, 'cue') && !takes(102, 'trk');
  }

  Object.assign(exports, { makeInstances, findCores, needsSplit, parseCue, sharedCardName, jsonDumps, SECTOR });
})(typeof module === 'object' && module.exports ? module.exports : (globalThis.PocketInstances = {}));
