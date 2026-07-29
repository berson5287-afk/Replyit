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
#   7. Item is not flagged needs_input — a reply that depends on a fact only
#      the user holds is never safe to send automatically, whatever its
#      category's agreement rate happens to be
# Every send then waits auto_send_delay_sec — the undo window. Opening the
# item's review or deleting it cancels the scheduled send.

ENGINE_VERSION = "1.2.0"  # v1.2.0: per-category opt-in, delay in minutes,
                          # inspectable queue for the Auto-Send tab
                          # v1.1.0: needs_input is a hard auto-send block

import time

import replypilot_classify_engine as clf
import replypilot_record_engine as rec

# Hard exclusions — never auto-sendable regardless of graduation/override
NEVER_AUTO = (clf.CAT_ESCALATE, clf.CAT_NO_REPLY)

# The undo window never closes completely, even with the delay switched off.
MIN_DELAY_SEC = 5


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
        """The undo window, in seconds.

        v1.2.0: the UI works in minutes, because a delay you would actually
        use to catch a bad reply is minutes long, not seconds. Stored as
        auto_send_delay_min; auto_send_delay_sec is still read so an existing
        settings file keeps its configured window.

        Turning the delay off does not mean zero. MIN_DELAY_SEC still applies:
        every gate is re-checked at fire time, so a row that is opened or
        deleted in that window is caught, and removing the window entirely
        would remove the only chance to stop a wrong reply.
        """
        if not self.delay_enabled():
            return MIN_DELAY_SEC
        mins = self.settings.get("auto_send_delay_min")
        if mins is not None and str(mins).strip() != "":
            try:
                return max(MIN_DELAY_SEC, int(float(str(mins).strip()) * 60))
            except (TypeError, ValueError):
                pass
        try:
            return max(MIN_DELAY_SEC,
                       int(self.settings.get("auto_send_delay_sec", 60)))
        except (TypeError, ValueError):
            return 60

    def delay_enabled(self):
        return bool(self.settings.get("auto_send_delay_enabled", True))

    def allowed_categories(self):
        """Categories the user has opted in to, or None for 'no restriction'.

        A missing key means every category is permitted, so an existing
        settings file behaves exactly as before. An explicit empty list means
        the user has opted nothing in, and nothing sends — which is different
        from 'not configured' and must not be collapsed into it.
        """
        v = self.settings.get("auto_send_categories")
        if v is None:
            return None
        return {str(c) for c in v}

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
        allowed = self.allowed_categories()
        out = []
        for r in self.store.pending():
            cat = r.get("ai_category")
            if not r.get("ai_needs_reply"):
                continue
            if cat in NEVER_AUTO:
                continue
            if allowed is not None and cat not in allowed:
                # v1.2.0: the user's per-category opt-in. This only ever
                # narrows — a category ticked here still has to graduate or
                # be overridden below, because a tick is a preference and
                # graduation is evidence, and the evidence is what makes
                # sending safe.
                continue
            if not stats.get(cat, {}).get("auto_send", False):
                continue
            if (r.get("ai_confidence") or 0.0) < min_conf:
                continue
            if not (r.get("ai_draft") or "").strip():
                continue
            if r.get("needs_input"):
                # v1.1.0: the reply depends on a fact only the user holds,
                # so no category graduation can make this safe to send
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

    def queued_rows(self, now=None):
        """[(row, seconds_remaining)] for everything waiting to send.

        v1.2.0: the Auto-Send tab needs to show what is about to go out and
        how long is left to stop it. Rows whose email has since vanished from
        the store are dropped rather than shown as blanks — and their
        schedule entry goes with them, since there is nothing left to send.
        """
        now = now if now is not None else time.time()
        out = []
        for mid, fire_at in sorted(self.scheduled.items(), key=lambda kv: kv[1]):
            row = self.store.get(mid)
            if row is None:
                self.scheduled.pop(mid, None)
                continue
            out.append((row, max(0, int(round(fire_at - now)))))
        return out

    def pending_count(self):
        return len(self.scheduled)

    def next_fire_in(self, now=None):
        """Seconds until the next scheduled send, or None."""
        if not self.scheduled:
            return None
        now = now if now is not None else time.time()
        return max(0, int(min(self.scheduled.values()) - now))
