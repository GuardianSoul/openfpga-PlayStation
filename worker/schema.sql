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

-- What a voter adds to their vote: how they started the game, where they
-- played it (handheld or Dock) and, if it has problems, what goes wrong.
-- Removed with the vote.
CREATE TABLE IF NOT EXISTS notes (
  voter TEXT NOT NULL,
  game TEXT NOT NULL,
  version TEXT NOT NULL,
  how TEXT NOT NULL,
  place TEXT NOT NULL,
  text TEXT NOT NULL,
  at INTEGER NOT NULL,
  PRIMARY KEY (voter, game, version)
);
CREATE INDEX IF NOT EXISTS notes_by_time ON notes (voter, at);
