// SPDX-License-Identifier: MIT
// Game files for the PlayStation core on an Analogue Pocket SD card.
//
// A browser port of scripts/make_pocket_instances.py (keep the two in step;
// tests/test_sd_card_page.py runs both on the same card and compares). It
// works on File System Access API directory handles, so the page can write
// straight to the SD card in Chrome or Edge. CHD images still need the
// script, because they have to be decompressed with chdman.
(function (exports) {
  'use strict';

  const SECTOR = 2352;
  const PREGAP = 150;
  const LIBCRYPT_SLOT = 99;
  const CARD_SLOT = 16;
  const DISC = / \(Disc \d+\)/;
  const DISC_N = /\s*\(Disc (\d+)\)/i;
  const DISC_N_ALL = /\s*\(Disc (\d+)\)/gi;
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
      await writeFile(card, [...dest, track], [new Uint8Array(zeros * SECTOR), source.slice(start * SECTOR, end * SECTOR)]);
      lines.push(`FILE "${track}" BINARY`, `  TRACK ${two} ${kind}`);
      lines.push(...(number !== plan[0].number ? ['    INDEX 00 00:00:00', '    INDEX 01 00:02:00'] : ['    INDEX 01 00:00:00']));
    }
    const out = [...dest, base + '.cue'];
    await writeFile(card, out, lines.join('\n') + '\n');
    return out;
  }

  // Split merged single-.bin cues under common into <name>/ folders next to
  // them; the originals stay. CHD images are reported, not converted.
  async function convert(card, common, log) {
    let done = 0, failed = 0;
    const images = [...await walk(await dirAt(card, common), (n) => /\.chd$/i.test(n)),
                    ...await walk(await dirAt(card, common), (n) => /\.cue$/i.test(n))];
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
  // platform: 'playstation', convert: bool}. log(line) gets the report.
  async function makeInstances(card, options, log = () => {}) {
    const { core, platform = 'playstation' } = options;
    let declared = null;
    try { declared = JSON.parse(await (await fileAt(card, ['Cores', core, 'data.json'])).text()).data.data_slots; } catch (e) { declared = null; }
    if (!Array.isArray(declared)) declared = null;
    const ids = (declared || []).map((s) => s && s.id).filter(Number.isInteger);
    const maxTracks = declared === null ? 8 : ids.filter((id) => id >= 101 && id <= 199).length;
    const sharedCards = declared === null || ids.includes(CARD_SLOT);
    const libcrypt = declared !== null && ids.includes(LIBCRYPT_SLOT);
    const common = ['Assets', platform, 'common'];
    const out = ['Assets', platform, core];
    const commonDir = await dirAt(card, common);
    if (!commonDir) throw new Error(`no ${common.join('/')} folder on this card`);
    const summary = { written: 0, skipped: 0, converted: 0, convertFailed: 0, shared: 0, lines: [] };
    const say = (line) => { summary.lines.push(line); log(line); };
    if (options.convert) {
      [summary.converted, summary.convertFailed] = await convert(card, common, say);
      say(`${summary.converted} images converted, ${summary.convertFailed} failed`);
    }
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
    for (const rel of await walk(commonDir, (n) => /\.cue$/i.test(n))) {
      const cue = [...common, ...rel];
      const folder = cue.slice(0, -1), cueName = cue[cue.length - 1], cueStem = stem(cueName);
      let files = null, reason = null;
      try { files = await readCue(card, cue); reason = await check(card, files, folder); } catch (e) { reason = e.message; }
      if (!reason && files.length > maxTracks) reason = `${files.length} track files (core takes ${maxTracks})`;
      if (reason && await fileAt(card, [...folder, cueStem, cueStem + '.cue'])) continue;   // converted copy is listed
      if (reason) { say(`skip ${rel.join('/')}: ${reason}`); summary.skipped++; continue; }
      const disc = DISC_N.exec(cueStem);
      if (disc && discsDeclared > 1) {
        const name = cueStem.replace(DISC_N_ALL, '');
        const game = name.replace(RELEASE, '').trim();
        if (!games.has(game)) games.set(game, new Map());
        const found = games.get(game), number = parseInt(disc[1], 10);
        if (!found.has(number)) found.set(number, []);
        found.get(number).push([cue, files, name.match(RELEASE) || []]);
        continue;
      }
      const dataPath = posix(folder.slice(common.length));
      const slots = [{ id: 100, filename: cueName }, ...files.map(([n], k) => ({ id: 101 + k, filename: n }))];
      const sbi = libcrypt ? await findSbi(card, cue) : null;
      if (sbi) slots.push({ id: LIBCRYPT_SLOT, filename: sbi });
      let prefix = dataPath !== '.' ? dataPath + '/' : '';
      const shared = sharedCards ? sharedCardName(cueStem) : null;
      if (shared) {
        for (const s of slots) s.filename = prefix + s.filename;
        slots.push({ id: CARD_SLOT, filename: shared });
        if (!discs.has(shared)) discs.set(shared, []);
        discs.get(shared).push(cueStem);
        prefix = '';
      }
      await writeInstance(cueStem, slots, prefix);
      say(`wrote ${cueStem}.json (${files.length} track${files.length > 1 ? 's' : ''}${sbi ? ', LibCrypt ' + sbi : ''})`);
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
      const tooLong = numbers.slice(1).filter((n) => parts.get(n)[1].length > discTracks[n]);
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
      for (const n of numbers) {
        const [cue, files] = parts.get(n);
        files.forEach(([name], k) => slots.push({ id: 100 * n + 1 + k, filename: rel(cue, name) }));
      }
      const sbi = libcrypt ? await findSbi(card, cue1) : null;
      if (sbi) slots.push({ id: LIBCRYPT_SLOT, filename: rel(cue1, sbi) });
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
      say(`wrote ${name}.json (${numbers.length} discs)`);
      summary.written++;
    }
    for (const name of [...discs.keys()].sort()) {
      const seeded = await seedSharedCard(card, platform, core, discs.get(name), name);
      if (seeded) say(`shared card ${name} starts from ${seeded}`);
    }
    summary.shared = discs.size;
    say(`${summary.written} instance files in ${out.join('/')}, ${summary.skipped} skipped`
        + (discs.size ? `, ${discs.size} multi-disc games share a card` : ''));
    return summary;
  }

  Object.assign(exports, { makeInstances, findCores, parseCue, sharedCardName, jsonDumps, SECTOR });
})(typeof module === 'object' && module.exports ? module.exports : (globalThis.PocketInstances = {}));
