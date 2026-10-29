"""Initial schema, verbatim from SPEC §3."""

import re

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

DDL = r"""
CREATE EXTENSION IF NOT EXISTS vector;   -- in prod the initdb script creates it as superuser (§15)

CREATE TABLE sessions (
  id         UUID PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE intake_items (
  id              BIGSERIAL PRIMARY KEY,
  ticket_code     TEXT NOT NULL UNIQUE,                 -- BUG-101 … ; user items: BUG-U<id>, FEAT-U<id>, ENQ-U<id>
  category        TEXT NOT NULL CHECK (category IN ('BUG_REPORT','FEATURE_REQUEST','CLIENT_ENQUIRY')),
  title           TEXT NOT NULL CHECK (char_length(title) BETWEEN 3 AND 200),
  description     TEXT NOT NULL CHECK (char_length(description) <= 4000),
  error_code      TEXT CHECK (error_code ~ '^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$' AND char_length(error_code) <= 64),
  severity        TEXT NOT NULL DEFAULT 'MEDIUM' CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
  subsystem       TEXT NOT NULL DEFAULT 'General' CHECK (char_length(subsystem) <= 40),
  status          TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','DUPLICATE','RESOLVED_AUTO')),
  duplicate_of    BIGINT REFERENCES intake_items(id) ON DELETE CASCADE,
  base_count      INTEGER NOT NULL DEFAULT 0 CHECK (base_count >= 0),  -- seed duplicates (bugs) or votes (features)
  resolution_note TEXT,                                 -- FAQ answer (enquiries)
  owner_session   UUID REFERENCES sessions(id) ON DELETE CASCADE,       -- NULL = seed, visible to everyone
  embedding_model TEXT NOT NULL,
  embedding       halfvec(384) NOT NULL,
  search_tsv      tsvector GENERATED ALWAYS AS (
                    setweight(to_tsvector('english', title), 'A') ||
                    setweight(to_tsvector('english', coalesce(error_code, '')), 'A') ||
                    setweight(to_tsvector('english', description), 'B')) STORED,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((status = 'OPEN') = (duplicate_of IS NULL))
);
CREATE INDEX intake_tsv_gin    ON intake_items USING gin (search_tsv);
CREATE INDEX intake_category   ON intake_items (category);
CREATE INDEX intake_error_code ON intake_items (error_code) WHERE error_code IS NOT NULL;
CREATE INDEX intake_owner      ON intake_items (owner_session) WHERE owner_session IS NOT NULL;
CREATE INDEX intake_dup_of     ON intake_items (duplicate_of) WHERE duplicate_of IS NOT NULL;
CREATE INDEX intake_hnsw       ON intake_items USING hnsw (embedding halfvec_cosine_ops)
                               WITH (m = 16, ef_construction = 64);

CREATE TABLE votes (                                    -- one vote per session per feature
  item_id    BIGINT NOT NULL REFERENCES intake_items(id) ON DELETE CASCADE,
  session_id UUID   NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (item_id, session_id)
);

CREATE TABLE idempotency_keys (
  session_id    UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  key           TEXT NOT NULL CHECK (char_length(key) BETWEEN 16 AND 64),
  payload_hash  CHAR(64) NOT NULL,
  status_code   SMALLINT NOT NULL,
  response_json JSONB NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, key)
);
"""


def upgrade():
    # DECISION: Split only SQL terminators; the verbatim DDL also has semicolons in comments.
    start = 0
    for match in re.finditer(r"--[^\n]*|'(?:''|[^'])*'|;", DDL):
        if match[0] == ";":
            op.execute(DDL[start : match.start()])
            start = match.end()


def downgrade():
    op.execute("DROP TABLE idempotency_keys, votes, intake_items, sessions")
