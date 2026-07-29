# replypilot_record_engine.py
# ReplyPilot Record Engine v1.0.0
# Stable decision-record store. SQLite primary + JSONL append-only audit log.
# Keyed on Internet Message-ID (never EntryID). Schema is frozen from v1 —
# additive changes only, so the training corpus stays usable forever.

ENGINE_VERSION = "1.5.0"
# v1.5.0: reclassify_pending (re-run classification without touching corpus)
# v1.4.0: needs_input column (+ migration) and set_needs_input
# v1.3.0: origin column (+ migration) and import_decided_record, so records
#         promoted from the learning importer are distinguishable from live
#         decisions in the corpus forever
# v1.2.0: update_ai_draft (AI Review pass rewrites pending drafts in place)
# v1.1.x (shipped unbumped with app v1.1.0 — noted here for the record):
#   ACTION_DELETED, purge(), dynamic SQL placeholders in stats/export

import os
import json
import sqlite3
import hashlib
import threading
from datetime import datetime, timezone

# ---------------------------------------------------------------- storage dir

def data_dir():
    """%LOCALAPPDATA%\\ReplyPilot on Windows (deliberately not OneDrive-synced,
    same reasoning as the MaINbox diag bridge files), ~/.replypilot elsewhere."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        d = os.path.join(base, "ReplyPilot")
    else:
        d = os.path.join(os.path.expanduser("~"), ".replypilot")
    os.makedirs(d, exist_ok=True)
    return d


DB_NAME = "replypilot.db"
AUDIT_NAME = "decisions_audit.jsonl"

# User actions (closed set — do not free-form these)
ACTION_PENDING = "pending"
ACTION_ACCEPTED = "accepted"            # AI category + draft accepted as-is
ACTION_RECATEGORIZED = "recategorized"  # user picked a different category
ACTION_EDITED = "edited"                # kept category, edited draft text
ACTION_DECLINED = "declined"            # no reply will be sent
ACTION_MOVED_NO_REPLY = "moved_no_reply"
ACTION_UNDO_NO_REPLY = "undo_no_reply"
ACTION_AUTO_SENT = "auto_sent"          # graduated category, sent without review
ACTION_DELETED = "deleted"              # v1.1.0: soft-delete; counts as no_reply
                                        # accepted so the AI learns the pattern

# Actions that count as "AI was right, unchanged" for graduation math
UNCHANGED_ACTIONS = (ACTION_ACCEPTED, ACTION_AUTO_SENT, ACTION_DELETED)
# Actions that count as a decided sample at all
DECIDED_ACTIONS = (ACTION_ACCEPTED, ACTION_RECATEGORIZED, ACTION_EDITED,
                   ACTION_DECLINED, ACTION_MOVED_NO_REPLY, ACTION_AUTO_SENT,
                   ACTION_DELETED)

# Graduation thresholds — the DEFAULTS. A store can be given its own via
# set_graduation(), because 50 at 95% is a judgement call rather than a law:
# it suits a high-volume category and is unreachable for a rare one, and the
# person carrying the risk of a wrong reply is the one who should be setting
# the bar. Lowering it never bypasses the other gates — a category still has
# to be ticked in Settings, be outside NEVER_AUTO, clear the confidence
# threshold, and hold a draft.
GRADUATION_MIN_SAMPLES = 50
GRADUATION_MIN_AGREEMENT = 0.95

# Floors that hold whatever the settings say. One sample is the least that can
# be called evidence, and an agreement bar of zero would mean "graduate a
# category you have never once agreed with", which is not a threshold at all.
GRADUATION_FLOOR_SAMPLES = 1
GRADUATION_FLOOR_AGREEMENT = 0.5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    message_id     TEXT PRIMARY KEY,
    received_at    TEXT,
    subject        TEXT,
    sender         TEXT,
    features       TEXT,
    ai_needs_reply INTEGER,
    ai_category    TEXT,
    ai_confidence  REAL,
    ai_draft       TEXT,
    ai_source      TEXT,
    user_action    TEXT NOT NULL DEFAULT 'pending',
    final_category TEXT,
    final_draft    TEXT,
    changed_by_user INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    decided_at     TEXT,
    body_preview   TEXT,
    body_full      TEXT,
    origin         TEXT NOT NULL DEFAULT 'live',
    needs_input    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS category_config (
    category         TEXT PRIMARY KEY,
    auto_send        INTEGER NOT NULL DEFAULT 0,
    manual_override  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_decisions_action ON decisions(user_action);
CREATE INDEX IF NOT EXISTS idx_decisions_aicat  ON decisions(ai_category);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RecordStore:
    """Thread-safe decision store. One instance per app; all methods take
    their own short-lived cursor under a lock — no long transactions."""

    def __init__(self, directory=None):
        self.dir = directory or data_dir()
        self.db_path = os.path.join(self.dir, DB_NAME)
        self.audit_path = os.path.join(self.dir, AUDIT_NAME)
        # per-store graduation bar; set_graduation() overrides from settings
        self.min_samples = GRADUATION_MIN_SAMPLES
        self.min_agreement = GRADUATION_MIN_AGREEMENT
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate_locked()
            self._conn.commit()

    def _migrate_locked(self):
        """v1.3.0: additive migrations for databases created by earlier
        versions. CREATE TABLE IF NOT EXISTS won't add columns to a table
        that already exists, so new columns are applied here. Additive only
        — the corpus schema is never rewritten or dropped."""
        cur = self._conn.execute("PRAGMA table_info(decisions)")
        have = {r["name"] for r in cur.fetchall()}
        if "origin" not in have:
            self._conn.execute(
                "ALTER TABLE decisions ADD COLUMN origin TEXT "
                "NOT NULL DEFAULT 'live'")
        if "needs_input" not in have:
            self._conn.execute(
                "ALTER TABLE decisions ADD COLUMN needs_input INTEGER "
                "NOT NULL DEFAULT 0")

    # ------------------------------------------------------------- audit log
    def _audit(self, event, payload):
        rec = {"ts": _now(), "event": event}
        rec.update(payload)
        # compact JSON, one record per line (lesson learned: never indent an
        # append-only ops log)
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ------------------------------------------------------------- ingestion
    @staticmethod
    def fallback_message_id(sender, subject, received_at, body):
        h = hashlib.md5(
            ("%s|%s|%s|%s" % (sender, subject, received_at, (body or "")[:500]))
            .encode("utf-8", "replace")).hexdigest()
        return "<replypilot-synth-%s@local>" % h

    def upsert_intake(self, message_id, received_at, subject, sender,
                      features, ai_needs_reply, ai_category, ai_confidence,
                      ai_draft, ai_source, body_full, needs_input=False):
        """Record a newly classified email. Idempotent on message_id —
        re-scanning the same mail never duplicates or clobbers a decision."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT user_action FROM decisions WHERE message_id=?",
                (message_id,))
            row = cur.fetchone()
            if row is not None:
                return False  # already known; never overwrite
            preview = (body_full or "")[:300]
            self._conn.execute(
                """INSERT INTO decisions
                   (message_id, received_at, subject, sender, features,
                    ai_needs_reply, ai_category, ai_confidence, ai_draft,
                    ai_source, user_action, created_at, body_preview,
                    body_full, needs_input)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (message_id, received_at, subject, sender,
                 json.dumps(features or {}, separators=(",", ":")),
                 1 if ai_needs_reply else 0, ai_category,
                 float(ai_confidence or 0.0), ai_draft, ai_source,
                 ACTION_PENDING, _now(), preview, body_full,
                 1 if needs_input else 0))
            self._conn.commit()
        self._audit("intake", {
            "message_id": message_id, "ai_category": ai_category,
            "ai_confidence": ai_confidence, "ai_source": ai_source,
            "needs_reply": bool(ai_needs_reply), "subject": subject})
        return True

    # -------------------------------------------------------------- decision
    def record_decision(self, message_id, user_action, final_category=None,
                        final_draft=None):
        assert user_action in DECIDED_ACTIONS + (ACTION_UNDO_NO_REPLY,), \
            "unknown action %r" % user_action
        with self._lock:
            cur = self._conn.execute(
                "SELECT ai_category, ai_draft FROM decisions WHERE message_id=?",
                (message_id,))
            row = cur.fetchone()
            if row is None:
                return False
            ai_cat, ai_draft = row["ai_category"], row["ai_draft"]
            fcat = final_category if final_category is not None else ai_cat
            fdraft = final_draft if final_draft is not None else ai_draft
            changed = 1 if (user_action not in UNCHANGED_ACTIONS) else 0
            self._conn.execute(
                """UPDATE decisions SET user_action=?, final_category=?,
                   final_draft=?, changed_by_user=?, decided_at=?
                   WHERE message_id=?""",
                (user_action, fcat, fdraft, changed, _now(), message_id))
            self._conn.commit()
        self._audit("decision", {
            "message_id": message_id, "action": user_action,
            "ai_category": ai_cat, "final_category": fcat,
            "changed_by_user": bool(changed)})
        return True

    def reopen(self, message_id, new_ai_category=None, new_ai_draft=None,
               new_needs_reply=None, ai_source=None):
        """Undo-from-No-Reply path: put an item back to pending, optionally
        with a fresh classification."""
        with self._lock:
            sets, vals = ["user_action=?", "decided_at=NULL"], [ACTION_PENDING]
            if new_ai_category is not None:
                sets.append("ai_category=?"); vals.append(new_ai_category)
            if new_ai_draft is not None:
                sets.append("ai_draft=?"); vals.append(new_ai_draft)
            if new_needs_reply is not None:
                sets.append("ai_needs_reply=?")
                vals.append(1 if new_needs_reply else 0)
            if ai_source is not None:
                sets.append("ai_source=?"); vals.append(ai_source)
            vals.append(message_id)
            self._conn.execute(
                "UPDATE decisions SET %s WHERE message_id=?" % ",".join(sets),
                vals)
            self._conn.commit()
        self._audit("reopen", {"message_id": message_id})
        return True

    def import_decided_record(self, message_id, received_at, subject, sender,
                              features, ai_category, ai_confidence, ai_draft,
                              ai_source, final_category, final_draft,
                              corrected, body_full=""):
        """v1.3.0: insert an already-decided record from the learning
        importer. Written with origin='import' so imported samples stay
        distinguishable from live decisions in the corpus forever.

        A confirmed inference lands as ACCEPTED (counts as unchanged); a
        corrected one lands as RECATEGORIZED (counts as changed) — the same
        arithmetic the live path uses, so graduation stats stay honest."""
        action = ACTION_RECATEGORIZED if corrected else ACTION_ACCEPTED
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM decisions WHERE message_id=?", (message_id,))
            if cur.fetchone() is not None:
                return False
            self._conn.execute(
                """INSERT INTO decisions
                   (message_id, received_at, subject, sender, features,
                    ai_needs_reply, ai_category, ai_confidence, ai_draft,
                    ai_source, user_action, final_category, final_draft,
                    changed_by_user, created_at, decided_at, body_preview,
                    body_full, origin)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (message_id, received_at, subject, sender,
                 json.dumps(features or {}, separators=(",", ":")),
                 1, ai_category, float(ai_confidence or 0.0), ai_draft,
                 ai_source, action, final_category, final_draft,
                 1 if corrected else 0, _now(), _now(),
                 (body_full or "")[:300], body_full, "import"))
            self._conn.commit()
        self._audit("import_decision", {
            "message_id": message_id, "action": action,
            "ai_category": ai_category, "final_category": final_category,
            "corrected": bool(corrected), "origin": "import"})
        return True

    def origin_counts(self):
        """v1.3.0: how much of the decided corpus is live vs imported."""
        d_ph = ",".join("?" * len(DECIDED_ACTIONS))
        with self._lock:
            cur = self._conn.execute(
                "SELECT origin, COUNT(*) n FROM decisions "
                "WHERE user_action IN (%s) GROUP BY origin" % d_ph,
                DECIDED_ACTIONS)
            return {r["origin"]: r["n"] for r in cur.fetchall()}

    def reclassify_pending(self, message_id, category, confidence, draft,
                           source, needs_reply=True, needs_input=None):
        """v1.5.0: rewrite the AI verdict on a still-pending row.

        Only pending rows are touched. A decided row's verdict is part of the
        training corpus — rewriting it would retroactively change what the
        user was agreeing or disagreeing with, and silently corrupt every
        agreement rate computed from it."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT user_action FROM decisions WHERE message_id=?",
                (message_id,))
            row = cur.fetchone()
            if row is None or row["user_action"] != ACTION_PENDING:
                return False
            sets = ["ai_category=?", "ai_confidence=?", "ai_draft=?",
                    "ai_source=?", "ai_needs_reply=?"]
            vals = [category, float(confidence or 0.0), draft, source,
                    1 if needs_reply else 0]
            if needs_input is not None:
                sets.append("needs_input=?")
                vals.append(1 if needs_input else 0)
            vals.append(message_id)
            self._conn.execute(
                "UPDATE decisions SET %s WHERE message_id=?" % ",".join(sets),
                vals)
            self._conn.commit()
        self._audit("reclassify", {"message_id": message_id,
                                   "category": category,
                                   "source": source})
        return True

    def set_needs_input(self, message_id, flag):
        """v1.4.0: mark/unmark an email as requiring the user's own
        knowledge. Auto-send treats this as a hard block."""
        with self._lock:
            self._conn.execute(
                "UPDATE decisions SET needs_input=? WHERE message_id=?",
                (1 if flag else 0, message_id))
            self._conn.commit()
        self._audit("needs_input", {"message_id": message_id,
                                    "flag": bool(flag)})
        return True

    def update_ai_draft(self, message_id, new_draft, source_note="ai_review"):
        """v1.2.0: AI Review pass — rewrite the AI draft of a still-pending
        item. Only touches pending records; a decided record's drafts are
        part of the training corpus and must never be rewritten."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT user_action, ai_source FROM decisions "
                "WHERE message_id=?", (message_id,))
            row = cur.fetchone()
            if row is None or row["user_action"] != ACTION_PENDING:
                return False
            src = row["ai_source"] or ""
            if source_note and source_note not in src:
                src = "%s+%s" % (src, source_note) if src else source_note
            self._conn.execute(
                "UPDATE decisions SET ai_draft=?, ai_source=? "
                "WHERE message_id=?", (new_draft, src, message_id))
            self._conn.commit()
        self._audit("ai_review_draft", {"message_id": message_id,
                                        "source": src})
        return True

    # ---------------------------------------------------------------- queries
    def pending(self):
        with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM decisions WHERE user_action=?
                   ORDER BY received_at DESC""", (ACTION_PENDING,))
            return [dict(r) for r in cur.fetchall()]

    def get(self, message_id):
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM decisions WHERE message_id=?", (message_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def by_action(self, action):
        with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM decisions WHERE user_action=?
                   ORDER BY received_at DESC""", (action,))
            return [dict(r) for r in cur.fetchall()]

    def known_ids(self):
        with self._lock:
            cur = self._conn.execute("SELECT message_id FROM decisions")
            return set(r["message_id"] for r in cur.fetchall())

    # ------------------------------------------------------------ stats/gradu
    def set_graduation(self, min_samples=None, min_agreement=None):
        """Set this store's graduation bar, clamped to the floors.

        Returns the (samples, agreement) actually in force, which is not
        always what was asked for — a settings file can hold anything, and a
        bar below the floors would let a category start replying to customers
        on no evidence at all.
        """
        if min_samples is not None and str(min_samples).strip() != "":
            try:
                self.min_samples = max(GRADUATION_FLOOR_SAMPLES,
                                       int(float(str(min_samples).strip())))
            except (TypeError, ValueError):
                pass
        if min_agreement is not None and str(min_agreement).strip() != "":
            try:
                v = float(str(min_agreement).strip())
                if v > 1:            # tolerate 95 meaning 95%
                    v = v / 100.0
                self.min_agreement = min(1.0,
                                         max(GRADUATION_FLOOR_AGREEMENT, v))
            except (TypeError, ValueError):
                pass
        return self.min_samples, self.min_agreement

    def graduation_preview(self, min_samples, min_agreement):
        """Which categories WOULD graduate at a proposed bar.

        The point of showing this before saving: a threshold is abstract, and
        the thing the user actually needs to know is which reply types are
        about to become sendable. NEVER_AUTO categories are still listed —
        the auto-send engine excludes them separately, and hiding them here
        would misrepresent what the number does.
        """
        try:
            n_min = max(GRADUATION_FLOOR_SAMPLES,
                        int(float(str(min_samples).strip())))
            a_min = float(str(min_agreement).strip())
            if a_min > 1:
                a_min = a_min / 100.0
            a_min = min(1.0, max(GRADUATION_FLOOR_AGREEMENT, a_min))
        except (TypeError, ValueError):
            return []
        out = []
        for cat, v in self.category_stats().items():
            if v["samples"] >= n_min and v["agreement"] >= a_min:
                out.append(cat)
        return sorted(out)

    def category_stats(self):
        """Per AI-assigned category: decided sample count, unchanged count,
        agreement rate, graduation status."""
        u_ph = ",".join("?" * len(UNCHANGED_ACTIONS))
        d_ph = ",".join("?" * len(DECIDED_ACTIONS))
        with self._lock:
            cur = self._conn.execute(
                """SELECT ai_category,
                          COUNT(*) AS n,
                          SUM(CASE WHEN user_action IN (%s) THEN 1 ELSE 0 END)
                              AS unchanged
                   FROM decisions
                   WHERE user_action IN (%s)
                   GROUP BY ai_category""" % (u_ph, d_ph),
                UNCHANGED_ACTIONS + DECIDED_ACTIONS)
            rows = cur.fetchall()
            overrides = {r["category"]: dict(r) for r in self._conn.execute(
                "SELECT * FROM category_config").fetchall()}
        out = {}
        for r in rows:
            cat = r["ai_category"] or "(none)"
            n = r["n"]
            unchanged = r["unchanged"] or 0
            rate = (unchanged / n) if n else 0.0
            graduated = (n >= self.min_samples
                         and rate >= self.min_agreement)
            ov = overrides.get(cat)
            auto_send = graduated
            if ov and ov["manual_override"]:
                auto_send = bool(ov["auto_send"])
            out[cat] = {"samples": n, "unchanged": unchanged,
                        "agreement": round(rate, 4),
                        "graduated": graduated, "auto_send": auto_send}
        # v1.4.0: a category can carry a manual override before it has any
        # decided rows at all. Building `out` only from the GROUP BY meant
        # such an override was silently ignored — the setting looked applied
        # and did nothing.
        for cat, ov in overrides.items():
            if cat in out or not ov["manual_override"]:
                continue
            out[cat] = {"samples": 0, "unchanged": 0, "agreement": 0.0,
                        "graduated": False, "auto_send": bool(ov["auto_send"])}
        return out

    def auto_send_enabled(self, category):
        return self.category_stats().get(category, {}).get("auto_send", False)

    def set_auto_send_override(self, category, enabled):
        with self._lock:
            self._conn.execute(
                """INSERT INTO category_config(category, auto_send, manual_override)
                   VALUES(?,?,1)
                   ON CONFLICT(category) DO UPDATE
                   SET auto_send=excluded.auto_send, manual_override=1""",
                (category, 1 if enabled else 0))
            self._conn.commit()
        self._audit("auto_send_override", {"category": category,
                                           "enabled": bool(enabled)})

    def export_training_jsonl(self, out_path):
        """Dump every decided record as one JSON object per line — the corpus."""
        n = 0
        d_ph = ",".join("?" * len(DECIDED_ACTIONS))
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM decisions WHERE user_action IN (%s)" % d_ph,
                DECIDED_ACTIONS)
            rows = [dict(r) for r in cur.fetchall()]
        with open(out_path, "w", encoding="utf-8") as f:
            for r in rows:
                r.pop("body_full", None)  # corpus keeps preview only
                f.write(json.dumps(r, ensure_ascii=False,
                                   separators=(",", ":")) + "\n")
                n += 1
        return n

    def purge(self, message_ids):
        """Hard-delete records by message_id list. No undo. Audit logged."""
        if not message_ids:
            return 0
        with self._lock:
            placeholders = ",".join("?" * len(message_ids))
            cur = self._conn.execute(
                "DELETE FROM decisions WHERE message_id IN (%s)"
                % placeholders, message_ids)
            n = cur.rowcount
            self._conn.commit()
        self._audit("purge", {"message_ids": message_ids, "count": n})
        return n

    def close(self):
        with self._lock:
            self._conn.close()
