# replyit_diag_bridge.py
# Replyit Diagnostic Bridge v1.0.0
#
# A loopback HTTP surface so an external tool (Claude Code) can inspect and
# exercise a RUNNING Replyit without screenshots or guesswork.
#
# Same security posture as the MaINbox bridge:
#   - OFF unless REPLYIT_DIAG=1 is set in the environment
#   - binds 127.0.0.1 only, never 0.0.0.0
#   - a fresh random token per boot, required on every request
#   - the token is written to the Replyit data dir (not OneDrive-synced) so a
#     local tool can read it, and is deleted on shutdown
#   - no endpoint touches Outlook COM
#
# Threading: the HTTP server runs on a daemon thread. SQLite reads go direct
# (RecordStore holds its own lock), but anything that touches Tk or app state
# is marshalled onto the Tk main thread via root.after and waited on — the
# same discipline the rest of the app uses, because Tk is not thread-safe and
# a background write would corrupt the widget tree rather than fail loudly.

BRIDGE_VERSION = "1.0.0"

import os
import json
import time
import socket
import secrets
import threading
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

DEFAULT_PORT = 8765
TOKEN_FILE = "diag_token.json"
MAIN_THREAD_TIMEOUT = 15.0

_bridge = None          # module-level singleton


def enabled():
    return os.environ.get("REPLYIT_DIAG", "") == "1"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _Handler(BaseHTTPRequestHandler):
    server_version = "ReplyitDiag/" + BRIDGE_VERSION

    # keep the console clean; the bridge has its own log
    def log_message(self, *args):
        pass

    # ------------------------------------------------------------- plumbing
    @property
    def bridge(self):
        return self.server.bridge

    def _send(self, code, payload):
        body = json.dumps(payload, indent=2, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _authed(self, query):
        supplied = (self.headers.get("X-Diag-Token")
                    or (query.get("token") or [""])[0])
        # constant-time compare so the token can't be probed by timing
        if not supplied or not secrets.compare_digest(
                str(supplied), self.bridge.token):
            self._send(401, {"error": "bad or missing token",
                             "hint": "send X-Diag-Token header or ?token="})
            return False
        return True

    def _body_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8", "replace"))
        except Exception:
            return {}

    # ----------------------------------------------------------------- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path.rstrip("/") or "/"
        if path == "/health":
            # deliberately unauthenticated: liveness only, no data
            return self._send(200, {"ok": True, "app": "replyit",
                                    "bridge": BRIDGE_VERSION,
                                    "pid": os.getpid()})
        if not self._authed(q):
            return
        try:
            handler = self.bridge.routes_get.get(path)
            if handler is None:
                # distinguish "wrong verb" from "no such endpoint" — the
                # former is the mistake a caller actually makes
                if path in self.bridge.routes_post:
                    return self._send(405, {
                        "error": "%s is POST-only" % path,
                        "hint": "resend as POST with a JSON body ({} is "
                                "fine when there are no arguments)"})
                return self._send(404, {"error": "unknown endpoint",
                                        "GET": sorted(self.bridge.routes_get),
                                        "POST": sorted(
                                            self.bridge.routes_post)})
            return self._send(200, handler(q))
        except Exception as e:
            return self._send(500, {"error": str(e),
                                    "trace": traceback.format_exc()})

    # ---------------------------------------------------------------- POST
    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path.rstrip("/") or "/"
        if not self._authed(q):
            return
        try:
            handler = self.bridge.routes_post.get(path)
            if handler is None:
                if path in self.bridge.routes_get:
                    return self._send(405, {
                        "error": "%s is GET-only" % path,
                        "hint": "resend as GET"})
                return self._send(404, {"error": "unknown endpoint",
                                        "GET": sorted(self.bridge.routes_get),
                                        "POST": sorted(
                                            self.bridge.routes_post)})
            return self._send(200, handler(self._body_json(), q))
        except Exception as e:
            return self._send(500, {"error": str(e),
                                    "trace": traceback.format_exc()})


class _Server(HTTPServer):
    """HTTPServer whose bind actually fails when the port is taken.

    HTTPServer sets allow_reuse_address = 1, which on Windows means SO_REUSEADDR
    lets a second process bind a port another process is already listening on,
    silently, with no OSError. The port-walk in start() is written around that
    OSError, so with the inherited default the walk never fires: Replyit binds
    over whatever already owns the port (MaINbox's bridge, on the same default
    8765), and which process receives a given connection is then arbitrary.
    That surfaces as authentication failures against the *other* app's token
    rather than as a bind error, which is a long way from the real cause.
    """
    allow_reuse_address = False


class DiagBridge:
    """Read/inspect surface over a live Replyit instance."""

    SYNTH_PREFIX = "<diag-synth-"

    def __init__(self, app, port=None, app_module=None):
        self.app = app
        # The bridge needs a couple of module-level app helpers. Normally
        # replypilot.pyw is __main__ and the lookup finds it; an embedded or
        # test run can pass the module explicitly rather than relying on a
        # name that may not be registered.
        self.app_module = app_module
        self.token = secrets.token_urlsafe(24)
        self.started_at = _now()
        self.port = int(port or os.environ.get("REPLYIT_DIAG_PORT")
                        or DEFAULT_PORT)
        self.httpd = None
        self.thread = None
        self.routes_get = {
            "/": self.ep_index,
            "/state": self.ep_state,
            "/config": self.ep_config,
            "/stats": self.ep_stats,
            "/pending": self.ep_pending,
            "/decided": self.ep_decided,
            "/email": self.ep_email,
            "/autosend": self.ep_autosend,
            "/learn": self.ep_learn,
            "/opslog": self.ep_opslog,
        }
        self.routes_post = {
            "/classify": self.ep_classify,
            "/draft": self.ep_draft,
            "/inject": self.ep_inject,
            "/reclassify": self.ep_reclassify,
            "/decide": self.ep_decide,
            "/set_needs_input": self.ep_set_needs_input,
            "/purge_synthetic": self.ep_purge_synthetic,
        }

    def _app_title(self):
        mod = self.app_module
        if mod is not None and hasattr(mod, "APP_TITLE"):
            return mod.APP_TITLE
        return _app_title()

    # --------------------------------------------------------- lifecycle
    def start(self):
        last = None
        for offset in range(0, 10):     # port may be taken by a stale run
            try:
                httpd = _Server(("127.0.0.1", self.port + offset),
                                _Handler)
            except OSError as e:
                last = e
                continue
            httpd.bridge = self
            self.httpd = httpd
            self.port = self.port + offset
            break
        if self.httpd is None:
            raise RuntimeError("no free loopback port for the diag bridge: %s"
                               % last)
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()
        self._write_token_file()
        return self

    def stop(self):
        try:
            if self.httpd:
                self.httpd.shutdown()
        except Exception:
            pass
        self._remove_token_file()

    def token_path(self):
        return os.path.join(self.app.store.dir, TOKEN_FILE)

    def _write_token_file(self):
        try:
            with open(self.token_path(), "w", encoding="utf-8") as f:
                json.dump({"token": self.token, "port": self.port,
                           "pid": os.getpid(), "started_at": self.started_at,
                           "base_url": self.base_url()}, f, indent=2)
        except OSError:
            pass

    def _remove_token_file(self):
        try:
            os.remove(self.token_path())
        except OSError:
            pass

    def base_url(self):
        return "http://127.0.0.1:%d" % self.port

    # ------------------------------------------------- main-thread bridge
    def on_main(self, fn, timeout=MAIN_THREAD_TIMEOUT):
        """Run fn on the Tk main thread and wait for its result.

        Everything that mutates app state goes through here. Tk is not
        thread-safe, and a widget touched from the HTTP thread corrupts
        state silently rather than raising, so the failure would surface
        later as something unrelated.
        """
        box, done = {}, threading.Event()

        def runner():
            try:
                box["r"] = fn()
            except Exception as e:
                box["e"] = "%s: %s" % (e.__class__.__name__, e)
                box["trace"] = traceback.format_exc()
            finally:
                done.set()
        try:
            self.app.root.after(0, runner)
        except Exception as e:
            return {"error": "cannot reach main thread: %s" % e}
        if not done.wait(timeout):
            return {"error": "timed out waiting for the main thread",
                    "timeout_sec": timeout}
        if "e" in box:
            return {"error": box["e"], "trace": box.get("trace")}
        return box.get("r")

    # ------------------------------------------------------------ GET eps
    def ep_index(self, q):
        return {"app": "replyit", "bridge": BRIDGE_VERSION,
                "app_version": self._app_title(),
                "base_url": self.base_url(), "started_at": self.started_at,
                "GET": sorted(self.routes_get),
                "POST": sorted(self.routes_post),
                "auth": "X-Diag-Token header, or ?token=",
                "token_file": self.token_path()}

    def ep_state(self, q):
        st = self.app.store
        pending = st.pending()
        clf = _clf()
        app_mod = self.app_module or _appmod()
        qrows, irows, nrows, airows = app_mod.partition_pending(
            pending, getattr(self.app, "ai_queue", {}))
        counts = {}
        for a in ("accepted", "recategorized", "edited", "declined",
                  "moved_no_reply", "auto_sent", "deleted"):
            counts[a] = len(st.by_action(a))
        return {
            "app_version": self._app_title(),
            "ts": _now(),
            "busy": bool(getattr(self.app, "busy", False)),
            "data_dir": st.dir,
            "tabs": {"queue": len(qrows), "needs_input": len(irows),
                     "ai_review_queue": len(airows),
                     "no_reply": len(nrows) + counts["moved_no_reply"],
                     "deleted": counts["deleted"],
                     "decided": sum(counts[a] for a in
                                    ("accepted", "recategorized", "edited",
                                     "declined", "auto_sent"))},
            "actions": counts,
            "pending_total": len(pending),
            "needs_input_total": sum(1 for r in pending
                                     if r.get("needs_input")),
            "ai_queue": dict(getattr(self.app, "ai_queue", {})),
            "origin": st.origin_counts(),
            "categories": list(clf.CATEGORIES),
        }

    def ep_config(self, q):
        clf = _clf()
        s = dict(getattr(self.app, "settings", {}) or {})
        # the signature is the user's own contact block — summarize, don't emit
        s["signature"] = "<%d chars>" % len(s.get("signature") or "")
        return {"ai": clf.ai_settings_defaults(),
                "endpoints": [{"label": l, "host": h, "port": p, "model": m}
                              for l, h, p, m in clf._endpoints()],
                "no_llm": clf.NO_LLM,
                "settings": s}

    def ep_stats(self, q):
        st = self.app.store
        return {"categories": st.category_stats(),
                "origin": st.origin_counts(),
                "graduation": {"min_samples": _rec().GRADUATION_MIN_SAMPLES,
                               "min_agreement":
                                   _rec().GRADUATION_MIN_AGREEMENT}}

    def _rows(self, rows, q, fields=None):
        limit = int((q.get("limit") or ["50"])[0])
        full = (q.get("full") or ["0"])[0] == "1"
        out = []
        for r in rows[:max(0, limit)]:
            d = dict(r)
            if not full:
                d.pop("body_full", None)
                if d.get("body_preview"):
                    d["body_preview"] = d["body_preview"][:200]
            if fields:
                d = {k: d.get(k) for k in fields}
            out.append(d)
        return out

    def ep_pending(self, q):
        rows = self.app.store.pending()
        cat = (q.get("category") or [""])[0]
        if cat:
            rows = [r for r in rows if r.get("ai_category") == cat]
        maxconf = (q.get("max_conf") or [""])[0]
        if maxconf:
            try:
                cap = float(maxconf)
                rows = [r for r in rows
                        if (r.get("ai_confidence") or 0) <= cap]
            except ValueError:
                pass
        return {"count": len(rows), "rows": self._rows(rows, q)}

    def ep_decided(self, q):
        st = self.app.store
        action = (q.get("action") or [""])[0]
        acts = [action] if action else list(_rec().DECIDED_ACTIONS)
        rows = []
        for a in acts:
            rows.extend(st.by_action(a))
        rows.sort(key=lambda r: r.get("decided_at") or "", reverse=True)
        return {"count": len(rows), "rows": self._rows(rows, q)}

    def ep_email(self, q):
        mid = (q.get("id") or [""])[0]
        if not mid:
            return {"error": "pass ?id=<message_id>"}
        row = self.app.store.get(mid)
        return row or {"error": "not found", "id": mid}

    def ep_autosend(self, q):
        eng = getattr(self.app, "auto", None)
        if eng is None:
            return {"error": "auto engine not attached"}
        elig = eng.eligible_rows()
        return {"master_on": eng.master_on(),
                "delay_sec": eng.delay_sec(),
                "min_conf": eng.min_conf(),
                "never_auto": list(_auto().NEVER_AUTO),
                "scheduled": {k: round(v - time.time(), 1)
                              for k, v in eng.scheduled.items()},
                "eligible_now": [r["message_id"] for r in elig],
                "eligible_count": len(elig),
                "sent_this_session": len(eng.sent_log)}

    def ep_learn(self, q):
        ls = getattr(self.app, "learn", None)
        if ls is None:
            return {"error": "learning store not attached"}
        le = _learn()
        return {"counts": ls.counts(),
                "staged_sample": [
                    {k: r.get(k) for k in
                     ("stage_id", "ai_category", "ai_confidence",
                      "ai_source", "reply_text", "orig_from_email")}
                    for r in ls.by_status(le.STATUS_STAGED)[:20]]}

    def ep_opslog(self, q):
        path = self.app.store.audit_path
        n = int((q.get("lines") or ["100"])[0])
        event = (q.get("event") or [""])[0]
        if not os.path.exists(path):
            return {"path": path, "lines": []}
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.readlines()
        except OSError as e:
            return {"error": str(e)}
        out = []
        for line in raw[-max(1, n) * 4:]:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if event and obj.get("event") != event:
                continue
            out.append(obj)
        return {"path": path, "count": len(out[-n:]), "lines": out[-n:]}

    # ----------------------------------------------------------- POST eps
    def ep_classify(self, body, q):
        """Classify supplied text WITHOUT storing anything.

        This is the accuracy-assessment endpoint: a batch of subject/body
        pairs can be scored against expected categories without writing to
        the corpus, so testing can't pollute the very data it measures.
        """
        clf, drafts = _clf(), _draftmod()
        items = body.get("emails")
        if items is None:
            items = [body]
        out = []
        for it in items:
            subj = it.get("subject", "")
            sender = it.get("sender", "")
            text = it.get("body", "")
            res = clf.classify(subj, sender, text)
            row = {"subject": subj, "sender": sender,
                   "category": res["category"],
                   "confidence": res["confidence"],
                   "needs_reply": res["needs_reply"],
                   "needs_input": res.get("needs_input"),
                   "source": res["source"]}
            if it.get("expect"):
                row["expect"] = it["expect"]
                row["match"] = (res["category"] == it["expect"])
            if body.get("features"):
                row["features"] = res["features"]
            if body.get("with_draft"):
                d, dsrc = drafts.make_draft(
                    res["category"], sender.split("@")[0], sender, subj,
                    text, getattr(self.app, "settings", {}))
                row["draft"] = d
                row["draft_source"] = dsrc
            out.append(row)
        summary = {"n": len(out)}
        scored = [r for r in out if "match" in r]
        if scored:
            summary["scored"] = len(scored)
            summary["correct"] = sum(1 for r in scored if r["match"])
            summary["accuracy"] = round(
                summary["correct"] / len(scored), 4)
            summary["misses"] = [
                {"subject": r["subject"][:70], "got": r["category"],
                 "expected": r["expect"], "confidence": r["confidence"]}
                for r in scored if not r["match"]]
        return {"summary": summary, "results": out}

    def ep_draft(self, body, q):
        drafts = _draftmod()
        cat = body.get("category")
        if not cat:
            return {"error": "pass category"}
        text, src = drafts.make_draft(
            cat, body.get("sender_name", ""), body.get("sender", ""),
            body.get("subject", ""), body.get("body", ""),
            getattr(self.app, "settings", {}))
        return {"category": cat, "draft": text, "source": src}

    def ep_inject(self, body, q):
        """Push synthetic emails through the REAL pipeline.

        Runs on the Tk main thread and never touches Outlook COM, so the
        live intake path — classify, draft, store, refresh — is exercised
        exactly as a scan would exercise it. Injected ids carry a marker so
        purge_synthetic can remove them cleanly afterwards.
        """
        clf, drafts = _clf(), _draftmod()
        items = body.get("emails") or [body]

        def work():
            made = []
            for it in items:
                subj = it.get("subject", "(diag)")
                sender = it.get("sender", "diag@example.com")
                text = it.get("body", "")
                mid = it.get("message_id") or "%s%s@diag>" % (
                    self.SYNTH_PREFIX, secrets.token_hex(6))
                res = clf.classify(subj, sender, text)
                draft, dsrc = "", "template"
                if res["needs_reply"] and \
                        res["category"] != clf.CAT_NO_REPLY:
                    draft, dsrc = drafts.make_draft(
                        res["category"], sender.split("@")[0], sender,
                        subj, text, self.app.settings)
                ok = self.app.store.upsert_intake(
                    mid, it.get("received_at") or _now(), subj, sender,
                    res["features"], res["needs_reply"], res["category"],
                    res["confidence"], draft,
                    "diag/%s/%s" % (res["source"], dsrc), text,
                    needs_input=res.get("needs_input", False))
                made.append({"message_id": mid, "inserted": bool(ok),
                             "category": res["category"],
                             "confidence": res["confidence"],
                             "needs_input": res.get("needs_input")})
            self.app._refresh_lists()
            return made
        result = self.on_main(work)
        if isinstance(result, dict) and "error" in result:
            return result
        return {"injected": result, "count": len(result or [])}

    def ep_reclassify(self, body, q):
        """Re-run classification over pending rows, synchronously, without
        the confirmation dialog. Pending only — decided rows are corpus."""
        clf, drafts = _clf(), _draftmod()

        def work():
            rows = [dict(r) for r in self.app.store.pending()]
            changed, same, moves = 0, 0, []
            for row in rows:
                res = clf.classify(row.get("subject", ""),
                                   row.get("sender", ""),
                                   row.get("body_full") or "")
                draft, dsrc = "", "template"
                if res["needs_reply"] and \
                        res["category"] != clf.CAT_NO_REPLY:
                    draft, dsrc = drafts.make_draft(
                        res["category"], row.get("sender", "").split("@")[0],
                        row.get("sender", ""), row.get("subject", ""),
                        row.get("body_full") or "", self.app.settings)
                ok = self.app.store.reclassify_pending(
                    row["message_id"], res["category"], res["confidence"],
                    draft, "%s/%s" % (res["source"], dsrc),
                    needs_reply=res["needs_reply"],
                    needs_input=res.get("needs_input", False))
                if not ok:
                    continue
                if res["category"] != row.get("ai_category"):
                    changed += 1
                    moves.append({"message_id": row["message_id"],
                                  "subject": (row.get("subject") or "")[:70],
                                  "from": row.get("ai_category"),
                                  "to": res["category"]})
                else:
                    same += 1
            self.app._refresh_lists()
            return {"changed": changed, "unchanged": same, "moves": moves}
        return self.on_main(work, timeout=180)

    def ep_decide(self, body, q):
        """Record a decision — for scripted end-to-end checks. Never sends."""
        mid = body.get("message_id")
        action = body.get("action")
        if not mid or not action:
            return {"error": "pass message_id and action"}
        if action not in _rec().DECIDED_ACTIONS:
            return {"error": "unknown action", "valid":
                    list(_rec().DECIDED_ACTIONS)}

        def work():
            ok = self.app.store.record_decision(
                mid, action, body.get("category"), body.get("draft"))
            self.app.ai_queue.pop(mid, None)
            self.app._refresh_lists()
            return {"ok": bool(ok), "message_id": mid, "action": action}
        return self.on_main(work)

    def ep_set_needs_input(self, body, q):
        mid = body.get("message_id")
        if not mid:
            return {"error": "pass message_id"}
        flag = bool(body.get("flag", True))

        def work():
            self.app.store.set_needs_input(mid, flag)
            self.app._refresh_lists()
            return {"ok": True, "message_id": mid, "needs_input": flag}
        return self.on_main(work)

    def ep_purge_synthetic(self, body, q):
        """Remove every record this bridge injected. Real mail is untouched
        because only ids carrying the synthetic marker are eligible."""
        def work():
            st = self.app.store
            ids = [m for m in st.known_ids()
                   if m.startswith(self.SYNTH_PREFIX)]
            n = st.purge(ids)
            for m in ids:
                self.app.ai_queue.pop(m, None)
            self.app._refresh_lists()
            return {"purged": n, "ids": ids}
        return self.on_main(work)


# --------------------------------------------------------- module helpers
# Imported lazily so the bridge never becomes an import-time dependency of
# the app; a broken bridge must not stop Replyit from starting.
def _clf():
    import replypilot_classify_engine as m
    return m


def _draftmod():
    import replypilot_draft_engine as m
    return m


def _rec():
    import replypilot_record_engine as m
    return m


def _auto():
    import replypilot_auto_engine as m
    return m


def _learn():
    import replypilot_learn_engine as m
    return m


def _appmod():
    """Find the running app module.

    Normally replypilot.pyw is __main__, but it can be loaded under another
    name (a harness, an embedded run), so fall back to scanning for the
    module that actually exposes the app API rather than trusting a name.
    """
    import sys
    for name in ("replyit_app", "replypilot", "__main__"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "partition_pending"):
            return mod
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        if hasattr(mod, "partition_pending") and hasattr(mod, "APP_TITLE"):
            return mod
    raise RuntimeError("app module not importable from the bridge")


def _app_title():
    try:
        return _appmod().APP_TITLE
    except Exception:
        return "unknown"


def start(app, port=None, app_module=None):
    """Start the bridge for `app`. Returns the DiagBridge, or None if the
    environment switch is off."""
    global _bridge
    if not enabled():
        return None
    if _bridge is not None:
        return _bridge
    _bridge = DiagBridge(app, port=port, app_module=app_module).start()
    return _bridge


def stop():
    global _bridge
    if _bridge is not None:
        _bridge.stop()
        _bridge = None
