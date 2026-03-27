PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS appuser (
    id INTEGER NOT NULL PRIMARY KEY,
    auth_issuer VARCHAR NOT NULL,
    auth_subject VARCHAR NOT NULL,
    email VARCHAR,
    full_name VARCHAR,
    given_name VARCHAR,
    family_name VARCHAR,
    picture_url VARCHAR,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_appuser_auth_identity ON appuser (auth_issuer, auth_subject);
CREATE INDEX IF NOT EXISTS ix_appuser_auth_subject ON appuser (auth_subject);
CREATE INDEX IF NOT EXISTS ix_appuser_auth_issuer ON appuser (auth_issuer);

CREATE TABLE IF NOT EXISTS applogintoken (
    id INTEGER NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    email VARCHAR NOT NULL,
    token_hash VARCHAR NOT NULL,
    expires_at DATETIME NOT NULL,
    used_at DATETIME,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(user_id) REFERENCES appuser (id)
);

CREATE INDEX IF NOT EXISTS ix_applogintoken_user_id ON applogintoken (user_id);
CREATE INDEX IF NOT EXISTS ix_applogintoken_email ON applogintoken (email);
CREATE INDEX IF NOT EXISTS ix_applogintoken_token_hash ON applogintoken (token_hash);

ALTER TABLE exam ADD COLUMN owner_user_id INTEGER;
ALTER TABLE classlist ADD COLUMN owner_user_id INTEGER;

CREATE INDEX IF NOT EXISTS ix_exam_owner_user_id ON exam (owner_user_id);
CREATE INDEX IF NOT EXISTS ix_classlist_owner_user_id ON classlist (owner_user_id);
