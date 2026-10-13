-- Exact schema from the first 3.0 development slice; no deployment data.
CREATE TABLE IF NOT EXISTS installations (
                installation_id TEXT PRIMARY KEY, package_id TEXT NOT NULL,
                version TEXT NOT NULL, digest TEXT NOT NULL, snapshot TEXT NOT NULL,
                snapshot_digest TEXT NOT NULL, workspace TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('inactive','active','uninstalled')),
                generation INTEGER NOT NULL DEFAULT 0, installed_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_active ON installations(state) WHERE state='active';
CREATE TABLE IF NOT EXISTS registrations (
                installation_id TEXT NOT NULL REFERENCES installations(installation_id),
                role_id TEXT NOT NULL, generation INTEGER NOT NULL, registered_at TEXT NOT NULL,
                PRIMARY KEY(installation_id, role_id, generation));
CREATE TABLE IF NOT EXISTS actor_tokens (
                token_hash TEXT PRIMARY KEY, installation_id TEXT NOT NULL,
                role_id TEXT NOT NULL, generation INTEGER NOT NULL,
                FOREIGN KEY(installation_id,role_id,generation)
                    REFERENCES registrations(installation_id,role_id,generation));
CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL REFERENCES installations(installation_id),
                role_id TEXT NOT NULL, generation INTEGER NOT NULL, text TEXT NOT NULL,
                client_request_id TEXT, state TEXT NOT NULL CHECK(state IN ('queued','running','completed','failed')),
                created_at TEXT NOT NULL, completed_at TEXT,
                UNIQUE(installation_id,client_request_id));
CREATE TABLE IF NOT EXISTS results (
                task_id TEXT PRIMARY KEY REFERENCES tasks(task_id), installation_id TEXT NOT NULL,
                role_id TEXT NOT NULL, generation INTEGER NOT NULL,
                tool_id TEXT NOT NULL, output TEXT, status TEXT NOT NULL CHECK(status IN ('ok','error')),
                error TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications (
                notification_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
                installation_id TEXT NOT NULL, role_id TEXT NOT NULL, generation INTEGER NOT NULL,
                kind TEXT NOT NULL, created_at TEXT NOT NULL);
PRAGMA application_id=1296118576;
PRAGMA user_version=1;
