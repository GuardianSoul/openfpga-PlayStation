# Game list votes

The vote service behind the game list page: a Cloudflare Worker with a D1
database, both on Cloudflare's free plan. Voting needs no account.

```
npx wrangler d1 create playstation-game-list      # put the id in wrangler.toml
npx wrangler d1 execute playstation-game-list --remote --file schema.sql
npx wrangler secret put SALT                      # any long random text
npx wrangler deploy
```

Then set `VOTES` in `docs/compat.js` to the worker's address.
