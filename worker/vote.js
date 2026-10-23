// SPDX-License-Identifier: MIT
// Vote service for the game list (docs/compatibility.html): a Cloudflare
// Worker with a D1 database (schema.sql). Anyone can vote how a game runs on a
// released core version, without an account. Each network address has one
// vote per game and version (a newer vote replaces it) and a few dozen votes
// an hour. Addresses are kept only as a salted hash.
//
//   GET  /versions -> { "versions": ["0.5.6", ...] }   (released, newest first)
//   GET  /votes -> { "<game>|<version>": { "<status>": count } }
//   GET  /mine  -> { "<game>|<version>": "<status>" }   (this address's votes)
//   POST /vote  <- { game, version, status }             (status "" withdraws it)
//
// Settings: secret SALT (any long random text).

const ORIGIN = 'https://guardiansoul.github.io';
const REPO = 'GuardianSoul/openfpga-PlayStation';
const STATUSES = ['Playable', 'Minor issues', 'Major issues', 'Boots', "Doesn't boot"];
const HOURLY_LIMIT = 30;   // votes per address per hour
const GAME = /^[a-z0-9]{1,100}\|(USA|Europe|Japan|Other)$/;

const cors = { 'Access-Control-Allow-Origin': ORIGIN, 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
               'Access-Control-Allow-Headers': 'Content-Type' };
const answer = (body, status = 200, extra = {}) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, 'Content-Type': 'application/json', ...extra } });

// One voter per network address, kept only as a salted hash.
async function voterOf(request, env) {
  const address = request.headers.get('CF-Connecting-IP') || '';
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`${env.SALT || ''}|${address}`));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

// Published core versions, from the releases feed (GitHub's API often turns
// away Cloudflare's shared addresses), checked at most every ten minutes, or
// every half minute while someone votes on a version the list doesn't have
// yet, so a new release takes votes right away.
// If GitHub can't be reached, any plain version number is taken, so votes
// don't stop; the page itself only counts released versions.
let released = { at: 0, versions: null };
const VERSION = /^\d+\.\d+(\.\d+)?$/;
async function releasedVersions(fetchImpl, wanted = '') {
  const age = Date.now() - released.at;
  const missing = released.versions && VERSION.test(wanted) && !released.versions.has(wanted);
  if (age < (missing ? 30000 : released.versions ? 600000 : 60000)) return released.versions;
  let versions = null;
  try {
    const response = await fetchImpl(`https://github.com/${REPO}/releases.atom`, { headers: { 'User-Agent': 'playstation-game-list' } });
    if (response.ok) {
      const tags = [...(await response.text()).matchAll(/\/releases\/tag\/v?([0-9][0-9.]*)"/g)].map((m) => m[1]);
      if (tags.length) versions = new Set(tags);
    }
  } catch (e) { /* keep the last list */ }
  released = { at: Date.now(), versions: versions || released.versions };
  return released.versions;
}

async function isReleased(version, fetchImpl) {
  const versions = await releasedVersions(fetchImpl, version);
  return versions ? versions.has(version) : VERSION.test(version);
}

// The page takes its version list from here too, so it always offers the
// version votes are checked against.
const newestFirst = (a, b) => {
  const x = a.split('.').map(Number), y = b.split('.').map(Number);
  for (let i = 0; i < Math.max(x.length, y.length); i++) if ((x[i] || 0) !== (y[i] || 0)) return (y[i] || 0) - (x[i] || 0);
  return 0;
};
async function versionList(fetchImpl) {
  return { versions: [...((await releasedVersions(fetchImpl)) || [])].filter((v) => VERSION.test(v)).sort(newestFirst) };
}

async function tallies(env) {
  const { results } = await env.DB.prepare(
    'SELECT game, version, status, COUNT(*) AS n FROM votes GROUP BY game, version, status').all();
  const out = {};
  for (const row of results) (out[`${row.game}|${row.version}`] ||= {})[row.status] = row.n;
  return out;
}

async function mine(voter, env) {
  const { results } = await env.DB.prepare('SELECT game, version, status FROM votes WHERE voter = ?').bind(voter).all();
  return Object.fromEntries(results.map((row) => [`${row.game}|${row.version}`, row.status]));
}

async function vote(request, voter, env, fetchImpl) {
  let body;
  try { body = await request.json(); } catch (e) { return answer({ error: 'bad request' }, 400); }
  const game = String(body.game || ''), version = String(body.version || ''), status = String(body.status ?? '');
  if (!GAME.test(game)) return answer({ error: 'unknown game' }, 400);
  if (!(await isReleased(version, fetchImpl))) return answer({ error: `core ${version} isn't a released version` }, 400);
  if (status && !STATUSES.includes(status)) return answer({ error: 'unknown status' }, 400);
  const now = Date.now();
  const recent = await env.DB.prepare('SELECT COUNT(*) AS n FROM votes WHERE voter = ? AND at > ?')
    .bind(voter, now - 3600000).first();
  if (recent && recent.n >= HOURLY_LIMIT) return answer({ error: 'Too many votes in an hour; try again later.' }, 429);
  if (status) {
    await env.DB.prepare('INSERT INTO votes (voter, game, version, status, at) VALUES (?, ?, ?, ?, ?) ' +
      'ON CONFLICT (voter, game, version) DO UPDATE SET status = excluded.status, at = excluded.at')
      .bind(voter, game, version, status, now).run();
  } else {
    await env.DB.prepare('DELETE FROM votes WHERE voter = ? AND game = ? AND version = ?').bind(voter, game, version).run();
  }
  return answer({ ok: true });
}

export default {
  async fetch(request, env, ctx, fetchImpl = fetch) {
    const url = new URL(request.url);
    if (request.method === 'OPTIONS') return new Response(null, { headers: cors });
    if (request.method === 'GET' && url.pathname === '/votes') {
      // Counted at most every 30 seconds, to stay well inside the free plan's database reads.
      const cache = globalThis.caches?.default, key = new Request(url.toString());
      const hit = cache && await cache.match(key);
      if (hit) return hit;
      const fresh = answer(await tallies(env), 200, { 'Cache-Control': 'public, max-age=30' });
      if (cache) ctx.waitUntil(cache.put(key, fresh.clone()));
      return fresh;
    }
    if (request.method === 'GET' && url.pathname === '/versions') {
      return answer(await versionList(fetchImpl), 200, { 'Cache-Control': 'public, max-age=60' });
    }
    if (request.method === 'GET' && url.pathname === '/mine') {
      return answer(await mine(await voterOf(request, env), env), 200, { 'Cache-Control': 'no-store' });
    }
    if (request.method === 'POST' && url.pathname === '/vote') {
      const response = await vote(request, await voterOf(request, env), env, fetchImpl);
      // A new vote shows at once: drop the saved counts so the next load counts again.
      const cache = globalThis.caches?.default;
      if (cache && response.ok) await cache.delete(new Request(new URL('/votes', url).toString()));
      return response;
    }
    return answer({ error: 'not found' }, 404);
  },
};
