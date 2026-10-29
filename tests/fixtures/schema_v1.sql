
                CREATE TABLE IF NOT EXISTS workspaces(
                    id TEXT PRIMARY KEY, path TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(
                    workspace_id TEXT NOT NULL, id TEXT NOT NULL, metadata TEXT NOT NULL,
                    last_active TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(workspace_id,id),
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id));
                CREATE TABLE IF NOT EXISTS session_records(
                    id INTEGER PRIMARY KEY, workspace_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    record TEXT NOT NULL, search_text TEXT NOT NULL,
                    FOREIGN KEY(workspace_id,session_id) REFERENCES sessions(workspace_id,id));
                CREATE INDEX IF NOT EXISTS records_session ON session_records(workspace_id,session_id,id);
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    prompt TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL,
                    started_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT,
                    error TEXT NOT NULL DEFAULT '', owner_pid INTEGER NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id));
                CREATE INDEX IF NOT EXISTS runs_workspace ON runs(workspace_id,started_at DESC);
                CREATE TABLE IF NOT EXISTS run_steps(
                    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, tool_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL, arguments TEXT NOT NULL, status TEXT NOT NULL,
                    output TEXT NOT NULL DEFAULT '', elapsed REAL NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL, finished_at TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(id));
                CREATE TABLE IF NOT EXISTS memory_index(
                    workspace_id TEXT NOT NULL, scope TEXT NOT NULL, name TEXT NOT NULL,
                    type TEXT NOT NULL, description TEXT NOT NULL, body TEXT NOT NULL,
                    path TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id,scope,name));
                PRAGMA user_version=1;
            