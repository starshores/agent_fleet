-- Frozen 3.0 / 3.1 Phase A schema 4; independent migration source fixture.
CREATE TABLE installations (
        installation_id TEXT PRIMARY KEY, package_id TEXT NOT NULL,
        version TEXT NOT NULL, digest TEXT NOT NULL, snapshot TEXT NOT NULL,
        snapshot_digest TEXT NOT NULL, workspace TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('inactive','active','uninstalled')),
        generation INTEGER NOT NULL DEFAULT 0, installed_at TEXT NOT NULL);
CREATE TABLE registrations (
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, generation INTEGER NOT NULL, registered_at TEXT NOT NULL,
        PRIMARY KEY(installation_id,role_id,generation));
CREATE TABLE actor_tokens (
        token_hash TEXT PRIMARY KEY, installation_id TEXT NOT NULL,
        role_id TEXT NOT NULL, generation INTEGER NOT NULL,
        FOREIGN KEY(installation_id,role_id,generation)
        REFERENCES registrations(installation_id,role_id,generation));
CREATE TABLE tasks (
        task_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, generation INTEGER NOT NULL, text TEXT NOT NULL,
        client_request_id TEXT, state TEXT NOT NULL CHECK(state IN
        ('queued','running','completed','failed','awaiting_decision','waiting','needs_reconcile','cancelled')),
        created_at TEXT NOT NULL, completed_at TEXT, dispatch_generation INTEGER NOT NULL,
        parent_task_id TEXT REFERENCES tasks(task_id), UNIQUE(installation_id,client_request_id));
CREATE TABLE results (
        task_id TEXT PRIMARY KEY REFERENCES tasks(task_id), installation_id TEXT NOT NULL,
        role_id TEXT NOT NULL, generation INTEGER NOT NULL, tool_id TEXT NOT NULL,
        output TEXT, status TEXT NOT NULL CHECK(status IN ('ok','error')), error TEXT,
        created_at TEXT NOT NULL, attempt_id TEXT REFERENCES attempts(attempt_id));
CREATE TABLE notifications (
        notification_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
        installation_id TEXT NOT NULL, role_id TEXT NOT NULL, generation INTEGER NOT NULL,
        kind TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE attempts (
        attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
        installation_id TEXT NOT NULL REFERENCES installations(installation_id), role_id TEXT NOT NULL,
        generation INTEGER NOT NULL, ordinal INTEGER NOT NULL, tool_id TEXT NOT NULL,
        owner_hash TEXT NOT NULL, lease_hash TEXT NOT NULL, expires_at REAL NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('running','completed','failed','expired','cancelled')),
        created_at TEXT NOT NULL, finished_at TEXT, UNIQUE(task_id,ordinal));
CREATE TABLE attempt_events (
        event_id TEXT PRIMARY KEY, attempt_id TEXT REFERENCES attempts(attempt_id),
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE continuations (
        continuation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
        installation_id TEXT NOT NULL, from_generation INTEGER NOT NULL,
        to_generation INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(task_id,to_generation));
CREATE TABLE decisions (
        decision_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL,
        task_id TEXT NOT NULL REFERENCES tasks(task_id), generation INTEGER NOT NULL,
        request_id TEXT NOT NULL, title TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','approved','rejected','expired','superseded')),
        revision INTEGER NOT NULL, expires_at REAL, answer_id TEXT, answer TEXT,
        created_at TEXT NOT NULL, answered_at TEXT,
        UNIQUE(installation_id,request_id,generation));
CREATE TABLE outbox (
        event_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        generation INTEGER NOT NULL, task_id TEXT REFERENCES tasks(task_id), recipient_role TEXT NOT NULL,
        kind TEXT NOT NULL, payload TEXT NOT NULL, dedupe_key TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','delivered','held')),
        created_at TEXT NOT NULL, delivered_at TEXT,
        UNIQUE(installation_id,generation,dedupe_key));
CREATE TABLE requests (
        request_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL, generation INTEGER NOT NULL,
        parent_task_id TEXT NOT NULL REFERENCES tasks(task_id),
        child_task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
        from_role TEXT NOT NULL, to_role TEXT NOT NULL, client_request_id TEXT NOT NULL,
        text TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('waiting','replied')),
        created_at TEXT NOT NULL, replied_at TEXT,
        UNIQUE(installation_id,parent_task_id,client_request_id));
CREATE TABLE migration_log (
        version INTEGER PRIMARY KEY, previous_version INTEGER NOT NULL,
        applied_at TEXT NOT NULL, backup_path TEXT);
CREATE TABLE member_sessions (
        member_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_hash TEXT NOT NULL,
        lease_hash TEXT NOT NULL, deployment_digest TEXT NOT NULL, tool_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('ready','waiting','busy','stopping','stopped','lost')),
        expires_at REAL NOT NULL, started_at TEXT NOT NULL, updated_at TEXT NOT NULL, stop_reason TEXT);
CREATE TABLE attempt_members (
        attempt_id TEXT PRIMARY KEY REFERENCES attempts(attempt_id),
        member_id TEXT NOT NULL REFERENCES member_sessions(member_id));
CREATE TABLE role_deployments (
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, workspace TEXT NOT NULL,
        access TEXT NOT NULL CHECK(access IN ('none','read-only','read-write')),
        created_at TEXT NOT NULL, PRIMARY KEY(installation_id,role_id));
CREATE TABLE attempt_inputs (
        input_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, generation INTEGER NOT NULL,
        attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
        sequence INTEGER NOT NULL CHECK(sequence>0), request_id TEXT NOT NULL, text TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','sending','accepted','held')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(attempt_id,sequence), UNIQUE(installation_id,generation,request_id));
CREATE UNIQUE INDEX one_active ON installations(state) WHERE state='active';
CREATE UNIQUE INDEX one_running_attempt ON attempts(task_id) WHERE state='running';
CREATE UNIQUE INDEX unique_decision_answer ON decisions(installation_id,answer_id) WHERE answer_id IS NOT NULL;
CREATE UNIQUE INDEX one_live_member ON member_sessions(installation_id,role_id) WHERE state IN ('ready','waiting','busy','stopping');
PRAGMA application_id=1296118576;
PRAGMA user_version=4;
