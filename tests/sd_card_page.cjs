// SPDX-License-Identifier: MIT
// Runs the SD card page's logic (docs/instances.js) on a folder, through
// a small stand-in for the browser's File System Access API.
//   node tests/sd_card_page.cjs <card> --core GuardianSoul.PlayStation [--convert] [--remove-originals] [--card-bytes N]
//   node tests/sd_card_page.cjs <card> --checks   (findCores, gamesElsewhere and checkBios, as the page runs them)
'use strict';
const fs = require('fs');
const path = require('path');
// docs/ in the public tree, release/docs/ in the workspace.
const { makeInstances, findCores, gamesElsewhere, checkBios } = require(['../docs/instances.js', '../release/docs/instances.js']
  .map((p) => path.join(__dirname, p)).find((p) => fs.existsSync(p)));

async function asFile(full) {
  const blob = await fs.openAsBlob(full);
  return Object.assign(blob, { lastModified: fs.statSync(full).mtimeMs, name: path.basename(full) });
}

// --card-bytes N: the card holds N bytes in all, so writes past that fail as
// a full card does in the browser.
let cardBytes = Infinity, cardRoot = null;
const used = (dir) => fs.readdirSync(dir, { withFileTypes: true }).reduce((n, e) => {
  const full = path.join(dir, e.name);
  return n + (e.isDirectory() ? used(full) : fs.statSync(full).size);
}, 0);
const quota = () => Object.assign(new Error('not enough space'), { name: 'QuotaExceededError' });

class FileHandle {
  constructor(full) { this.full = full; this.kind = 'file'; }
  async getFile() { return asFile(this.full); }
  async createWritable() {
    // Like Chrome: writes go to a swap copy that replaces the file on close.
    const full = this.full;
    let data = Buffer.alloc(0), at = 0;
    const room = (size) => {
      const other = cardRoot ? used(cardRoot) : 0;
      if (other + size + data.length > cardBytes) throw quota();   // the swap copy and the file both count
    };
    const resize = (size) => {
      if (size > data.length) { room(size - data.length); data = Buffer.concat([data, Buffer.alloc(size - data.length)]); }
      else data = data.subarray(0, size);
      at = Math.min(at, size);
    };
    return {
      async write(chunk) {
        let bytes;
        if (typeof chunk === 'string') bytes = Buffer.from(chunk, 'utf8');
        else if (chunk instanceof Uint8Array) bytes = Buffer.from(chunk);
        else bytes = Buffer.from(await chunk.arrayBuffer());
        if (at + bytes.length > data.length) resize(at + bytes.length);
        bytes.copy(data, at); at += bytes.length;
      },
      async truncate(size) { resize(size); },
      async seek(position) { at = position; },
      async abort() { data = null; },
      async close() { fs.writeFileSync(full, data); },
    };
  }
}

const notFound = (what) => Object.assign(new Error(`${what} not found`), { name: 'NotFoundError' });

class DirectoryHandle {
  constructor(full) { this.full = full; this.kind = 'directory'; }
  async *entries() {
    for (const entry of fs.readdirSync(this.full, { withFileTypes: true })) {
      const full = path.join(this.full, entry.name);
      yield [entry.name, entry.isDirectory() ? new DirectoryHandle(full) : new FileHandle(full)];
    }
  }
  async getDirectoryHandle(name, { create = false } = {}) {
    const full = path.join(this.full, name);
    if (!fs.existsSync(full)) { if (!create) throw notFound(name); fs.mkdirSync(full); }
    if (!fs.statSync(full).isDirectory()) throw Object.assign(new Error(name), { name: 'TypeMismatchError' });
    return new DirectoryHandle(full);
  }
  async getFileHandle(name, { create = false } = {}) {
    const full = path.join(this.full, name);
    if (!fs.existsSync(full)) { if (!create) throw notFound(name); fs.writeFileSync(full, ''); }
    if (!fs.statSync(full).isFile()) throw Object.assign(new Error(name), { name: 'TypeMismatchError' });
    return new FileHandle(full);
  }
  async removeEntry(name, { recursive = false } = {}) {
    const full = path.join(this.full, name);
    if (!fs.existsSync(full)) throw notFound(name);
    fs.rmSync(full, { recursive });
  }
}

async function main() {
  const args = process.argv.slice(2);
  const card = args[0];
  const option = (flag) => { const i = args.indexOf(flag); return i >= 0 ? args[i + 1] : undefined; };
  if (args.includes('--checks')) {
    const root = new DirectoryHandle(path.resolve(card));
    for (const core of await findCores(root)) {
      console.log(`core ${core.id} ${core.platform} ${core.supported ? 'supported' : 'other'}`);
      if (!core.supported) continue;
      console.log(`elsewhere ${await gamesElsewhere(root, core.platform)}`);
      for (const line of await checkBios(root, core.id, core.platform)) console.log(line);
    }
    return null;
  }
  if (option('--card-bytes')) { cardBytes = Number(option('--card-bytes')); cardRoot = path.resolve(card); }
  const summary = await makeInstances(new DirectoryHandle(path.resolve(card)), {
    core: option('--core') || 'GuardianSoul.PlayStation',
    platform: option('--platform') || 'playstation',
    convert: args.includes('--convert'),
    removeOriginals: args.includes('--remove-originals'),
  }, (line) => console.log(line));
  return summary;
}

main().catch((error) => { console.error(error.stack || String(error)); process.exit(1); });
