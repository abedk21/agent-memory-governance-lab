"""SQLite memory policy engine. Caller identity/source labels are *not* authentication."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
import time
from typing import Callable

TOKEN_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{30,}|ghp_[A-Za-z0-9]{20,})\b|\b(?:password|api[_-]?key|bearer)\s*[:=]\s*\S+", re.I)
WORD_RE = re.compile(r"[a-z0-9]+", re.I)
MAX_LENGTH = 600


class MemoryPolicyError(ValueError):
    pass


class ConflictError(MemoryPolicyError):
    pass


@dataclass(frozen=True)
class Actor:
    tenant: str
    user: str
    session: str

    def __post_init__(self):
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) for value in (self.tenant, self.user, self.session)):
            raise MemoryPolicyError("tenant, user and session must be simple nonempty identifiers")


def terms(text: str) -> set[str]:
    return set(WORD_RE.findall(text.lower()))


def token_estimate(text: str) -> int:
    """A deliberately simple budget unit (words), not a model tokenizer."""
    return len(WORD_RE.findall(text))


class MemoryStore:
    def __init__(self, path: str = ":memory:", now: Callable[[], float] = time.time):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        self.now = now
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
          id INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL,
          user_id TEXT NOT NULL, session TEXT NOT NULL, scope TEXT NOT NULL,
          slot TEXT NOT NULL, content TEXT NOT NULL, source TEXT NOT NULL,
          created REAL NOT NULL, expires REAL NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('active','superseded'))
        );
        CREATE INDEX IF NOT EXISTS idx_owner ON memories(tenant,user_id,scope,status,expires);
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
          memory_id INTEGER, event_time REAL NOT NULL
        );
        """)

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _audit(self, action: str, mid: int | None) -> None:
        # Audit records deliberately contain no raw memory content or owner IDs.
        self.db.execute("INSERT INTO audit(action,memory_id,event_time) VALUES(?,?,?)", (action, mid, self.now()))

    def remember(self, actor: Actor, slot: str, content: str, *,
                 scope: str = "session", source: str = "user", consent: bool = False,
                 ttl_seconds: int | None = None) -> dict:
        if source != "user":
            raise MemoryPolicyError("Only explicitly trusted user turns may create memories; retrieved/tool text cannot")
        if scope not in {"session", "durable"}:
            raise MemoryPolicyError("Scope must be session or durable")
        if scope == "durable" and not consent:
            raise MemoryPolicyError("Explicit consent is required for durable memory")
        if not isinstance(slot, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,39}", slot):
            raise MemoryPolicyError("slot must be a lowercase name, 2-40 characters")
        if not isinstance(content, str) or not content.strip() or len(content) > MAX_LENGTH:
            raise MemoryPolicyError("Content must contain 1-600 characters")
        if TOKEN_RE.search(content):
            raise MemoryPolicyError("Possible secret detected; do not store credentials")
        ttl = ttl_seconds if ttl_seconds is not None else (3600 if scope == "session" else 90 * 86400)
        ceiling = 86400 if scope == "session" else 365 * 86400
        if not isinstance(ttl, int) or isinstance(ttl, bool) or not (1 <= ttl <= ceiling):
            raise MemoryPolicyError("TTL outside retention policy")
        content = content.strip()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            now = self.now()
            previous = self.db.execute("""SELECT id,content FROM memories WHERE tenant=? AND user_id=?
                AND scope=? AND (scope!='session' OR session=?) AND slot=? AND status='active' AND expires>?
                ORDER BY id DESC LIMIT 1""", (actor.tenant, actor.user, scope, actor.session, slot, now)).fetchone()
            if previous and previous["content"] == content:
                self.db.execute("COMMIT")
                return {"id": previous["id"], "result": "unchanged"}
            if previous:
                self.db.execute("UPDATE memories SET status='superseded' WHERE id=?", (previous["id"],))
                self._audit("supersede", previous["id"])
            cur = self.db.execute("""INSERT INTO memories(tenant,user_id,session,scope,slot,content,source,created,expires,status)
                    VALUES(?,?,?,?,?,?,?,?,?,'active')""",
                    (actor.tenant, actor.user, actor.session, scope, slot, content, source, now, now + ttl))
            self._audit("remember", cur.lastrowid)
            self.db.execute("COMMIT")
            return {"id": cur.lastrowid, "result": "updated" if previous else "created"}
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def recall(self, actor: Actor, query: str = "", *, word_budget: int = 80, limit: int = 10) -> list[dict]:
        if not isinstance(query, str) or len(query) > 250:
            raise MemoryPolicyError("Query too long")
        if not isinstance(word_budget, int) or not (1 <= word_budget <= 2000):
            raise MemoryPolicyError("Word budget outside policy")
        if not isinstance(limit, int) or not (1 <= limit <= 50):
            raise MemoryPolicyError("Result limit outside policy")
        rows = self.db.execute("""SELECT * FROM memories WHERE tenant=? AND user_id=? AND status='active'
             AND expires>? AND (scope='durable' OR (scope='session' AND session=?))""",
             (actor.tenant, actor.user, self.now(), actor.session)).fetchall()
        q = terms(query)
        ranked = []
        for r in rows:
            overlap = len(q & (terms(r["content"]) | terms(r["slot"])))
            if q and not overlap:
                continue
            ranked.append((overlap, r["created"], r["id"], r))
        ranked.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        selected = []
        remaining = word_budget
        for _, _, _, r in ranked:
            cost = token_estimate(r["content"])
            if cost > remaining:
                continue
            selected.append({"id": r["id"], "slot": r["slot"], "text": r["content"],
                             "scope": r["scope"], "source": "user", "trust": "untrusted_context",
                             "word_cost": cost})
            remaining -= cost
            if len(selected) >= limit:
                break
        return selected

    def forget(self, actor: Actor, *, memory_id: int | None = None, slot: str | None = None) -> int:
        if (memory_id is None) == (slot is None):
            raise MemoryPolicyError("Specify exactly one of memory_id or slot")
        if memory_id is not None and (type(memory_id) is not int or memory_id < 1):
            raise MemoryPolicyError("Invalid memory id")
        if slot is not None and (not isinstance(slot, str) or not slot):
            raise MemoryPolicyError("Invalid slot")
        field, value = ("id", memory_id) if memory_id is not None else ("slot", slot)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            rows = self.db.execute(f"SELECT id FROM memories WHERE tenant=? AND user_id=? AND {field}=?", (actor.tenant, actor.user, value)).fetchall()
            for row in rows:
                self.db.execute("DELETE FROM memories WHERE id=?", (row["id"],))
                self._audit("forget", row["id"])
            self.db.execute("COMMIT")
            return len(rows)
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def prune_expired(self) -> int:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            rows = self.db.execute("SELECT id FROM memories WHERE expires<=?", (self.now(),)).fetchall()
            for row in rows:
                self.db.execute("DELETE FROM memories WHERE id=?", (row["id"],))
                self._audit("expire", row["id"])
            self.db.execute("COMMIT")
            return len(rows)
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def export(self, actor: Actor) -> list[dict]:
        # Excludes other users and expired records, but includes superseded content for transparency.
        rows = self.db.execute("""SELECT id,scope,slot,content,status,created,expires FROM memories
            WHERE tenant=? AND user_id=? AND expires>? ORDER BY id""", (actor.tenant, actor.user, self.now())).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        # Demonstration-only aggregate counters, no user content.
        return {"active": self.db.execute("SELECT COUNT(*) FROM memories WHERE status='active' AND expires>?", (self.now(),)).fetchone()[0],
                "audit_events": self.db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]}
