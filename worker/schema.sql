-- One row per voter (a salted hash of the network address), game and core version.
CREATE TABLE IF NOT EXISTS votes (
  voter TEXT NOT NULL,
  game TEXT NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL,
  at INTEGER NOT NULL,
  PRIMARY KEY (voter, game, version)
);
CREATE INDEX IF NOT EXISTS votes_by_time ON votes (voter, at);
