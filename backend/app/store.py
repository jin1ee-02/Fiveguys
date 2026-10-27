"""SQLite persistence and room-scoped bearer capabilities for the local MVP."""

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from app.schemas import RoomSettings
from app.scoring import thermometer


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY, relationship TEXT NOT NULL, invite_hash TEXT UNIQUE,
                    version INTEGER NOT NULL DEFAULT 0, temperature REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS participants (
                    room_id TEXT NOT NULL REFERENCES rooms(id), speaker TEXT NOT NULL,
                    token_hash TEXT UNIQUE NOT NULL, settings TEXT NOT NULL,
                    PRIMARY KEY(room_id, speaker)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    room_id TEXT NOT NULL REFERENCES rooms(id), speaker TEXT NOT NULL, text TEXT NOT NULL,
                    created_at TEXT NOT NULL, request_id TEXT NOT NULL, result TEXT NOT NULL,
                    UNIQUE(room_id, request_id)
                );
                CREATE INDEX IF NOT EXISTS messages_room ON messages(room_id, sequence);
                CREATE TABLE IF NOT EXISTS verdicts (
                    id TEXT PRIMARY KEY, room_id TEXT NOT NULL REFERENCES rooms(id), snapshot TEXT NOT NULL,
                    result TEXT NOT NULL, appeals TEXT NOT NULL, parent_id TEXT,
                    request_id TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(room_id, request_id)
                );
            """)
            if "core_state" not in [row["name"] for row in db.execute("PRAGMA table_info(rooms)")]:
                db.execute("ALTER TABLE rooms ADD COLUMN core_state TEXT")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, relationship):
        room_id, token, invite = str(uuid.uuid4()), secrets.token_urlsafe(32), secrets.token_urlsafe(18)
        with self.connect() as db:
            db.execute("INSERT INTO rooms(id,relationship,invite_hash,created_at) VALUES(?,?,?,?)", (room_id, relationship, digest(invite), now()))
            db.execute("INSERT INTO participants VALUES(?,?,?,?)", (room_id, "A", digest(token), RoomSettings().model_dump_json()))
        return {"roomId": room_id, "speaker": "A", "token": token, "inviteCode": invite}

    def join(self, invite):
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            room = db.execute("SELECT * FROM rooms WHERE invite_hash=?", (digest(invite),)).fetchone()
            if not room:
                raise HTTPException(404, "초대 코드가 유효하지 않거나 이미 사용됐어요.")
            db.execute("INSERT INTO participants VALUES(?,?,?,?)", (room["id"], "B", digest(token), RoomSettings().model_dump_json()))
            db.execute("UPDATE rooms SET invite_hash=NULL WHERE id=?", (room["id"],))
        return {"roomId": room["id"], "speaker": "B", "token": token, "inviteCode": None}

    def authorize(self, room_id, authorization):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "대화방 참가 토큰이 필요합니다.")
        with self.connect() as db:
            row = db.execute("SELECT speaker FROM participants WHERE room_id=? AND token_hash=?", (room_id, digest(authorization[7:]))).fetchone()
        if not row:
            raise HTTPException(403, "이 대화방에 접근할 수 없습니다.")
        return row["speaker"]

    @staticmethod
    def message(row):
        return {"id": row["id"], "speaker": row["speaker"], "text": row["text"], "createdAt": row["created_at"], "result": {"mode": "ai", "data": json.loads(row["result"])}}

    def state(self, room_id, speaker):
        with self.connect() as db:
            room = db.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
            if not room:
                raise HTTPException(404, "대화방을 찾을 수 없습니다.")
            rows = db.execute("SELECT * FROM messages WHERE room_id=? ORDER BY sequence DESC LIMIT 200", (room_id,)).fetchall()
            settings = db.execute("SELECT settings FROM participants WHERE room_id=? AND speaker=?", (room_id, speaker)).fetchone()
            count = db.execute("SELECT COUNT(*) FROM participants WHERE room_id=?", (room_id,)).fetchone()[0]
        messages, settings = [self.message(row) for row in reversed(rows)], json.loads(settings[0])
        # Both thermometers come from the stored Light results, so reading the room costs no model call.
        emotions = {who: thermometer(messages, who) for who in "AB"}
        return {"roomId": room_id, "relationship": room["relationship"], "version": room["version"], "temperature": room["temperature"], "messages": messages, "settings": settings, "participantCount": count, "emotions": emotions}

    def update(self, room_id, speaker, request):
        with self.connect() as db:
            db.execute("UPDATE rooms SET relationship=?,version=version+1 WHERE id=? AND relationship<>?", (request.relationship, room_id, request.relationship))
            db.execute("UPDATE participants SET settings=? WHERE room_id=? AND speaker=?", (request.settings.model_dump_json(), room_id, speaker))
        return self.state(room_id, speaker)

    def sent(self, room_id, request_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM messages WHERE room_id=? AND request_id=?", (room_id, request_id)).fetchone()
        return self.message(row) if row else None

    def append(self, room_id, speaker, request, result, version):
        message_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            room = db.execute("SELECT version FROM rooms WHERE id=?", (room_id,)).fetchone()
            if room[0] != version:
                raise HTTPException(409, "대화 문맥이 변경됐어요. 다시 보내기를 눌러주세요.")
            db.execute("INSERT INTO messages(id,room_id,speaker,text,created_at,request_id,result) VALUES(?,?,?,?,?,?,?)", (message_id, room_id, speaker, request.text, now(), request.request_id, result.model_dump_json()))
            db.execute("UPDATE rooms SET temperature=?,version=version+1 WHERE id=?", (result.temperature, room_id))
        return self.state(room_id, speaker)

    def verdict_by_request(self, room_id, request_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM verdicts WHERE room_id=? AND request_id=?", (room_id, request_id)).fetchone()
        return dict(row) if row else None

    def verdict(self, verdict_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM verdicts WHERE id=?", (verdict_id,)).fetchone()
        if not row:
            raise HTTPException(404, "판결을 찾을 수 없습니다.")
        return dict(row)

    def core(self, room_id):
        with self.connect() as db:
            row = db.execute("SELECT core_state FROM rooms WHERE id=?", (room_id,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def save_verdict(self, request, snapshot, result, core=None):
        with self.connect() as db:
            if core:
                db.execute("UPDATE rooms SET core_state=? WHERE id=?", (core, request.room_id))
            # appeals / parent_id columns stay for databases made before appeals were removed; new rows leave them empty.
            db.execute("INSERT INTO verdicts VALUES(?,?,?,?,?,?,?,?)", (result.verdictId, request.room_id, json.dumps(snapshot, ensure_ascii=False), result.model_dump_json(), "[]", None, request.request_id, now()))

    def history(self, room_id):
        with self.connect() as db:
            rows = db.execute("SELECT result FROM verdicts WHERE room_id=? ORDER BY created_at DESC LIMIT 20", (room_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]


# Fixed stripes keep the lock registry bounded. Deployment uses one worker.
ROOM_LOCKS = [threading.Lock() for _ in range(64)]


@contextmanager
def room_lock(key, wait=25.0):
    """Two people sending at the same moment queue for the short analysis instead of one of them failing."""
    lock = ROOM_LOCKS[int(digest(key)[:8], 16) % len(ROOM_LOCKS)]
    if not lock.acquire(timeout=wait):
        raise HTTPException(429, "대화방의 이전 작업을 기다려주세요.", headers={"Retry-After": "3"})
    try:
        yield
    finally:
        lock.release()


@lru_cache(maxsize=1)
def get_store():
    path = os.getenv("DATABASE_PATH", "data/mvp.sqlite3")
    if not Path(path).is_absolute():
        path = str(Path(__file__).resolve().parents[1] / path)
    return Store(path)
