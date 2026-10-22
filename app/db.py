import json
import sqlite3
from contextlib import contextmanager
from app.settings import HOME


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@contextmanager
def db():
    con = sqlite3.connect(HOME / 'wargame.sqlite', timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    HOME.mkdir(parents=True, exist_ok=True)
    for name in ('images', 'raw', 'processed'):
        (HOME / name).mkdir(exist_ok=True)
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS questions (
          question_id TEXT PRIMARY KEY, parent_id TEXT, replay_id TEXT NOT NULL,
          image_step INTEGER NOT NULL, level TEXT NOT NULL, role TEXT NOT NULL,
          body TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS question_filter ON questions(level,role,replay_id);
        CREATE TABLE IF NOT EXISTS images (id TEXT PRIMARY KEY, mime TEXT, real INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS image_map (
          replay_id TEXT, image_step INTEGER, image_id TEXT REFERENCES images(id),
          PRIMARY KEY(replay_id,image_step));
        CREATE TABLE IF NOT EXISTS models (id TEXT PRIMARY KEY, body TEXT NOT NULL, secret TEXT);
        CREATE TABLE IF NOT EXISTS prompts (id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS teams (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS submissions (
          id TEXT PRIMARY KEY, team_id TEXT REFERENCES teams(id), version TEXT NOT NULL,
          name TEXT NOT NULL, model_id TEXT NOT NULL, prompt_id TEXT NOT NULL,
          fingerprint TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(team_id,version));
        CREATE TABLE IF NOT EXISTS import_previews (
          id TEXT PRIMARY KEY, secret TEXT, created_at TEXT NOT NULL, result TEXT);
        CREATE TABLE IF NOT EXISTS evaluation_batches (
          id TEXT PRIMARY KEY, request_key TEXT UNIQUE, name TEXT NOT NULL,
          created_at TEXT NOT NULL, body TEXT NOT NULL, started INTEGER NOT NULL DEFAULT 0,
          stopped INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS batch_entries (
          batch_id TEXT REFERENCES evaluation_batches(id), team_id TEXT NOT NULL,
          submission_id TEXT NOT NULL, run_id TEXT UNIQUE REFERENCES runs(id),
          PRIMARY KEY(batch_id,team_id));
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL,
          stop INTEGER DEFAULT 0, body TEXT NOT NULL, secret TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS results (
          run_id TEXT REFERENCES runs(id), question_id TEXT,
          body TEXT NOT NULL, PRIMARY KEY(run_id,question_id));
        CREATE TABLE IF NOT EXISTS calls (
          run_id TEXT, question_id TEXT, ordinal INTEGER, body TEXT NOT NULL,
          PRIMARY KEY(run_id,question_id,ordinal));
        ''')
        # Never replay an ambiguous, potentially charged request after a crash.
        c.execute("UPDATE runs SET status='interrupted', error='主办方进程中断；可统一重跑，不自动重复收费请求' WHERE status IN ('running','queued','stopping')")


def question_rows():
    with db() as c:
        rows = c.execute('SELECT body FROM questions ORDER BY question_id').fetchall()
    return [json.loads(r['body']) for r in rows]
