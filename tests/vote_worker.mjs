// SPDX-License-Identifier: MIT
// Runs the game list's vote service (worker/vote.js) against an in-memory
// SQLite stand-in for Cloudflare D1 and prints what each step answered.
//   node tests/vote_worker.mjs
import { DatabaseSync } from 'node:sqlite';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
// worker/ in the public tree, release/worker/ in the workspace.
const dir = ['../worker', '../release/worker'].map((p) => path.join(here, p)).find((p) => fs.existsSync(path.join(p, 'vote.js')));
const worker = (await import(pathToFileURL(path.join(dir, 'vote.js')))).default;

const sqlite = new DatabaseSync(':memory:');
sqlite.exec(fs.readFileSync(path.join(dir, 'schema.sql'), 'utf8'));
const statement = (sql, args = []) => ({
  bind: (...values) => statement(sql, values),
  all: async () => ({ results: sqlite.prepare(sql).all(...args) }),
  first: async () => sqlite.prepare(sql).get(...args) ?? null,
  run: async () => sqlite.prepare(sql).run(...args),
});
const env = { DB: { prepare: (sql) => statement(sql) }, SALT: 'test' };
const ctx = { waitUntil() {} };
// GitHub's releases feed, as the worker reads it.
const tags = ['v0.5.3', 'v0.5.2'];
const feed = () => tags.map((t) => `<link rel="alternate" type="text/html" href="https://github.com/GuardianSoul/openfpga-PlayStation/releases/tag/${t}"/>`).join('\n');
let githubDown = false;
const releases = async () => (githubDown ? new Response('', { status: 503 }) : new Response(`<feed>${feed()}</feed>`));

async function call(method, route, ip, body) {
  const request = new Request(`https://votes.example${route}`, {
    method, headers: { 'CF-Connecting-IP': ip || '', 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const response = await worker.fetch(request, env, ctx, releases);
  return { status: response.status, cors: response.headers.get('Access-Control-Allow-Origin'),
           body: method === 'OPTIONS' ? null : await response.json() };
}

const tekken = 'tekken3|USA';
const out = {};
out.first = await call('POST', '/vote', '1.1.1.1', { game: tekken, version: '0.5.3', status: 'Playable' });
await call('POST', '/vote', '1.1.1.1', { game: tekken, version: '0.5.3', status: 'Minor issues' });   // replaces
await call('POST', '/vote', '2.2.2.2', { game: tekken, version: '0.5.3', status: 'Playable' });
await call('POST', '/vote', '3.3.3.3', { game: tekken, version: '0.5.2', status: 'Boots' });
await call('POST', '/vote', '3.3.3.3', { game: tekken, version: '0.5.2', status: '' });              // withdraws
out.badVersion = await call('POST', '/vote', '4.4.4.4', { game: tekken, version: '9.9', status: 'Playable' });
out.unreleased = await call('POST', '/vote', '4.4.4.4', { game: tekken, version: '0.6.0', status: 'Playable' });
// A new release takes votes once the feed lists it, without waiting ten minutes.
tags.unshift('v0.6.0');
const realNow = Date.now;
Date.now = () => realNow() + 60000;
out.newRelease = await call('POST', '/vote', '4.4.4.4', { game: tekken, version: '0.6.0', status: 'Playable' });
await call('POST', '/vote', '4.4.4.4', { game: tekken, version: '0.6.0', status: '' });
Date.now = realNow;
out.badGame = await call('POST', '/vote', '4.4.4.4', { game: '<b>x</b>|USA', version: '0.5.3', status: 'Playable' });
out.badStatus = await call('POST', '/vote', '4.4.4.4', { game: tekken, version: '0.5.3', status: 'Great' });
out.votes = await call('GET', '/votes', '9.9.9.9');
out.mine = await call('GET', '/mine', '1.1.1.1');
let limited = null;
for (let i = 0; i < 40 && limited === null; i++) {
  const r = await call('POST', '/vote', '5.5.5.5', { game: `game${i}|Japan`, version: '0.5.3', status: 'Playable' });
  if (r.status === 429) limited = i;
}
out.limitedAt = limited;
out.missing = await call('GET', '/nothing', '1.1.1.1');
// A worker that has never reached GitHub still takes plain version numbers.
const fresh = (await import(pathToFileURL(path.join(dir, 'vote.js')) + '?fresh')).default;
githubDown = true;
const down = (version) => fresh.fetch(new Request('https://votes.example/vote', { method: 'POST',
  headers: { 'CF-Connecting-IP': '6.6.6.6' }, body: JSON.stringify({ game: tekken, version, status: 'Playable' }) }), env, ctx, releases);
out.downPlain = (await down('0.5.3')).status;
out.downJunk = (await down('latest')).status;
out.voters = sqlite.prepare('SELECT DISTINCT voter FROM votes').all().map((r) => r.voter);
console.log(JSON.stringify(out));
