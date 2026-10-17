// SPDX-License-Identifier: MIT
// Runs the game list page's grouping (docs/compat.js) on report issues and
// prints the entries as JSON.
//   node tests/compat_page.cjs <issues.json> <seeds.json> [--released=<versions.json>] [--votes=<votes.json>] [games.tsv]
'use strict';
const fs = require('fs');
const path = require('path');
// docs/ in the public tree, release/docs/ in the workspace.
const Compat = require(['../docs/compat.js', '../release/docs/compat.js']
  .map((p) => path.join(__dirname, p)).find((p) => fs.existsSync(p)));
const option = (name) => {
  const flag = process.argv.find((a) => a.startsWith(`--${name}=`));
  return flag ? JSON.parse(fs.readFileSync(flag.slice(name.length + 3), 'utf8')) : undefined;
};
const released = option('released'), votes = option('votes');
const [issues, seeds, tsv] = process.argv.slice(2).filter((a) => !a.startsWith('--'));
const games = tsv ? Compat.loadGames(fs.readFileSync(tsv, 'utf8')) : null;
const entries = Compat.build(JSON.parse(fs.readFileSync(issues, 'utf8')), JSON.parse(fs.readFileSync(seeds, 'utf8')), games, released, votes);
const search = games ? Compat.search(games, 'final fant 7', 'USA', 3).map((g) => g.name) : [];
console.log(JSON.stringify({ entries: entries.map(({ name, region, serials, status, version, tally, reports }) =>
  ({ name, region, serials, status, version, tally, numbers: reports.map((r) => r.number || null) })), search }));
