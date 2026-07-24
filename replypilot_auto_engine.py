# replypilot_auto_engine.py
# Replyit Auto-Send Engine v1.0.0
# Pure scheduling/eligibility logic — no UI, no COM, no threads of its own.
# The app calls evaluate_and_schedule() after each scan and due() on its
# UI tick; actual sending stays in the app's existing worker-thread path.
#
# Safety model (all gates must pass before anything is scheduled):
#   1. Master switch on (settings, off by default)
#   2. Category has auto_send enabled (graduated >=50 samples at >=95%
#      unchanged, or explicit manual override in the Stats window)
#   3. Category is not in NEVER_AUTO (escalate and no_reply are hard-excluded
#      even if someone force-overrides them — escalations always get a human)
#   4. Confidence >= auto_send_min_conf (settings)
#   5. Draft is non-empty
#   6. Item is still pending at both schedule time AND fire time
# Every send then waits auto_send_delay_sec — the undo window. Opening the
# item's review or deleting it cancels the scheduled send.

ENGINE_VERSION = "1.0.0"

import time

import replypilot_classify_engine as clf
import replypilot_record_engine as rec

# Hard exclusions — never auto-sendable regardless of graduation/override
NEVER_AUTO = (clf.CAT_ESCALATE, clf.CAT_NO_REPLY)


class AutoSendEngine:
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self.scheduled = {}   # message_id -> fire_epoch
        self.sent_log = []    # (message_id, epoch) this session

    # ------------------------------------------------------------- settings
    def master_on(self):
        return bool(self.settings.get("auto_send_master", False))

    def delay_sec(self):
        try:
            return max(5, int(self.settings.get("auto_send_delay_sec", 60)))
        except Exception:
            return 60

    def min_conf(self):
        try:
            return float(self.settings.get("auto_send_min_conf", 0.85))
        except Exception:
            return 0.85

    # ----------------------------------------------------------- evaluation
    def eligible_rows(self):
        """All currently pending rows that pass every gate."""
        if not self.master_on():
            return []
        stats = self.store.category_stats()
        min_conf = self.min_conf()
        out = []
        for r in self.store.pending():
            cat = r.get("ai_category")
            if not r.get("ai_needs_reply"):
                continue
            if cat in NEVER_AUTO:
                continue
            if not stats.get(cat, {}).get("auto_send", False):
                continue
            if (r.get("ai_confidence") or 0.0) < min_conf:
                continue
            if not (r.get("ai_draft") or "").strip():
                continue
            out.append(r)
        return out

    def evaluate_and_schedule(self, now=None):
        """Schedule every newly eligible pending item. Returns list of
        newly scheduled message_ids."""
        now = now if now is not None else time.time()
        newly = []
        if not self.master_on():
            return newly
        fire_at = now + self.delay_sec()
        for r in self.eligible_rows():
            mid = r["message_id"]
            if mid not in self.scheduled:
                self.scheduled[mid] = fire_at
                newly.append(mid)
        return newly

    # ------------------------------------------------------------ lifecycle
    def cancel(self, message_id):
        """Cancel a scheduled send (review opened, deleted, etc.).
        Returns True if something was cancelled."""
        return self.scheduled.pop(message_id, None) is not None

    def cancel_all(self):
        n = len(self.scheduled)
        self.scheduled.clear()
        return n

    def due(self, now=None):
        """Pop and return message_ids whose delay has elapsed AND that are
        still pending and still eligible (re-verified at fire time — the
        user may have decided or deleted them during the delay window)."""
        now = now if now is not None else time.time()
        fired = [m for m, t in self.scheduled.items() if t <= now]
        out = []
        if not fired:
            return out
        eligible_now = {r["message_id"] for r in self.eligible_rows()}
        for mid in fired:
            del self.scheduled[mid]
            if mid in eligible_now:
                out.append(mid)
                self.sent_log.append((mid, now))
        return out

    def pending_count(self):
        return len(self.scheduled)

    def next_fire_in(self, now=None):
        """Seconds until the next scheduled send, or None."""
        if not self.scheduled:
            return None
        now = now if now is not None else time.time()
        return max(0, int(min(self.scheduled.values()) - now))
