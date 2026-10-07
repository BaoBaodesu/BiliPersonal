"""盲评增量表；不改写候选、模型或旧JSON工件。"""


def upgrade(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS policy_reviews (
      id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
      status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
      snapshot TEXT NOT NULL, preparation TEXT NOT NULL DEFAULT '{}',
      report TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
      abort_reason TEXT, UNIQUE(owner,request_id));
    CREATE UNIQUE INDEX IF NOT EXISTS policy_review_active ON policy_reviews(owner)
      WHERE status IN ('preparing','batch_ready','rating','failed');
    CREATE TABLE IF NOT EXISTS policy_review_batches (
      id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES policy_reviews(id),
      number INTEGER NOT NULL, status TEXT NOT NULL, frozen_at REAL NOT NULL,
      submitted_at REAL, private TEXT NOT NULL, invalid_reason TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS policy_batch_active ON policy_review_batches(experiment_id,number)
      WHERE status!='invalidated';
    CREATE TABLE IF NOT EXISTS policy_review_ratings (
      id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES policy_review_batches(id),
      bvid TEXT NOT NULL, ordinal INTEGER NOT NULL, video TEXT NOT NULL,
      score INTEGER CHECK(score IS NULL OR score IN (-1,0,1,2,3)),
      revision INTEGER NOT NULL DEFAULT 0, request_id TEXT, updated_at REAL,
      UNIQUE(batch_id,bvid));
    """)
