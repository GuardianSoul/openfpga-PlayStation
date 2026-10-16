// SPDX-License-Identifier: MIT
// Runs the SD card page's logic (docs/instances.js) on a folder, through
// a small stand-in for the browser's File System Access API.
//   node tests/sd_card_page.cjs <card> --core GuardianSoul.PlayStation [--convert] [--remove-originals]
'use strict';
const fs = require('fs');
const path = require('path');
// docs/ in the public tree, release/docs/ in the workspace.
const { makeInstances } = require(['../docs/instances.js', '../release/docs/instances.js']
  .map((p) => path.join(__dirname, p)).find((p) => fs.existsSync(p)));

async function asFile(full) {
  const blob = await fs.openAsBlob(full);
  return Object.assign(blob, { lastModified: fs.statSync(full).mtimeMs, name: path.basename(full) });
}

class FileHandle {
  constructor(full) { this.full = full; this.kind = 'file'; }
  async getFile() { return asFile(this.full); }
  async createWritable() {
    const parts = [];
    const full = this.full;
    return {
      async write(data) {
        if (typeof data === 'string') parts.push(Buffer.from(data, 'utf8'));
        else if (data instanceof Uint8Array) parts.push(Buffer.from(data));
        else parts.push(Buffer.from(await data.arrayBuffer()));
      },
      async close() { fs.writeFileSync(full, Buffer.concat(parts)); },
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
  const summary = await makeInstances(new DirectoryHandle(path.resolve(card)), {
    core: option('--core') || 'GuardianSoul.PlayStation',
    platform: option('--platform') || 'playstation',
    convert: args.includes('--convert'),
    removeOriginals: args.includes('--remove-originals'),
  }, (line) => console.log(line));
  return summary;
}

main().catch((error) => { console.error(error.stack || String(error)); process.exit(1); });
