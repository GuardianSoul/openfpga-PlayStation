// SPDX-License-Identifier: MIT
// The game list on compatibility.html: players' votes (worker/vote.js), the
// "Game problem" issues (.github/ISSUE_TEMPLATE/compatibility.yml) and the
// earlier tests in compatibility.json, grouped into one entry per game and region.
//
// games.tsv (redump names and serials) gives each disc serial its game, so
// reports with differently spelled names but one serial merge, and the
// discs of a multi-disc game fold into one entry. A report without a serial
// is matched to a game by its name and region. The same list drives the
// title search that fills in a new report. tests/test_compat_page.py covers
// the grouping.
(function (exports) {
  'use strict';

  const REPO = 'GuardianSoul/openfpga-PlayStation';
  const LABEL = 'compatibility';
  // Reports with these labels, or closed as not planned, are left off the list.
  const HIDDEN = new Set(['invalid', 'duplicate', 'spam']);
  // Best first.
  const STATUSES = ['Playable', 'Minor issues', 'Major issues', 'Boots', "Doesn't boot"];
  const REGIONS = ['USA', 'Europe', 'Japan', 'Other'];
  const FIELDS = { 'Game': 'game', 'Disc serial': 'serial', 'Region': 'region', 'Status': 'status',
                   'Core version': 'version', 'Notes': 'notes', 'What goes wrong': 'notes' };
  const PLACES = /\b(USA|Europe|Japan|Asia|Australia|Canada|Korea|Taiwan|China|Brazil|Russia|Scandinavia|Germany|France|Italy|Spain|Netherlands|Sweden|Portugal|Denmark|Norway|Finland|Belgium|Austria|Switzerland|Greece|Poland|UK|World)\b/;
  const ROMAN = { ii: 2, iii: 3, iv: 4, v: 5, vi: 6, vii: 7, viii: 8, ix: 9, x: 10 };

  // "### Label\n\nvalue" sections, as GitHub writes an issue form's answers.
  function parseBody(body) {
    const out = {};
    const parts = String(body || '').replace(/\r\n/g, '\n').split(/^### +(.+?) *$/m);
    for (let i = 1; i + 1 < parts.length; i += 2) {
      const key = FIELDS[parts[i].trim()];
      const value = parts[i + 1].trim();
      if (key) out[key] = value === '_No response_' ? '' : value;
    }
    return out;
  }

  function normalSerial(text) {
    const m = /\b(S[CL][A-Z]{2})[\s_.-]*(\d{3})[.]?(\d{2})\b/.exec(String(text || '').toUpperCase());
    return m ? `${m[1]}-${m[2]}${m[3]}` : '';
  }

  function regionOfSerial(serial) {
    const prefix = String(serial || '').slice(0, 4);
    if (/^S[CL]US$/.test(prefix)) return 'USA';
    if (/^S[CL]E[SD]$/.test(prefix)) return 'Europe';
    if (/^S[CL]P[SM]$/.test(prefix)) return 'Japan';
    return prefix ? 'Other' : '';
  }

  function normalStatus(text) {
    const t = String(text || '').toLowerCase().replace(/\u2019/g, "'");
    if (t.startsWith("doesn't") || t.startsWith('does not')) return "Doesn't boot";
    return STATUSES.find((s) => t.startsWith(s.toLowerCase())) || '';
  }

  function normalRegion(text) {
    const t = String(text || '').trim();
    return REGIONS.includes(t) ? t : '';
  }

  // The game's name without region, language, disc and revision tags.
  function baseName(name) {
    return String(name || '').replace(/\s*\(([^)]*)\)/g, (all, tag) =>
      PLACES.test(tag) || /^(?:disc|cd)\s*\d+$|^rev\s*\w+$|^v\d[\d.]*$|^edc$/i.test(tag.trim())
        || /^[A-Z][a-z](?:,[A-Z][a-z])*$/.test(tag.trim()) ? '' : all)
      .replace(/\s+/g, ' ').trim().replace(/^(.+), The$/, 'The $1');
  }

  // Lower-case words, with accents, punctuation and Roman numerals evened out.
  function words(name) {
    return String(name || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
      .replace(/&/g, ' and ').replace(/'/g, '').split(/[^a-z0-9]+/).filter(Boolean)
      .map((w) => (w in ROMAN ? String(ROMAN[w]) : w));
  }

  // Same key for "The Adventures of Little Ralph" and "Adventures of Little Ralph, The".
  function nameKey(name) {
    const w = words(baseName(name));
    if (w[0] === 'the') w.shift();
    else if (w[w.length - 1] === 'the') w.pop();
    return w.join('');
  }

  // Sorts "0.5.10" after "0.5.3"; no number sorts first.
  function versionKey(text) {
    const m = /\d+(?:\.\d+)*/.exec(String(text || ''));
    return m ? m[0].split('.').map(Number) : [];
  }

  function compareVersions(a, b) {
    const x = versionKey(a), y = versionKey(b);
    for (let i = 0; i < Math.max(x.length, y.length); i++) {
      const d = (x[i] || 0) - (y[i] || 0);
      if (d) return d;
    }
    return 0;
  }

  // games.tsv: "serial<TAB>redump name" lines; # starts a comment.
  function loadGames(text) {
    const bySerial = new Map(), byKey = new Map(), byName = new Map();
    for (const line of String(text || '').split('\n')) {
      if (!line || line[0] === '#') continue;
      const [serial, full] = line.split('\t');
      if (!full) continue;
      const name = baseName(full), region = regionOfSerial(serial), key = `${nameKey(name)}|${region}`;
      if (!bySerial.has(serial)) bySerial.set(serial, { name, region, key });
      if (!byKey.has(key)) byKey.set(key, { name, region, key, serials: [], discs: 1, words: words(name), dumps: [] });
      if (!byName.has(nameKey(name))) byName.set(nameKey(name), name);
      const game = byKey.get(key);
      if (!game.serials.includes(serial)) game.serials.push(serial);
      // Each release as redump names it, with the serials of its discs. A
      // single-disc release is one serial (revisions with that serial share it).
      const release = full.replace(/\s*\((?:disc|cd)\s*\d+\)/i, '');
      const multi = release !== full;
      let dump = game.dumps.find((d) => (multi ? d.multi && d.name === release : !d.multi && d.serials[0] === serial));
      if (!dump) game.dumps.push(dump = { name: release, serials: [], multi });
      if (!dump.serials.includes(serial)) dump.serials.push(serial);
      const disc = /\((?:disc|cd)\s*(\d+)\)/i.exec(full);
      if (disc) game.discs = Math.max(game.discs, Number(disc[1]));
    }
    return { bySerial, byKey, byName };
  }

  // Games whose name has every typed word (or the start of it), best match first.
  function search(games, query, region, limit = 20) {
    const typed = words(query);
    if (!typed.length || !games) return [];
    const found = [];
    for (const game of games.byKey.values()) {
      if (region && game.region !== region) continue;
      if (!typed.every((t) => game.words.some((w) => w.startsWith(t)))) continue;
      const score = (game.words[0].startsWith(typed[0]) ? 0 : 2)
        + (typed.every((t, i) => game.words[i] === t) ? 0 : 1)
        + (/\b(demo|sampler|taikenban|preview|beta|proto)/i.test(game.name) ? 4 : 0);
      found.push({ game, score });
    }
    return found.sort((a, b) => a.score - b.score || a.game.name.length - b.game.name.length
      || a.game.name.localeCompare(b.game.name)).slice(0, limit).map((f) => f.game);
  }

  // The listed game a report belongs to, if the list knows it.
  function lookup(games, report) {
    if (!games) return null;
    if (report.serial && games.bySerial.has(report.serial)) return games.byKey.get(games.bySerial.get(report.serial).key);
    if (report.serial) {
      // A later disc that the list files under disc 1's serial: USA and Japan
      // discs count up (SCUS-94163, -94164), European ones by ten thousand.
      const [prefix, number] = report.serial.split('-');
      for (let n = 1; n < 6; n++) {
        for (const step of [1, 10000]) {
          const game = games.bySerial.get(`${prefix}-${String(Number(number) - n * step).padStart(5, '0')}`);
          if (game && games.byKey.get(game.key).discs > n) return games.byKey.get(game.key);
        }
      }
    }
    const key = nameKey(report.game);
    if (!key || !report.region) return null;
    const exact = games.byKey.get(`${key}|${report.region}`);
    if (exact) return exact;
    // A shortened name ("Crash Bandicoot 2") is used only when it fits one game.
    const starts = [...games.byKey.values()].filter((g) => g.region === report.region && g.key.startsWith(key)
      && !/\b(demo|sampler|taikenban|preview|beta|proto)/i.test(g.name));
    return starts.length === 1 ? starts[0] : null;
  }

  function fromIssue(issue) {
    if (issue.pull_request) return null;
    if (issue.state_reason === 'not_planned') return null;
    const labels = (issue.labels || []).map((l) => String(typeof l === 'string' ? l : l.name).toLowerCase());
    if (labels.some((l) => HIDDEN.has(l))) return null;
    // Players vote on reports with 👍 and 👎; one that more people reject than back is left off.
    const up = Number(issue.reactions?.['+1']) || 0, down = Number(issue.reactions?.['-1']) || 0;
    if (down >= 2 && down > up) return null;
    const f = parseBody(issue.body);
    const titleGame = String(issue.title || '').replace(/^\s*\[(?:compatibility|problem)\]\s*/i, '').trim();
    const serial = normalSerial(f.serial) || normalSerial(f.game) || normalSerial(titleGame);
    const report = {
      game: baseName(f.game || titleGame), serial,
      region: regionOfSerial(serial) || normalRegion(f.region),
      status: normalStatus(f.status), version: (f.version || '').trim().slice(0, 20), notes: (f.notes || '').slice(0, 2000),
      date: (issue.created_at || '').slice(0, 10), number: Number(issue.number) || 0,
      url: /^https:\/\/github\.com\//.test(issue.html_url || '') ? issue.html_url : '',
      author: String(issue.user?.login || '').toLowerCase(), votes: 1 + Math.max(0, up - down),
    };
    report.game = report.game.slice(0, 120);
    return report.game || report.serial ? report : null;
  }

  function fromSeed(entry) {
    const serial = normalSerial(entry.serial);
    return {
      game: baseName(entry.game), serial, region: regionOfSerial(serial) || normalRegion(entry.region),
      status: normalStatus(entry.status), version: entry.version || '', notes: entry.notes || '',
      rank: entry.version || '', date: entry.date || '', earlier: 'earlier test',
    };
  }

  function group(reports, games) {
    const parent = new Map();
    const find = (k) => { while (parent.get(k) !== k) { parent.set(k, parent.get(parent.get(k))); k = parent.get(k); } return k; };
    const add = (k) => { if (!parent.has(k)) parent.set(k, k); return k; };
    const union = (a, b) => { const x = find(add(a)), y = find(add(b)); if (x !== y) parent.set(y, x); };
    const known = new Map();
    for (const r of reports) {
      const game = lookup(games, r);
      if (game) { known.set(r, game); if (!r.region) r.region = game.region; }
    }
    const keysOf = (r) => {
      const keys = [];
      if (known.has(r)) keys.push('g:' + known.get(r).key);
      if (r.serial) keys.push('s:' + r.serial);
      if (nameKey(r.game)) keys.push(`n:${nameKey(r.game)}|${r.region}`);
      return keys;
    };
    for (const r of reports) {
      const keys = keysOf(r);
      add(keys[0]);
      for (const k of keys.slice(1)) union(keys[0], k);
    }
    // A report without a region joins the game's only regional entry.
    const regions = new Map();
    for (const r of reports) {
      const n = nameKey(known.get(r)?.name || r.game);
      if (!r.region || !n) continue;
      if (!regions.has(n)) regions.set(n, new Set());
      regions.get(n).add(find(keysOf(r)[0]));
    }
    for (const r of reports) {
      const n = nameKey(r.game);
      if (r.region || !n || !regions.has(n)) continue;
      const roots = new Set([...regions.get(n)].map(find));
      if (roots.size === 1) union([...roots][0], keysOf(r)[0]);
    }

    const groups = new Map();
    for (const r of reports) {
      const root = find(keysOf(r)[0]);
      if (!groups.has(root)) groups.set(root, []);
      groups.get(root).push(r);
    }
    return [...groups.values()].map((list) => entryOf(list, known, games));
  }

  function entryOf(reports, known, games) {
    reports = reports.slice().sort((a, b) => compareVersions(b.rank, a.rank) || b.date.localeCompare(a.date)
      || (b.number || 0) - (a.number || 0));
    // One report per GitHub account and game: its newest.
    const authors = new Set();
    reports = reports.filter((r) => !r.author || (!authors.has(r.author) && authors.add(r.author)));
    const game = reports.map((r) => known.get(r)).find(Boolean);
    const names = new Map();
    for (const r of reports) if (r.game) names.set(r.game, (names.get(r.game) || 0) + 1);
    // The listed name, else the most used one; ties go to a capitalised one, then the newest report.
    const capitalised = (n) => (n !== n.toLowerCase() ? 1 : 0);
    const used = [...names.entries()].sort((a, b) => b[1] - a[1] || capitalised(b[0]) - capitalised(a[0]))[0]?.[0]
      || reports[0].serial;
    const name = game?.name || games?.byName.get(nameKey(used)) || used;
    const region = reports.find((r) => r.region)?.region || '';
    const serials = [...new Set([...reports.map((r) => r.serial), ...(game?.serials || [])].filter(Boolean))].sort();
    const key = game?.key || (region && nameKey(name) ? `${nameKey(name)}|${region}` : '');
    return { key, game, name, region, serials, reports, sortName: name.replace(/^the\s+/i, '').toLowerCase() };
  }

  // An entry's status: the most votes on the newest released version anyone
  // voted or reported on. Each vote counts once, each report once plus its
  // net 👍; a tie goes to the worse status. votes: { "<key>|<version>": { status: n } }.
  function decide(entry, votes, released) {
    const known = released ? new Set(released) : null;
    const versions = Object.keys(votes || {}).filter((k) => entry.key && k.startsWith(entry.key + '|'))
      .map((k) => k.slice(entry.key.length + 1)).filter((v) => !known || known.has(v));
    const rated = entry.reports.filter((r) => r.status);
    const all = [...versions, ...rated.map((r) => r.rank)];
    const newest = all.sort((a, b) => compareVersions(b, a))[0] ?? '';
    const tally = {};
    for (const [status, n] of Object.entries((votes || {})[`${entry.key}|${newest}`] || {})) {
      if (STATUSES.includes(status)) tally[status] = (tally[status] || 0) + (Number(n) || 0);
    }
    for (const r of rated) if (!compareVersions(r.rank, newest)) tally[r.status] = (tally[r.status] || 0) + (r.votes || 1);
    const status = Object.entries(tally).filter(([, n]) => n > 0)
      .sort((a, b) => b[1] - a[1] || STATUSES.indexOf(b[0]) - STATUSES.indexOf(a[0]))[0]?.[0] || '';
    return Object.assign(entry, { status, version: all.length ? newest : '', tally });
  }

  // All report issues, a page of 100 at a time.
  async function fetchIssues(fetchImpl = fetch, repo = REPO) {
    const issues = [];
    for (let page = 1; page <= 20; page++) {
      const url = `https://api.github.com/repos/${repo}/issues?labels=${LABEL}&state=all&per_page=100&page=${page}`;
      const response = await fetchImpl(url, { headers: { Accept: 'application/vnd.github+json' } });
      if (!response.ok) {
        const limited = response.status === 403 || response.status === 429;
        throw new Error(limited ? 'GitHub is limiting requests from this network for now' : `GitHub answered ${response.status}`);
      }
      const batch = await response.json();
      issues.push(...batch);
      if (batch.length < 100) break;
    }
    return issues;
  }

  // Released core versions, newest first ("0.5.3"), from the repo's releases.
  async function fetchVersions(fetchImpl = fetch, repo = REPO) {
    const response = await fetchImpl(`https://api.github.com/repos/${repo}/releases?per_page=30`,
                                     { headers: { Accept: 'application/vnd.github+json' } });
    if (!response.ok) throw new Error(`GitHub answered ${response.status}`);
    return (await response.json()).filter((r) => !r.draft && !r.prerelease)
      .map((r) => String(r.tag_name).replace(/^v/i, '')).sort((a, b) => compareVersions(b, a));
  }

  // released: the published core versions. A report naming any other version
  // is listed but ranks below them, so a made-up "9.9" can't set a status.
  // votes: the vote service's counts; a game nobody reported yet gets an entry
  // from its votes, if the game list knows it.
  function build(issues, seeds, games, released, votes) {
    const known = released ? new Set(released) : null;
    const reports = [...(seeds || []).map(fromSeed), ...(issues || []).map(fromIssue).filter(Boolean).map((r) =>
      ({ ...r, rank: !known || known.has(r.version.replace(/^v/i, '')) ? r.version : '' }))];
    let entries = group(reports, games);
    const keys = new Set(entries.map((e) => e.key));
    for (const k of Object.keys(votes || {})) {
      const key = k.split('|').slice(0, 2).join('|'), game = games?.byKey.get(key);
      if (!game || keys.has(key)) continue;
      keys.add(key);
      entries.push(entryFor(game));
    }
    // An entry without a region gives way to the same game's regional entries:
    // its reports move into each of them as older reports.
    const regional = new Map();
    for (const e of entries) if (e.region) {
      const n = nameKey(e.name);
      if (!regional.has(n)) regional.set(n, []);
      regional.get(n).push(e);
    }
    entries = entries.filter((e) => {
      const targets = !e.region && regional.get(nameKey(e.name));
      if (!targets) return true;
      for (const t of targets) t.reports = [...t.reports, ...e.reports].sort((a, b) => compareVersions(b.rank, a.rank));
      return false;
    });
    return entries.map((e) => decide(e, votes, released))
      .sort((a, b) => a.sortName.localeCompare(b.sortName) || a.region.localeCompare(b.region));
  }

  // An empty entry for a listed game, to vote on.
  function entryFor(game) {
    return { key: game.key, game, name: game.name, region: game.region, serials: game.serials.slice().sort(),
             reports: [], sortName: game.name.replace(/^the\s+/i, '').toLowerCase() };
  }

  // The vote service (worker/vote.js); empty until it is set up, which hides voting.
  const VOTES = 'https://playstation-game-list.guardiansoul.workers.dev';

  async function fetchVotes(fetchImpl = fetch, base = VOTES) {
    const response = await fetchImpl(`${base}/votes`);
    if (!response.ok) throw new Error(`the vote service answered ${response.status}`);
    return response.json();
  }

  async function fetchMine(fetchImpl = fetch, base = VOTES) {
    const response = await fetchImpl(`${base}/mine`, { cache: 'no-store' });
    return response.ok ? response.json() : {};
  }

  // status "" withdraws the vote.
  async function sendVote(game, version, status, fetchImpl = fetch, base = VOTES) {
    const response = await fetchImpl(`${base}/vote`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ game, version, status }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || `the vote service answered ${response.status}`);
  }

  // The GitHub form, filled in with what is known about the game.
  // dump: the release picked ({ name, serials }); its first disc's serial goes in.
  function reportUrl(game, version, dump, repo = REPO) {
    const q = new URLSearchParams({ template: 'compatibility.yml' });
    if (version) q.set('version', version);
    if (game) {
      q.set('title', `[Problem] ${game.name}${game.region ? ` (${game.region})` : ''}`);
      q.set('game', game.name);
      if (dump) q.set('serial', dump.serials[0]);
    }
    return `https://github.com/${repo}/issues/new?${q}`;
  }

  Object.assign(exports, { REPO, STATUSES, REGIONS, parseBody, normalSerial, regionOfSerial, normalStatus,
                           baseName, nameKey, compareVersions, loadGames, search, lookup, fromIssue, fromSeed,
                           group, build, decide, entryFor, fetchIssues, fetchVersions, fetchVotes, fetchMine, sendVote, reportUrl, VOTES });
})(typeof module === 'object' && module.exports ? module.exports : (globalThis.Compat = {}));
