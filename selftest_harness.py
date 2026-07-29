# selftest_harness.py — ReplyPilot v1.0.0 end-to-end harness (no LLM, no COM)
import os, sys, json, re, tempfile
os.environ["REPLYPILOT_NO_LLM"] = "1"

import replypilot_mail_engine as mail
import replypilot_classify_engine as clf
import replypilot_draft_engine as drafts
import replypilot_record_engine as rec

FAILS = []
SKIPPED = []
def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label + ((" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILS.append(label)

EMLS = {
"rfq_parts.eml": b"""From: Mike Torres <mtorres@abcelectric.com>\r
To: sales@americanpower.com\r
Subject: RFQ - breakers needed\r
Date: Mon, 20 Jul 2026 09:15:00 -0400\r
Message-ID: <rfq123@abcelectric.com>\r
Content-Type: text/plain\r
\r
Good morning,\r
Please quote the following:\r
(3) QO2100 breakers\r
(2) TQD22200 qty 2\r
Need pricing and lead time.\r
Thanks, Mike\r
""",
"noreply_ship.eml": b"""From: UPS Notifications <noreply@ups.com>\r
To: sales@americanpower.com\r
Subject: Shipment Notification - Tracking Number 1Z999\r
Date: Mon, 20 Jul 2026 10:00:00 -0400\r
Message-ID: <ship1@ups.com>\r
Content-Type: text/plain\r
\r
Your package has shipped. This is an automated message, do not reply.\r
""",
"thanks.eml": b"""From: Dana Reyes <dana@metrocontractors.com>\r
To: sales@americanpower.com\r
Subject: RE: your quote\r
Date: Mon, 20 Jul 2026 11:00:00 -0400\r
Message-ID: <thx9@metro.com>\r
Content-Type: text/plain\r
\r
Thanks!\r
""",
"vague_quote.eml": b"""From: Joe P <joep@northsideelec.com>\r
To: sales@americanpower.com\r
Subject: pricing\r
Date: Mon, 20 Jul 2026 12:00:00 -0400\r
Message-ID: <vq77@northside.com>\r
Content-Type: text/plain\r
\r
Hey can you give me pricing on some panelboards and gear? Not sure exactly what yet.\r
""",
"job_bid.eml": b"""From: Estimating <estimating@bigbuild.com>\r
To: sales@americanpower.com\r
Subject: Quote request - school project\r
Date: Mon, 20 Jul 2026 13:00:00 -0400\r
Message-ID: <bid55@bigbuild.com>\r
Content-Type: text/plain\r
\r
We are bidding a school job, bid date is next Friday. Please quote per plans and specs, spec section 260000.\r
""",
"angry.eml": b"""From: Bill K <bill@kappaelectric.com>\r
To: sales@americanpower.com\r
Subject: wrong material\r
Date: Mon, 20 Jul 2026 14:00:00 -0400\r
Message-ID: <angry3@kappa.com>\r
Content-Type: text/html\r
\r
<html><body><p>The wrong parts were shipped on our PO 4471. This is unacceptable, we need a refund.</p></body></html>\r
""",
"no_msgid.eml": b"""From: someone@x.com\r
To: sales@americanpower.com\r
Subject: quick question\r
Date: Mon, 20 Jul 2026 15:00:00 -0400\r
Content-Type: text/plain\r
\r
Do you stock Square D QO2100? Need qty 5.\r
""",
}

tmp = tempfile.mkdtemp(prefix="rp_eml_")
for name, raw in EMLS.items():
    with open(os.path.join(tmp, name), "wb") as f:
        f.write(raw)

# ---- 1. eml parsing ---------------------------------------------------------
items = mail.scan_eml_folder(tmp)
check("folder scan finds 7 emls", len(items) == 7, len(items))
by_subj = {i["subject"]: i for i in items}
rfq = by_subj["RFQ - breakers needed"]
check("plain body extracted", "QO2100" in rfq["body"])
check("message-id parsed", rfq["message_id"] == "<rfq123@abcelectric.com>", rfq["message_id"])
check("sender name parsed", rfq["sender_name"] == "Mike Torres", rfq["sender_name"])
angry = by_subj["wrong material"]
check("html stripped to text", "wrong parts were shipped" in angry["body"] and "<p>" not in angry["body"], angry["body"][:80])
nomid = by_subj["quick question"]
check("missing message-id is empty", nomid["message_id"] == "")
fb = rec.RecordStore.fallback_message_id(nomid["sender"], nomid["subject"], nomid["received_at"], nomid["body"])
check("fallback id deterministic", fb == rec.RecordStore.fallback_message_id(nomid["sender"], nomid["subject"], nomid["received_at"], nomid["body"]))

# ---- 2. heuristic classification -------------------------------------------
def cat_of(item):
    return clf.classify(item["subject"], item["sender"], item["body"])

r = cat_of(rfq)
check("rfq with parts -> quote_ack, needs reply", r["category"] == clf.CAT_QUOTE_ACK and r["needs_reply"], r)
check("classifier source is heuristic under NO_LLM", r["source"] == "heuristic", r["source"])
r = cat_of(by_subj["Shipment Notification - Tracking Number 1Z999"])
check("shipping notice -> no_reply", r["category"] == clf.CAT_NO_REPLY and not r["needs_reply"], r)
r = cat_of(by_subj["RE: your quote"])
check("bare thanks -> no_reply", r["category"] == clf.CAT_NO_REPLY, r)
r = cat_of(by_subj["pricing"])
check("vague pricing -> need_info", r["category"] == clf.CAT_NEED_INFO, r)
r = cat_of(by_subj["Quote request - school project"])
check("bid/plans-specs no parts -> job_name", r["category"] == clf.CAT_JOB_NAME, r)
r = cat_of(angry)
check("wrong-material complaint -> escalate", r["category"] == clf.CAT_ESCALATE, r)
r = cat_of(nomid)
check("part+qty question -> quote_ack", r["category"] == clf.CAT_QUOTE_ACK, r)

# ---- 3. drafts --------------------------------------------------------------
sdir = tempfile.mkdtemp(prefix="rp_data_")
settings = drafts.load_settings(sdir)
check("settings created with defaults", settings["use_llm_polish"] is False and "Berson" in settings["signature"])
d, src = drafts.make_draft(clf.CAT_QUOTE_ACK, "Mike Torres", "mtorres@abcelectric.com", settings=settings)
# assert the property (addressed to Mike by first name), not the literal
# greeting — the opener is the user's measured style and may change with it
check("quote_ack draft greets by first name", d.splitlines()[0].endswith("Mike,"), d[:30])
check("draft carries signature", settings["signature"] in d)
check("draft source is template", src == "template")
d, _ = drafts.make_draft(clf.CAT_JOB_NAME, "", "estimating@bigbuild.com", settings=settings)
check("job_name draft asks which job it is for", "job" in d.lower() and "?" in d, d[:60])
d, _ = drafts.make_draft(clf.CAT_NO_REPLY, "Mike", "m@x.com", settings=settings)
check("no_reply draft is empty", d == "")
d, _ = drafts.make_draft(clf.CAT_QUOTE_ACK, "Torres, Mike", "mtorres@abcelectric.com", settings=settings)
check("Last,First name handled", d.splitlines()[0].endswith("Mike,"), d[:30])
# the templates ARE the voice whenever polish is off or rejected, so hold them
# to the measured shape: one or two short sentences, never house-style filler
for _c in clf.REPLY_CATEGORIES:
    _t, _ = drafts.make_draft(_c, "Mike", "m@x.com", settings=settings)
    _body = _t.replace(settings["signature"], "").strip()
    _bodyline = " ".join(_body.splitlines()[1:]).strip()
    check("%s template stays terse" % _c, len(_bodyline.split()) <= 22,
          "%d words: %s" % (len(_bodyline.split()), _bodyline[:70]))
    check("%s template has no corporate filler" % _c,
          not re.search(r"(best regards|kind regards|don't hesitate|"
                        r"at your earliest convenience|please be advised)",
                        _t, re.I), _t[:70])

# ---- 4. record store: intake, decisions, idempotency ------------------------
store = rec.RecordStore(directory=sdir)
def intake(item):
    res = cat_of(item)
    mid = item["message_id"] or rec.RecordStore.fallback_message_id(item["sender"], item["subject"], item["received_at"], item["body"])
    draft_text = ""
    if res["needs_reply"] and res["category"] != clf.CAT_NO_REPLY:
        draft_text, _ = drafts.make_draft(res["category"], item["sender_name"], item["sender"], item["subject"], item["body"], settings)
    ok = store.upsert_intake(mid, item["received_at"], item["subject"], item["sender"], res["features"], res["needs_reply"], res["category"], res["confidence"], draft_text, res["source"], item["body"])
    return mid, ok

mids = {}
for name, item in by_subj.items():
    mid, ok = intake(item)
    mids[name] = mid
    check("intake inserted: %s" % name[:24], ok)
_, ok2 = intake(rfq)
check("re-intake same message-id rejected (idempotent)", ok2 is False)
check("pending count is 7", len(store.pending()) == 7, len(store.pending()))

# accept the RFQ as-is -> unchanged
store.record_decision(mids["RFQ - breakers needed"], rec.ACTION_ACCEPTED)
row = store.get(mids["RFQ - breakers needed"])
check("accepted decision persisted", row["user_action"] == rec.ACTION_ACCEPTED and row["changed_by_user"] == 0)
check("final category defaulted to ai category", row["final_category"] == clf.CAT_QUOTE_ACK)

# recategorize the vague one -> changed
store.record_decision(mids["pricing"], rec.ACTION_RECATEGORIZED, clf.CAT_NO_QUOTE, "custom text")
row = store.get(mids["pricing"])
check("recategorize marks changed_by_user", row["changed_by_user"] == 1 and row["final_category"] == clf.CAT_NO_QUOTE)

# move thanks email decision + undo path
store.record_decision(mids["RE: your quote"], rec.ACTION_MOVED_NO_REPLY, clf.CAT_NO_REPLY, "")
store.reopen(mids["RE: your quote"], new_ai_category=clf.CAT_QUOTE_ACK, new_ai_draft="hello", new_needs_reply=True, ai_source="user_undo")
row = store.get(mids["RE: your quote"])
check("undo returns item to pending with new classification", row["user_action"] == rec.ACTION_PENDING and row["ai_category"] == clf.CAT_QUOTE_ACK and row["ai_needs_reply"] == 1)

# ---- 5. graduation math -----------------------------------------------------
for i in range(60):
    mid = "<grad%d@test>" % i
    store.upsert_intake(mid, "2026-07-20T00:00:00+00:00", "RFQ %d" % i, "c%d@x.com" % i, {}, True, clf.CAT_QUOTE_ACK, 0.8, "draft", "heuristic", "body")
    # 58 accepted, 2 recategorized -> 58/60 = 96.7% >= 95%, n=60 >= 50
    if i < 58:
        store.record_decision(mid, rec.ACTION_ACCEPTED)
    else:
        store.record_decision(mid, rec.ACTION_RECATEGORIZED, clf.CAT_NEED_INFO, "x")
stats = store.category_stats()
qa = stats[clf.CAT_QUOTE_ACK]
check("quote_ack samples counted (60 synth + 1 real)", qa["samples"] == 61, qa)
check("agreement ~96.7%%", abs(qa["agreement"] - (59/61)) < 0.001, qa["agreement"])
check("quote_ack graduated", qa["graduated"] is True and qa["auto_send"] is True)
nq = stats.get(clf.CAT_NO_QUOTE)
check("no_quote absent or not graduated", nq is None or not nq["graduated"])

# manual override off
store.set_auto_send_override(clf.CAT_QUOTE_ACK, False)
check("manual override disables auto_send despite graduation", store.auto_send_enabled(clf.CAT_QUOTE_ACK) is False)
check("graduated flag itself unaffected by override", store.category_stats()[clf.CAT_QUOTE_ACK]["graduated"] is True)

# ---- 6. export + audit ------------------------------------------------------
out = os.path.join(sdir, "corpus.jsonl")
n = store.export_training_jsonl(out)
check("export writes decided records", n >= 62, n)
with open(out) as f:
    first = json.loads(f.readline())
check("corpus rows drop body_full", "body_full" not in first and "message_id" in first)
with open(store.audit_path) as f:
    lines = f.readlines()
check("audit log is compact one-record-per-line json", all(l.strip().startswith("{") and "\n" not in l.strip() for l in lines[:5]) and len(lines) > 60, len(lines))

# ---- 7. deleted action + purge + undo --------------------------------------
mid_del = "<del_test@test>"
store.upsert_intake(mid_del, "2026-07-21T10:00:00+00:00", "FYI only", "fyi@x.com",
                    {}, False, clf.CAT_NO_REPLY, 0.9, "", "heuristic", "just fyi")
store.record_decision(mid_del, rec.ACTION_DELETED,
                      final_category=clf.CAT_NO_REPLY, final_draft="")
row = store.get(mid_del)
check("deleted action persisted", row["user_action"] == rec.ACTION_DELETED)
check("deleted counts as unchanged in stats",
      store.category_stats().get(clf.CAT_NO_REPLY, {}).get("unchanged", 0) >= 1)
# undo delete
store.reopen(mid_del)
row = store.get(mid_del)
check("undo delete returns to pending", row["user_action"] == rec.ACTION_PENDING)
# purge
mid_purge = "<purge_me@test>"
store.upsert_intake(mid_purge, "2026-07-21T11:00:00+00:00", "spam", "sp@x.com",
                    {}, False, clf.CAT_NO_REPLY, 0.9, "", "heuristic", "spam")
store.record_decision(mid_purge, rec.ACTION_DELETED,
                      final_category=clf.CAT_NO_REPLY, final_draft="")
n_purged = store.purge([mid_purge])
check("purge removes 1 record", n_purged == 1)
check("purged record gone from store", store.get(mid_purge) is None)

# ---- 9. eml draft fallback --------------------------------------------------
p = mail.write_eml_draft(os.path.join(sdir, "outbox"), "mtorres@abcelectric.com", "RFQ - breakers needed", "Hi Mike,\n\nGot it.\n\nSteve", in_reply_to="<rfq123@abcelectric.com>")
check("eml draft written", os.path.exists(p))
import email as _em
with open(p, "rb") as f:
    m = _em.message_from_bytes(f.read())
check("draft has RE: subject + In-Reply-To", m["Subject"].startswith("RE:") and m["In-Reply-To"] == "<rfq123@abcelectric.com>")

# ---- 11. v1.2.0: acknowledgement category ----------------------------------
victor_body = """Hello Steve,

Please find Caddy P/A below and let us know if you have any questions or feedback.

512HD    Heavy Duty T-Grid Box Hanger, Mounting Clip
$902.34/c
No Stock  approximately 3-4 weeks lead time
must purchase in (increments of 25)

CAT1224SM   nVent CADDY Cablecat J-Hook
$346.73/c
Stock OH  must purchase in (increments of 40)
"""
r = clf.classify("Re: FW: P&A", "victor@brazill.com", victor_body)
check("vendor P&A delivery -> acknowledgement", r["category"] == clf.CAT_ACK and r["needs_reply"], r)
check("ack features: delivery phrase + prices", r["features"]["delivery_phrase"] and r["features"]["price_count"] >= 2, r["features"])
# RFQ must still classify as quote_ack (no delivery phrase, no prices)
r = cat_of(rfq)
check("customer RFQ still quote_ack after ack rule", r["category"] == clf.CAT_QUOTE_ACK, r)
# prices alone without delivery phrase must NOT fire ack (two-signal rule)
r = clf.classify("invoice question", "cust@x.com", "Why was I charged $500.00 on the last order? Please quote me a better price.")
check("prices without delivery phrase never ack", r["category"] != clf.CAT_ACK, r)
# ack template
d, _ = drafts.make_draft(clf.CAT_ACK, "Victor", "victor@brazill.com", settings=settings)
check("ack draft says thank you", "Thank you" in d and d.splitlines()[0].endswith("Victor,"), d[:60])
check("ack in REPLY_CATEGORIES for UI radios", clf.CAT_ACK in clf.REPLY_CATEGORIES)

# ---- 12. v1.2.0: update_ai_draft (AI Review) --------------------------------
mid_rev = "<review_me@test>"
store.upsert_intake(mid_rev, "2026-07-24T15:00:00+00:00", "Re: FW: P&A", "victor@brazill.com",
                    {}, True, clf.CAT_ACK, 0.7, "original draft", "heuristic", victor_body)
ok = store.update_ai_draft(mid_rev, "tailored draft", "ai_review")
row = store.get(mid_rev)
check("ai review updates pending draft", ok and row["ai_draft"] == "tailored draft")
check("ai review appends source note once", row["ai_source"] == "heuristic+ai_review")
ok2 = store.update_ai_draft(mid_rev, "tailored again", "ai_review")
check("source note not duplicated on second pass", store.get(mid_rev)["ai_source"] == "heuristic+ai_review")
store.record_decision(mid_rev, rec.ACTION_ACCEPTED)
check("ai review refuses to touch decided records", store.update_ai_draft(mid_rev, "nope") is False)
check("decided draft unchanged", store.get(mid_rev)["ai_draft"] == "tailored again")

# ---- 13. v1.2.0: auto-send engine gates -------------------------------------
import replypilot_auto_engine as auto
auto_settings = dict(settings)
# office hours off: these cases are about the other gates, and leaving it
# on made them depend on what time the suite happened to run
auto_settings["office_hours_enabled"] = False
eng = auto.AutoSendEngine(store, auto_settings)
# gate 1: master off -> nothing, even though quote_ack graduated earlier
store.set_auto_send_override(clf.CAT_QUOTE_ACK, True)  # re-enable from sec.5
mid_auto = "<auto_cand@test>"
store.upsert_intake(mid_auto, "2026-07-24T16:00:00+00:00", "RFQ auto", "c@x.com",
                    {}, True, clf.CAT_QUOTE_ACK, 0.95, "the draft", "heuristic", "please quote QO2100 qty 3")
check("master off -> no eligible rows", eng.eligible_rows() == [])
check("master off -> schedule is no-op", eng.evaluate_and_schedule() == [])
# gate 2+: master on
auto_settings["auto_send_master"] = True
auto_settings["auto_send_delay_sec"] = 60
auto_settings["auto_send_min_conf"] = 0.85
elig = eng.eligible_rows()
check("graduated+confident+pending is eligible", any(r["message_id"] == mid_auto for r in elig), [r["message_id"] for r in elig])
# gate: confidence below threshold excluded
mid_lowconf = "<auto_lowconf@test>"
store.upsert_intake(mid_lowconf, "2026-07-24T16:01:00+00:00", "RFQ low", "c2@x.com",
                    {}, True, clf.CAT_QUOTE_ACK, 0.60, "draft", "heuristic", "quote please")
check("low confidence excluded", not any(r["message_id"] == mid_lowconf for r in eng.eligible_rows()))
# gate: escalate never auto even with forced override
store.set_auto_send_override(clf.CAT_ESCALATE, True)
mid_esc = "<auto_esc@test>"
store.upsert_intake(mid_esc, "2026-07-24T16:02:00+00:00", "complaint", "c3@x.com",
                    {}, True, clf.CAT_ESCALATE, 0.99, "draft", "heuristic", "unacceptable")
check("escalate hard-excluded despite override", not any(r["message_id"] == mid_esc for r in eng.eligible_rows()))
# scheduling + delay + cancel + due
t0 = 1000000.0
newly = eng.evaluate_and_schedule(now=t0)
# NOTE: <thx9@metro.com> (reopened in sec.4 as pending quote_ack, 0.85 conf)
# is ALSO legitimately eligible — assertions below are mid-specific.
check("schedules the eligible candidate", mid_auto in newly)
check("re-evaluate does not double-schedule", mid_auto not in eng.evaluate_and_schedule(now=t0 + 1))
check("nothing due before delay elapses", eng.due(now=t0 + 30) == [])
check("countdown reported", eng.next_fire_in(now=t0 + 30) == 30)
check("cancel removes scheduled send", eng.cancel(mid_auto) is True and mid_auto not in eng.scheduled)
check("cancelled item never fires", mid_auto not in eng.due(now=t0 + 120))
# reschedule and let it fire
eng.evaluate_and_schedule(now=t0)
fired = eng.due(now=t0 + 61)
check("due after delay fires the send", mid_auto in fired, fired)
# fire-time re-verification: decide the item during the delay window
eng2 = auto.AutoSendEngine(store, auto_settings)
mid_auto2 = "<auto_cand2@test>"
store.upsert_intake(mid_auto2, "2026-07-24T16:03:00+00:00", "RFQ auto2", "c4@x.com",
                    {}, True, clf.CAT_QUOTE_ACK, 0.95, "the draft", "heuristic", "please quote TQD22200 qty 1")
eng2.evaluate_and_schedule(now=t0)
store.record_decision(mid_auto2, rec.ACTION_ACCEPTED)   # user beat the timer
leftover = [m for m in eng2.due(now=t0 + 120) if m == mid_auto2]
check("decided-during-delay item does not auto-fire", leftover == [])
# master off cancels all
eng.evaluate_and_schedule(now=t0)
auto_settings["auto_send_master"] = False
check("cancel_all clears schedule", eng.cancel_all() >= 0 and eng.pending_count() == 0)
check("master off blocks due processing", eng.due(now=t0 + 999) == [])

# ---- 14. v1.3.0: polish output repair pipeline ------------------------------
sig = settings["signature"]
tpl = "Hi Mike,\n\nThanks for the RFQ.\n\n" + sig
# markdown fences + chatty preamble stripped, sig intact -> accepted
raw = "Here's a personalized version:\n```\nHi Mike,\n\nThanks for the RFQ on the QO breakers.\n\n" + sig + "\n```"
out, reason = drafts._clean_polish_output(raw, tpl, sig)
check("polish: fences+preamble stripped, accepted", reason == "ok" and out.startswith("Hi Mike,") and "```" not in out, (reason, out[:40] if out else out))
# model dropped the signature -> re-appended, not rejected
out, reason = drafts._clean_polish_output("Hi Mike,\n\nThanks for the RFQ on the QO breakers, I'll have pricing shortly.", tpl, sig)
check("polish: missing signature re-appended", reason == "ok" and out.endswith(sig), (reason, out[-40:] if out else out))
# wrapping quotes stripped
out, reason = drafts._clean_polish_output('"Hi Mike,\n\nThanks for the breaker RFQ.\n\n' + sig + '"', tpl, sig)
check("polish: wrapping quotes stripped", reason == "ok" and out.startswith("Hi Mike,"), (reason, out[:20] if out else out))
# runaway output rejected
out, reason = drafts._clean_polish_output("word " * 2000, tpl, sig)
check("polish: runaway length rejected", out is None and reason == "too_long")
# junk rejected
out, reason = drafts._clean_polish_output("ok", tpl, sig)
check("polish: too-short junk rejected", out is None and reason == "too_short")
# polish_draft honest reasons under NO_LLM
out, reason = drafts.polish_draft(tpl, "subj", "body")
check("polish_draft reports no_llm", out is None and reason == "no_llm")
out, reason = drafts.polish_draft("", "subj", "body")
check("polish_draft reports empty_template", out is None and reason in ("no_llm", "empty_template"))
check("ollama_reachable false in sandbox (fast fail)", drafts.ollama_reachable(timeout=1) is False)

# ---- 15. v1.3.0: toolbar layout normalizer ----------------------------------
import importlib.util as _ilu
from importlib.machinery import SourceFileLoader as _SFL
_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "replypilot.pyw")
_loader = _SFL("replyit_app", _path)   # .pyw isn't a known suffix; force it
_spec = _ilu.spec_from_loader("replyit_app", _loader)
_app = _ilu.module_from_spec(_spec)
_loader.exec_module(_app)
ids = tuple(b[0] for b in _app.TOOLBAR_BUTTONS)
# empty/None -> full default layout, all visible
lay = _app.normalize_toolbar_layout(None)
check("layout: default has every manifest button visible", [e["id"] for e in lay] == list(ids) and all(e["visible"] for e in lay))
# saved order preserved, unknown dropped, missing appended
saved = [{"id": "export", "visible": True}, {"id": "bogus_button", "visible": True}, {"id": "ai_review", "visible": False}]
lay = _app.normalize_toolbar_layout(saved)
check("layout: order kept, unknown dropped, missing appended", lay[0]["id"] == "export" and lay[1]["id"] == "ai_review" and "bogus_button" not in [e["id"] for e in lay] and set(e["id"] for e in lay) == set(ids))
check("layout: hidden flag survives", [e for e in lay if e["id"] == "ai_review"][0]["visible"] is False)
# settings can never be hidden
lay = _app.normalize_toolbar_layout([{"id": "settings", "visible": False}])
check("layout: settings forced visible", [e for e in lay if e["id"] == "settings"][0]["visible"] is True)
# dupes collapse
lay = _app.normalize_toolbar_layout([{"id": "stats", "visible": True}, {"id": "stats", "visible": False}])
check("layout: duplicate ids collapse to first", [e["id"] for e in lay].count("stats") == 1 and [e for e in lay if e["id"] == "stats"][0]["visible"] is True)

# ---- 16. v1.4.0: host-first Ollama with local fallback ----------------------
import threading as _thr
from http.server import BaseHTTPRequestHandler, HTTPServer

class _StubOllama(BaseHTTPRequestHandler):
    reply_text = "Hi Mike,\n\nThanks for the RFQ on the breakers.\n\n" + settings["signature"]
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/api/tags":
            self.send_response(200); self.end_headers()
            self.wfile.write(b'{"models":[]}')
        else:
            self.send_response(404); self.end_headers()
    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        self.rfile.read(ln)
        self.send_response(200); self.end_headers()
        import json as _j
        self.wfile.write(_j.dumps({"message": {"content": self.reply_text}}).encode())

def _serve():
    srv = HTTPServer(("127.0.0.1", 0), _StubOllama)
    port = srv.server_address[1]
    t = _thr.Thread(target=srv.serve_forever, daemon=True); t.start()
    return srv, port

# reconfigure the classify engine's endpoints to point at stubs.
# host = an unused dead port; local = the live stub. Simulates tillium down.
srv, live_port = _serve()
clf.NO_LLM = False
clf.OLLAMA_HOST, clf.OLLAMA_PORT = "127.0.0.1", 1        # dead (privileged, refused)
clf.LOCAL_OLLAMA_HOST, clf.LOCAL_OLLAMA_PORT = "127.0.0.1", live_port
clf.LOCAL_OLLAMA_MODEL = "gemma3:27b"

check("host down but local up -> any_endpoint_reachable True", clf.any_endpoint_reachable(timeout=2) is True)
check("active endpoint is local when host down", clf.active_endpoint_label(timeout=2) == "local")
content, label = clf.ollama_call([{"role":"user","content":"hi"}], timeout=3)
check("ollama_call falls back to local", content is not None and label == "local", (label, content[:20] if content else content))
# polish uses the fallback end-to-end
import replypilot_draft_engine as _de2
_de2.NO_LLM = False   # draft engine bound NO_LLM by value at import; the
                      # harness set it True at top-of-file, so clear it here
                      # to exercise the live fallback path
tpl = "Hi Mike,\n\nThanks for the RFQ.\n\n" + settings["signature"]
out, reason = _de2.polish_draft(tpl, "RFQ", "please quote QO2100", settings=settings)
check("polish_draft succeeds via local fallback", reason == "ok" and out.startswith("Hi Mike,"), (reason, out[:20] if out else out))
check("ollama_reachable True when only local up", _de2.ollama_reachable(timeout=2) is True)

# now both down -> honest no_endpoint
clf.LOCAL_OLLAMA_HOST, clf.LOCAL_OLLAMA_PORT = "127.0.0.1", 1
check("both down -> any_endpoint_reachable False", clf.any_endpoint_reachable(timeout=2) is False)
check("both down -> active label None", clf.active_endpoint_label(timeout=2) is None)
content, label = clf.ollama_call([{"role":"user","content":"hi"}], timeout=2)
check("ollama_call reports no_endpoint when both down", content is None and label == "no_endpoint", label)
out, reason = _de2.polish_draft(tpl, "RFQ", "body", settings=settings)
check("polish_draft reports no_endpoint when both down", out is None and reason == "no_endpoint", reason)

# dedup: identical host and local collapses to one endpoint
clf.OLLAMA_HOST, clf.OLLAMA_PORT = "127.0.0.1", live_port
clf.LOCAL_OLLAMA_HOST, clf.LOCAL_OLLAMA_PORT = "127.0.0.1", live_port
check("identical host/local collapses to single endpoint", len(clf._endpoints()) == 1)

srv.shutdown()

# ---- 17. v1.5.0: apply_ai_settings (UI-driven config) -----------------------
# snapshot & restore around mutation
_ai_snapshot = clf.ai_settings_defaults()
clf.apply_ai_settings({
    "ai_host": "10.0.0.5", "ai_port": "9999", "ai_host_model": "llama3:8b",
    "ai_local_host": "127.0.0.1", "ai_local_port": "11434",
    "ai_local_model": "", "ai_timeout": "45", "ai_host_probe": "2",
})
check("apply_ai_settings sets host/port/model", clf.OLLAMA_HOST == "10.0.0.5" and clf.OLLAMA_PORT == 9999 and clf.OLLAMA_MODEL == "llama3:8b")
check("apply_ai_settings blank local model mirrors host", clf.LOCAL_OLLAMA_MODEL == "llama3:8b")
check("apply_ai_settings sets timeout + probe", clf.OLLAMA_TIMEOUT == 45 and clf.OLLAMA_HOST_PROBE == 2)
eps = clf._endpoints()
check("apply_ai_settings reflected in endpoints", eps[0][1] == "10.0.0.5" and eps[0][3] == "llama3:8b")
# blank fields fall back to current (not wiped)
clf.apply_ai_settings({"ai_host": "  ", "ai_port": "", "ai_host_model": ""})
check("blank fields fall back, don't wipe", clf.OLLAMA_HOST == "10.0.0.5" and clf.OLLAMA_PORT == 9999)
# bad port ignored
clf.apply_ai_settings({"ai_port": "notanumber"})
check("non-numeric port ignored", clf.OLLAMA_PORT == 9999)
# explicit local model overrides mirror
clf.apply_ai_settings({"ai_local_model": "phi3:mini"})
check("explicit local model honored", clf.LOCAL_OLLAMA_MODEL == "phi3:mini")
# defaults dict round-trips through AI_SETTINGS_KEYS
d = clf.ai_settings_defaults()
check("ai_settings_defaults covers all keys", set(clf.AI_SETTINGS_KEYS) == set(d.keys()))
clf.apply_ai_settings(_ai_snapshot)   # restore
check("restore returns host to snapshot", clf.OLLAMA_HOST == _ai_snapshot["ai_host"])

# ---- 18. v1.5.0/v1.7.0: window geometry restore/clamp ----------------------
# _geometry_for uses self.settings + self.root.winfo_screen{width,height}.
class _ScreenStub:
    def __init__(self, sw, sh):
        self._sw, self._sh = sw, sh
    def winfo_screenwidth(self):
        return self._sw
    def winfo_screenheight(self):
        return self._sh
class _GeoStub:
    _DEFAULT_GEOMETRY = _app.ReplyPilotApp._DEFAULT_GEOMETRY
    _geometry_for = _app.ReplyPilotApp._geometry_for
    def __init__(self, sw, sh, saved):
        self.root = _ScreenStub(sw, sh)
        self.settings = ({"window_geometry": {"main": saved}} if saved else {})
def _geo(sw, sh, saved):
    return _app.ReplyPilotApp._restore_geometry(_GeoStub(sw, sh, saved))
check("geometry: no saved -> default", _geo(1920, 1080, None) == _app.ReplyPilotApp._DEFAULT_GEOMETRY)
check("geometry: valid restored as-is when on-screen", _geo(1920, 1080, "1000x700+100+100") == "1000x700+100+100")
check("geometry: size clamped to screen", _geo(1280, 720, "3000x2000+0+0") == "1280x720+0+0")
check("geometry: off-screen position clamped back", _geo(1920, 1080, "800x600+5000+5000").endswith("+1820+980"))
check("geometry: negative offset clamped to 0", _geo(1920, 1080, "800x600+-300+-300") == "800x600+0+0")
check("geometry: size-only string preserved", _geo(1920, 1080, "900x650") == "900x650")
check("geometry: garbage -> default", _geo(1920, 1080, "not-a-geometry") == _app.ReplyPilotApp._DEFAULT_GEOMETRY)

# ---- 19. v1.6.0: learning engine (real MaINbox export fixture) --------------
import replypilot_learn_engine as le
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixture_sent_samples.json")
if os.path.exists(FIXTURE):
    sent = le.load_sent_json(FIXTURE)
    check("fixture loads as list of 15", isinstance(sent, list) and len(sent) == 15, len(sent))
    # reply detection
    reps = [r for r in sent if le.looks_like_reply(r)]
    check("reply filter keeps 11 of 15", len(reps) == 11, len(reps))
    check("in_reply_to and RE:/FW: prefix agree on every record",
          all(bool((r.get("in_reply_to") or "").strip()) ==
              bool(re.match(r"^\s*(RE|FW|FWD)\s*:", r.get("subject") or "", re.I))
              for r in sent))
    # body splitting
    rec4 = [r for r in sent if r["subject"].startswith("RE: 1597-013")][0]
    rep, orig = le.split_body(rec4["body"])
    check("split_body extracts typed reply only", rep == "TY for letting me know will do", repr(rep[:60]))
    check("split_body strips signature", "NOW HIRING" not in rep and "Ramland" not in rep)
    check("split_body captures quoted original", "Danny" in orig and "closing at 3pm" in orig)
    h = le.parse_quoted_headers(orig)
    check("quoted headers parse sender email", h["from_email"] == "danny@campbellanddawes.com", h)
    check("quoted headers parse subject", h["subject"] == "1597-013 & 1582-302", h)
    ob = le.original_body_text(orig)
    check("original body excludes header block", "closing at 3pm" in ob and "From:" not in ob.split("\n")[0])
    # selection
    cands, st = le.select_candidates(sent, limit=None)
    check("selection: 9 usable from 15", st["usable"] == 9 and st["not_reply"] == 4 and st["no_reply_text"] == 2, st)
    check("selection: 1 outbound follow-up flagged", st["outbound_followup"] == 1, st)
    check("selection respects limit", len(le.select_candidates(sent, limit=3)[0]) == 3)
    # smart-quote normalization (the bug real data exposed)
    check("normalize_quotes folds U+2019", le.normalize_quotes("don\u2019t") == "don't")
    c_noquote = [c for c in cands if "lighting" in c["reply_text"]][0]
    cat, conf, src = le.infer_category(c_noquote)
    check("curly-apostrophe decline infers no_quote", cat == clf.CAT_NO_QUOTE, (cat, conf))
    # inference on real replies
    def _infer_for(sub):
        c = [x for x in cands if sub in x["reply_text"]][0]
        return le.infer_category(c)[0]
    check("'$15.73 in stock' -> quote_delivered", _infer_for("$15.73") == clf.CAT_QUOTE_DELIVERED)
    check("'your cost is $100' -> quote_delivered", _infer_for("Your cost is $100") == clf.CAT_QUOTE_DELIVERED)
    check("'what job this is for' -> job_name", _infer_for("what job this is for") == clf.CAT_JOB_NAME)
    check("internal colleague thread flagged, not learned",
          le.infer_category([c for c in cands if "should have it over shortly" in c["reply_text"]][0])[2] == "internal_thread")
    check("own domain derived from quoted To: headers (not Exchange DN)",
          st["own_domain"] == "americanpoweresc.com", st.get("own_domain"))
    check("internal count reported", st["internal"] == 1, st)
    check("'TY...will do' -> acknowledgement", _infer_for("TY for letting me know") == clf.CAT_ACK)
    check("thanks-then-question -> need_info (question beats thanks prefix)", _infer_for("Arlington low voltage") == clf.CAT_NEED_INFO)
    check("own-RFQ follow-up flagged, not guessed", le.infer_category([c for c in cands if "forgot an item" in c["reply_text"]][0])[2] == "outbound_followup")

    # ---- staging isolation: the core safety property ----
    lstore = le.LearningStore(store)
    before_stats = store.category_stats()
    before_qd = before_stats.get(clf.CAT_QUOTE_DELIVERED, {}).get("samples", 0)
    new_n, skipped = lstore.stage(cands)
    check("stage inserts all candidates", new_n == 9 and skipped == 0, (new_n, skipped))
    check("re-staging same export is idempotent", lstore.stage(cands) == (0, 9))
    after_stats = store.category_stats()
    check("STAGED ROWS ARE INERT: graduation stats unchanged",
          after_stats.get(clf.CAT_QUOTE_DELIVERED, {}).get("samples", 0) == before_qd)
    check("staged rows counted separately", lstore.counts().get(le.STATUS_STAGED) == 9)
    # ignore is still inert
    staged = lstore.by_status(le.STATUS_STAGED)
    lstore.ignore(staged[0]["stage_id"])
    check("ignored row does not enter corpus",
          store.category_stats().get(clf.CAT_QUOTE_DELIVERED, {}).get("samples", 0) == before_qd)
    check("ignored row leaves staged pool", lstore.counts().get(le.STATUS_STAGED) == 8)
    # confirm promotes
    qd_row = [r for r in lstore.by_status(le.STATUS_STAGED) if r["ai_category"] == clf.CAT_QUOTE_DELIVERED][0]
    ok, action = lstore.confirm(qd_row["stage_id"])
    check("confirm promotes and reports 'confirmed'", ok and action == le.STATUS_CONFIRMED, (ok, action))
    st2 = store.category_stats()
    check("confirmed row NOW counts toward graduation",
          st2.get(clf.CAT_QUOTE_DELIVERED, {}).get("samples", 0) == before_qd + 1)
    check("confirmed row counts as unchanged (AI was right)",
          st2.get(clf.CAT_QUOTE_DELIVERED, {}).get("unchanged", 0) >= 1)
    check("double-confirm is refused", lstore.confirm(qd_row["stage_id"])[0] is False)
    # correct promotes with the user's category and marks changed
    corr_row = [r for r in lstore.by_status(le.STATUS_STAGED) if r["ai_category"] == clf.CAT_NEED_INFO][0]
    ok, action = lstore.confirm(corr_row["stage_id"], final_category=clf.CAT_JOB_NAME)
    check("correct promotes and reports 'corrected'", ok and action == le.STATUS_CORRECTED, (ok, action))
    jn = store.category_stats().get(clf.CAT_NEED_INFO, {})
    check("corrected row counts as CHANGED against inferred category", jn.get("samples", 0) >= 1)
    # origin tracking
    oc = store.origin_counts()
    check("imported records tagged origin='import'", oc.get("import", 0) == 2, oc)
    check("live records still tagged origin='live'", oc.get("live", 0) > 0, oc)
    # phrasing library only draws on promoted rows
    ph = lstore.phrasing_examples(clf.CAT_QUOTE_DELIVERED, limit=5)
    check("phrasing library returns confirmed reply text", len(ph) == 1 and "stock" in ph[0].lower(), ph)
    check("phrasing library ignores un-promoted rows", lstore.phrasing_examples(clf.CAT_ESCALATE, limit=5) == [])
    # unstage_all spares promoted rows
    removed = lstore.unstage_all()
    check("unstage_all clears inert rows only", removed == 7, removed)
    check("promoted rows survive unstage_all",
          lstore.counts().get(le.STATUS_CONFIRMED, 0) == 1 and lstore.counts().get(le.STATUS_CORRECTED, 0) == 1)
    check("corpus keeps imported samples after unstage", store.origin_counts().get("import", 0) == 2)
    lstore.close()
else:
    # The fixture is a real sent-mail export and is deliberately NOT in the
    # repository — it contains live customer/vendor addresses and pricing.
    # Its absence is expected on a clone, so skip rather than fail.
    SKIPPED.append("learning-engine tests (fixture_sent_samples.json absent)")
    print("SKIP  learning-engine tests — fixture_sent_samples.json not present"
          " (expected on a clone; the file holds real mail and is gitignored)")

# ---- 20. v1.6.0: origin column migration on an existing DB -----------------
import sqlite3 as _sq
_mdir = tempfile.mkdtemp(prefix="rp_mig_")
_mdb = os.path.join(_mdir, "replypilot.db")
_c = _sq.connect(_mdb)
_c.executescript("""CREATE TABLE decisions (message_id TEXT PRIMARY KEY, received_at TEXT,
 subject TEXT, sender TEXT, features TEXT, ai_needs_reply INTEGER, ai_category TEXT,
 ai_confidence REAL, ai_draft TEXT, ai_source TEXT, user_action TEXT NOT NULL DEFAULT 'pending',
 final_category TEXT, final_draft TEXT, changed_by_user INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, decided_at TEXT, body_preview TEXT, body_full TEXT);""")
_c.execute("INSERT INTO decisions (message_id, created_at, user_action, ai_category) VALUES ('<old@x>','2026-01-01','accepted','quote_ack')")
_c.commit(); _c.close()
_old = rec.RecordStore(directory=_mdir)
_cols = {r[1] for r in _old._conn.execute("PRAGMA table_info(decisions)")}
check("migration adds origin column to pre-v1.3 DB", "origin" in _cols)
check("migration preserves existing rows", _old.get("<old@x>") is not None)
check("pre-existing rows default to origin='live'", _old.origin_counts().get("live", 0) == 1, _old.origin_counts())
_old.close()


# ---- 21. v1.6.1: retune cases from a real production import ----------------
# Reply texts observed in the live Learning window, with the response type
# each should resolve to. Every one of these was mis-binned by v1.6.0.
PROD_CASES = [
    ("Thanks for the info, attached is your quote. Factory N/S ships from NJ 6/30. TY Steve Berson,", clf.CAT_QUOTE_DELIVERED),
    ("Good afternoon, attached is your quote. Please see the availability notes on the quote.", clf.CAT_QUOTE_DELIVERED),
    ("Sorry about that I added those on and checked everything else. Attached is your revised quote.", clf.CAT_QUOTE_DELIVERED),
    ("I only got that spec sheet, let me get this info for you and will advise. Steve Berson,", clf.CAT_QUOTE_ACK),
    ("Good afternoon, I am working on this for you and will advise Steve Berson,", clf.CAT_QUOTE_ACK),
    ("Good afternoon, The last item is not coming up on the portal. Please see below and advise. Thanks!", clf.CAT_NEED_INFO),
    ("Good afternoon, Not sure if you are still looking for this wire but I found something similar. Please confirm this", clf.CAT_NEED_INFO),
    ("Thanks! Steve Berson,", clf.CAT_ACK),
]
for _txt, _want in PROD_CASES:
    _got, _conf = le.infer_from_reply_text(_txt)
    check("prod: %-18s <- %s" % (_want, _txt[:40]), _got == _want, _got)

# direction/ownership cases must NOT get a confident response label
PROD_REJECT = [
    "Good afternoon, Please take a look at below and see if we can quote. Thanks!",
    "My customer never got back to me but please quote this for me and if it doesn't work",
]
for _txt in PROD_REJECT:
    _got, _conf = le.infer_from_reply_text(_txt)
    check("prod: outbound not labeled <- %s" % _txt[:38],
          _got == clf.CAT_ESCALATE and _conf <= 0.2, (_got, _conf))

# sign-off + greeting stripping (root cause of most prod misses)
check("signoff stripped from reply text",
      le.strip_signoff("Thanks! Steve Berson,") == "Thanks!",
      repr(le.strip_signoff("Thanks! Steve Berson,")))
check("greeting stripped before anchored match",
      le.strip_greeting("Good afternoon, Thanks for the info") == "Thanks for the info",
      repr(le.strip_greeting("Good afternoon, Thanks for the info")))
check("double greeting peeled",
      le.strip_greeting("Good afternoon, Matt, can you match this") == "can you match this",
      repr(le.strip_greeting("Good afternoon, Matt, can you match this")))
check("split_body applies signoff strip",
      le.split_body("Thanks! Steve Berson,\r\n\r\n**NOW HIRING")[0] == "Thanks!",
      repr(le.split_body("Thanks! Steve Berson,\r\n\r\n**NOW HIRING")[0]))
# "will advise" (I will) vs "please advise" (you tell me)
check("'will advise' -> quote_ack", le.infer_from_reply_text("let me check and will advise")[0] == clf.CAT_QUOTE_ACK)
check("'please advise' -> need_info", le.infer_from_reply_text("The item is not on the portal, please advise")[0] == clf.CAT_NEED_INFO)
# long message merely opening with Thanks is not an acknowledgement
_long = "Thanks for that. " + ("The customer needs the revised layout before Friday and we should confirm the run length. " * 3)
check("long message opening with Thanks is not acknowledgement",
      le.infer_from_reply_text(_long)[0] != clf.CAT_ACK)
# own-domain helpers
if os.path.exists(FIXTURE):
    check("derive_own_domain ignores Exchange DN sender field",
          le.derive_own_domain(sent) == "americanpoweresc.com",
          le.derive_own_domain(sent))
check("looks_internal matches on own domain",
      le.looks_internal({"orig_from_email": "matt@americanpoweresc.com", "to_addr": ""}, "americanpoweresc.com") is True)
check("looks_internal false for customer domain",
      le.looks_internal({"orig_from_email": "bob@customer.com", "to_addr": ""}, "americanpoweresc.com") is False)
check("looks_internal inert without a known domain",
      le.looks_internal({"orig_from_email": "matt@americanpoweresc.com", "to_addr": ""}, "") is False)


# ---- 22. v1.7.0: second production retune ----------------------------------
PROD2 = [
    ("Attached is your revised quote, left off the D rings which we don't stock and we have 75pcs of the 4040ast in stock", clf.CAT_QUOTE_DELIVERED),
    ("Michael, Attached is your quote. We only have 75pcs of the 4040ast but we have plenty of the 3838ast in stock", clf.CAT_QUOTE_DELIVERED),
    ("Yeah we don't stock much but we can supply them. If you would like price and availability please let me know", clf.CAT_NEED_INFO),
    ("Sorry I tried looking around for you but came up empty handed.", clf.CAT_NO_QUOTE),
    ("Good afternoon, I will get your request updated for you. Have a great day!", clf.CAT_QUOTE_ACK),
    ("Good afternoon, I am still working on this for you I found one source that has it", clf.CAT_QUOTE_ACK),
    ("I forwarded your request along and I will keep you posted.", clf.CAT_QUOTE_ACK),
    ("Good afternoon, please see the message from my customer and advise. I'm being told the #2awg stock is gone.", clf.CAT_NEED_INFO),
    ("Here you go, if you don't mind just please fill in the address change. Thanks!", clf.CAT_NEED_INFO),
    ("Good morning, My driver is trying to make this delivery but can't reach Chris. Please advise. Thanks!", clf.CAT_NEED_INFO),
]
for _txt, _want in PROD2:
    _got, _c = le.infer_from_reply_text(_txt)
    check("prod2: %-16s <- %s" % (_want, _txt[:36]), _got == _want, _got)

# delivery-beats-decline is the ordering property, asserted directly
check("delivered quote outranks an embedded decline clause",
      le.infer_from_reply_text("Attached is your quote, we don't stock the D rings")[0] == clf.CAT_QUOTE_DELIVERED)
check("a pure decline is still no_quote",
      le.infer_from_reply_text("We don't stock those, sorry.")[0] == clf.CAT_NO_QUOTE)
check("'but we can supply' withdraws the decline",
      le.infer_from_reply_text("We don't stock much but we can supply them")[0] != clf.CAT_NO_QUOTE)

# outbound stock-checks (user asking a vendor)
for _txt in ("Good afternoon, is this all in stock?",
             "how about on the attached do you have stock on this one? If so please quote."):
    _got, _c = le.infer_from_reply_text(_txt)
    check("prod2: outbound stock-check not labeled <- %s" % _txt[:32],
          _got == clf.CAT_ESCALATE and _c <= 0.2, (_got, _c))

# stage_id must not move when the parser improves (the duplicate-rows bug)
_c1 = {"source_message_id": "", "sent_on": "2026-07-25T10:00:00",
       "subject": "RE: P&A", "to_addr": "bob@x.com",
       "reply_text": "Thanks! Steve Berson,"}
_c2 = dict(_c1, reply_text="Thanks!")      # same mail, retuned parser
check("stage_id stable across reply_text changes",
      le.stage_id_for(_c1) == le.stage_id_for(_c2))
check("stage_id still distinguishes different mail",
      le.stage_id_for(_c1) != le.stage_id_for(dict(_c1, subject="RE: other")))
check("stage_id prefers message-id when present",
      le.stage_id_for({"source_message_id": "<a@b>", "reply_text": "x"}) ==
      le.stage_id_for({"source_message_id": "<a@b>", "reply_text": "y"}))

# ---- 23. v1.7.0: per-window geometry + Outlook signature -------------------
class _GeoStub2:
    _DEFAULT_GEOMETRY = _app.ReplyPilotApp._DEFAULT_GEOMETRY
    def __init__(self, sw, sh, settings):
        self.root = _ScreenStub(sw, sh)
        self.settings = settings
def _g2(settings, key, default, sw=1920, sh=1080):
    return _app.ReplyPilotApp._geometry_for(_GeoStub2(sw, sh, settings), key, default)
check("geometry: per-window key restored", _g2({"window_geometry": {"learning": "900x500+40+40"}}, "learning", "1180x700") == "900x500+40+40")
check("geometry: unknown key falls back to that window's default", _g2({"window_geometry": {"learning": "900x500"}}, "settings", "640x640") == "640x640")
check("geometry: keys are independent", _g2({"window_geometry": {"main": "800x600", "review": "940x660+10+10"}}, "review", "940x660") == "940x660+10+10")
_legacy = {"window_geometry": "1000x700+50+50"}
check("geometry: v1.5 bare-string setting migrates to main", _g2(_legacy, "main", "1060x660") == "1000x700+50+50")
check("geometry: migration rewrites the setting as a dict", isinstance(_legacy["window_geometry"], dict))
check("geometry: child window still clamped on-screen", _g2({"window_geometry": {"learning": "900x500+9000+9000"}}, "learning", "1180x700").endswith("+1820+980"))

# signature reading (files, no COM)
_sdir2 = tempfile.mkdtemp(prefix="rp_sig_")
_sp = os.path.join(_sdir2, "Main.txt")
with open(_sp, "w", encoding="utf-8-sig") as _f:
    _f.write("Steve Berson\r\nAmerican Power ESC\r\n\r\n\r\n\r\nO: 718-731-8300")
_sig = drafts.read_signature_file(_sp)
check("signature: BOM + CRLF handled", _sig.startswith("Steve Berson") and "\r" not in _sig)
check("signature: runs of blank lines collapsed", "\n\n\n" not in _sig)
_sp16 = os.path.join(_sdir2, "Utf16.txt")
with open(_sp16, "w", encoding="utf-16") as _f:
    _f.write("Steve Berson\nAmerican Power ESC")
check("signature: UTF-16 file read without mojibake", drafts.read_signature_file(_sp16).startswith("Steve Berson"))
check("signature: missing file returns empty", drafts.read_signature_file(os.path.join(_sdir2, "nope.txt")) == "")
check("signature: dir helper empty when APPDATA absent", drafts.outlook_signature_dir() == "" or os.path.isdir(drafts.outlook_signature_dir()))
check("signature: list is empty off-Windows", isinstance(drafts.list_outlook_signatures(), list))


# ---- 24. v1.8.0: model discovery + signature-preserving send ---------------
class _StubTags(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        import json as _j
        if self.path == "/api/tags":
            self.send_response(200); self.end_headers()
            self.wfile.write(_j.dumps({"models": [
                {"name": "gemma3:27b"}, {"name": "llama3.1:8b"},
                {"name": "qwen2.5:14b"}]}).encode())
        else:
            self.send_response(404); self.end_headers()

_srv2 = HTTPServer(("127.0.0.1", 0), _StubTags)
_port2 = _srv2.server_address[1]
_thr.Thread(target=_srv2.serve_forever, daemon=True).start()

_models = clf.list_models("127.0.0.1", _port2, timeout=3)
check("list_models returns installed tags sorted",
      _models == ["gemma3:27b", "llama3.1:8b", "qwen2.5:14b"], _models)
check("list_models on a dead endpoint returns [] (no raise)",
      clf.list_models("127.0.0.1", 1, timeout=1) == [])
check("model_available exact tag", clf.model_available("127.0.0.1", _port2, "gemma3:27b", 3) is True)
check("model_available bare name matches tagged", clf.model_available("127.0.0.1", _port2, "llama3.1", 3) is True)
check("model_available false for missing model",
      clf.model_available("127.0.0.1", _port2, "mistral:7b", 3) is False)
# endpoint_models honors the configured endpoints
_snap2 = clf.ai_settings_defaults()
clf.apply_ai_settings({"ai_host": "127.0.0.1", "ai_port": str(_port2),
                       "ai_local_host": "127.0.0.1", "ai_local_port": "1"})
_hm, _lm = clf.endpoint_models(timeout=2)
check("endpoint_models reads host list", "gemma3:27b" in _hm, _hm)
check("endpoint_models empty for dead local", _lm == [], _lm)
clf.apply_ai_settings(_snap2)
_srv2.shutdown()

# signature stripping before an Outlook-signed send
_sig_block = "Steve Berson\nAmerican Power Electrical Supply"
_draft = "Hi Mike,\n\nThanks for the RFQ.\n\n" + _sig_block
check("configured signature stripped from draft",
      drafts.strip_configured_signature(_draft, _sig_block) == "Hi Mike,\n\nThanks for the RFQ.",
      repr(drafts.strip_configured_signature(_draft, _sig_block)))
check("stripping is a no-op when signature absent",
      drafts.strip_configured_signature("Just a line.", _sig_block) == "Just a line.")
check("falls back to first signature line if block was edited",
      drafts.strip_configured_signature("Hi.\n\nSteve Berson\nSome New Title", _sig_block) == "Hi.")
check("empty signature setting leaves draft untouched",
      drafts.strip_configured_signature(_draft, "") == _draft.strip())
# html conversion used for the HTMLBody send path
_h = mail._text_to_html("Hi Mike,\n\nLine one\nLine two")
check("text_to_html escapes and breaks paragraphs",
      "<p>" in _h and "<br>" in _h and "Line one" in _h, _h[:80])
check("text_to_html escapes markup",
      "&lt;script&gt;" in mail._text_to_html("<script>"), mail._text_to_html("<script>"))
check("send_outlook_reply accepts the signature flag",
      "use_outlook_signature" in mail.send_outlook_reply.__code__.co_varnames)


# ---- 25. v1.9.0: multi-mailbox folder enumeration + scanning ---------------
# A fake COM folder tree. Mirrors the shapes the real code touches
# (.Folders/.Count/.Item(i)/.FolderPath/.Name/.DefaultItemType/.Items) so the
# recursion, path matching and per-folder capping are genuinely exercised
# without Outlook.
class _FakeColl:
    def __init__(self, items): self._i = list(items)
    @property
    def Count(self): return len(self._i)
    def Item(self, n): return self._i[n - 1]
    def __iter__(self): return iter(self._i)

class _FakeItems(_FakeColl):
    def Sort(self, *a, **k): pass
    def Restrict(self, *a, **k): return self

class _FakeMail:
    Class = 43
    def __init__(self, subject, sender="a@b.com", body="hi"):
        self.Subject = subject; self.SenderEmailAddress = sender
        self.SenderName = "A"; self.Body = body
        self.ReceivedTime = type("T", (), {"year": 2026, "month": 7, "day": 25,
                                           "hour": 9, "minute": 0, "second": 0})()
        self.PropertyAccessor = type("PA", (), {
            "GetProperty": staticmethod(lambda _p: "<%s@test>" % subject)})()

class _FakeFolder:
    def __init__(self, name, path, children=(), mails=(), item_type=0):
        self.Name = name; self.FolderPath = path
        self.DefaultItemType = item_type
        self.Folders = _FakeColl(children)
        self.Items = _FakeItems(mails)

_shared_inbox = _FakeFolder("Inbox", "\\\\sales@ap.com\\Inbox",
                            mails=[_FakeMail("s%d" % i) for i in range(5)])
_shared_sub = _FakeFolder("Quotes", "\\\\sales@ap.com\\Inbox\\Quotes",
                          mails=[_FakeMail("q1")])
_shared_inbox.Folders = _FakeColl([_shared_sub])
_own_inbox = _FakeFolder("Inbox", "\\\\steve@ap.com\\Inbox",
                         mails=[_FakeMail("o%d" % i) for i in range(3)])
_calendar = _FakeFolder("Calendar", "\\\\steve@ap.com\\Calendar", item_type=1)
_store_a = _FakeFolder("steve@ap.com", "\\\\steve@ap.com",
                       children=[_own_inbox, _calendar])
_store_b = _FakeFolder("sales@ap.com", "\\\\sales@ap.com",
                       children=[_shared_inbox])

class _FakeNS:
    Folders = _FakeColl([_store_a, _store_b])
    def GetDefaultFolder(self, _n): return _own_inbox
class _FakeApp:
    def GetNamespace(self, _n): return _FakeNS()

_real_fresh = mail.fresh_outlook
_real_init, _real_uninit = mail.outlook_thread_init, mail.outlook_thread_uninit
mail.fresh_outlook = lambda: _FakeApp()
mail.outlook_thread_init = lambda: None
mail.outlook_thread_uninit = lambda: None
try:
    _folders = mail.list_mail_folders()
    _paths = [f["path"] for f in _folders]
    check("enumerates folders across every store",
          "\\\\steve@ap.com\\Inbox" in _paths and "\\\\sales@ap.com\\Inbox" in _paths, _paths)
    check("enumerates nested subfolders",
          "\\\\sales@ap.com\\Inbox\\Quotes" in _paths, _paths)
    check("non-mail folders excluded (Calendar)",
          "\\\\steve@ap.com\\Calendar" not in _paths, _paths)
    check("store name carried on each folder",
          {f["store"] for f in _folders} == {"steve@ap.com", "sales@ap.com"},
          {f["store"] for f in _folders})
    check("item counts reported", any(f["count"] == 5 for f in _folders),
          [(f["name"], f["count"]) for f in _folders])
    check("nesting depth reported", any(f["depth"] == 1 for f in _folders))

    # resolution by FolderPath
    _ns = _FakeNS()
    check("resolve finds a nested folder by path",
          mail._resolve_folder(_ns, "\\\\sales@ap.com\\Inbox\\Quotes") is _shared_sub)
    check("resolve returns None for an unknown path",
          mail._resolve_folder(_ns, "\\\\gone@ap.com\\Inbox") is None)
    check("resolve returns None for empty path",
          mail._resolve_folder(_ns, "") is None)

    # scanning multiple folders
    _items, _rep = mail.scan_outlook_folders(
        ["\\\\steve@ap.com\\Inbox", "\\\\sales@ap.com\\Inbox"], max_items=100)
    check("scans every selected folder", len(_items) == 8, len(_items))
    check("per-folder report returned ok for each", all(r[2] == "ok" for r in _rep) and len(_rep) == 2, _rep)
    check("scanned items tagged with their folder",
          {i["source_path"] for i in _items} == {"\\\\steve@ap.com\\Inbox", "\\\\sales@ap.com\\Inbox"})
    # cap is PER folder, not global
    _items2, _ = mail.scan_outlook_folders(
        ["\\\\steve@ap.com\\Inbox", "\\\\sales@ap.com\\Inbox"], max_items=2)
    check("max_items applies per folder, not globally", len(_items2) == 4, len(_items2))
    # a folder that has since disappeared is reported, not fatal
    _items3, _rep3 = mail.scan_outlook_folders(
        ["\\\\steve@ap.com\\Inbox", "\\\\gone@ap.com\\Inbox"], max_items=10)
    check("missing folder reported without aborting the scan",
          len(_items3) == 3 and any(r[2] == "not found" for r in _rep3), _rep3)
    # no selection -> default inbox only (previous behavior preserved)
    _items4, _rep4 = mail.scan_outlook_folders(None, max_items=10)
    check("empty selection falls back to the default Inbox",
          len(_items4) == 3 and len(_rep4) == 1, (len(_items4), _rep4))
    check("back-compat scan_outlook_inbox still returns a plain list",
          isinstance(mail.scan_outlook_inbox(max_items=10), list))
    check("default_inbox_path resolves", mail.default_inbox_path() == "\\\\steve@ap.com\\Inbox")
finally:
    mail.fresh_outlook = _real_fresh
    mail.outlook_thread_init, mail.outlook_thread_uninit = _real_init, _real_uninit
check("COM stubs restored after the fake-tree tests", mail.fresh_outlook is _real_fresh)


# ---- 26. v1.9.1: mailbox list grouping + noise filtering -------------------
check("system folder names recognized",
      all(mail.is_system_folder_name(n) for n in
          ["Sync Issues", "Yammer Root", "Conversation History",
           "Quick Step Settings", "WebExtAddIns", "Calendar", "RSS Feeds"]))
check("real folders not flagged system",
      not any(mail.is_system_folder_name(n) for n in
              ["Inbox", "INVOICES", "PRICING", "Archive", "TTR", "AP"]))
check("GUID-named folders flagged system",
      mail.is_system_folder_name("8f4d1315-5cf9-9872-b1b94618e70a"))
check("blank name is not system", mail.is_system_folder_name("") is False)

# system flag taints the whole subtree (Yammer Root -> Inbound/Outbound/Feeds)
_yam_child = _FakeFolder("Inbound", "\\\\s@ap.com\\Yammer Root\\Inbound")
_yam = _FakeFolder("Yammer Root", "\\\\s@ap.com\\Yammer Root",
                   children=[_yam_child])
_real_inbox = _FakeFolder("Inbox", "\\\\s@ap.com\\Inbox",
                          mails=[_FakeMail("m1")])
_store_c = _FakeFolder("s@ap.com", "\\\\s@ap.com",
                       children=[_yam, _real_inbox])
class _NS3:
    Folders = _FakeColl([_store_c])
    def GetDefaultFolder(self, _n): return _real_inbox
mail.fresh_outlook = lambda: type("A", (), {"GetNamespace": lambda s, n: _NS3()})()
mail.outlook_thread_init = lambda: None
mail.outlook_thread_uninit = lambda: None
try:
    _fs = mail.list_mail_folders()
    _byname = {f["name"]: f for f in _fs}
    check("child of a system folder inherits the flag",
          _byname["Inbound"]["system"] is True, _byname.get("Inbound"))
    check("ordinary Inbox not flagged", _byname["Inbox"]["system"] is False)
finally:
    mail.fresh_outlook = _real_fresh
    mail.outlook_thread_init, mail.outlook_thread_uninit = _real_init, _real_uninit

# --- display grouping, modelled on a real 3-mailbox profile ---
FOLDERS = [
    {"path": "\\\\steve@ap.com\\Inbox", "name": "Inbox", "store": "steve@ap.com", "depth": 0, "count": 15946, "system": False},
    {"path": "\\\\steve@ap.com\\Sync Issues", "name": "Sync Issues", "store": "steve@ap.com", "depth": 0, "count": 70289, "system": True},
    {"path": "\\\\steve@ap.com\\Archive", "name": "Archive", "store": "steve@ap.com", "depth": 0, "count": 11, "system": False},
    {"path": "\\\\steve@ap.com\\Drafts", "name": "Drafts", "store": "steve@ap.com", "depth": 0, "count": 0, "system": False},
    {"path": "\\\\sales@ap.com\\Inbox", "name": "Inbox", "store": "sales@ap.com", "depth": 0, "count": 4199, "system": False},
    {"path": "\\\\sales@ap.com\\Inbox\\INVOICES", "name": "INVOICES", "store": "sales@ap.com", "depth": 1, "count": 0, "system": False},
    {"path": "\\\\sales@ap.com\\Inbox\\PRICING", "name": "PRICING", "store": "sales@ap.com", "depth": 1, "count": 80, "system": False},
    {"path": "\\\\empty@ap.com\\Yammer Root", "name": "Yammer Root", "store": "empty@ap.com", "depth": 0, "count": 0, "system": True},
]
_rows = _app.group_folders_by_store(FOLDERS, selected=(), hide_empty=True, show_system=False)
_stores = [v for k, v in _rows if k == "store"]
_fidx = [v for k, v in _rows if k == "folder"]
check("rows grouped under a heading per mailbox",
      _stores == ["steve@ap.com", "sales@ap.com"], _stores)
check("a store with nothing visible is omitted entirely",
      "empty@ap.com" not in _stores, _stores)
check("system folders hidden by default",
      all(not FOLDERS[i]["system"] for i in _fidx))
check("empty folders hidden by default",
      all(FOLDERS[i]["count"] != 0 for i in _fidx))
check("real folders survive the filter",
      {FOLDERS[i]["name"] for i in _fidx} == {"Inbox", "Archive", "Inbox", "PRICING"},
      {FOLDERS[i]["name"] for i in _fidx})
check("both Inboxes present, one per mailbox",
      sum(1 for i in _fidx if FOLDERS[i]["name"] == "Inbox") == 2)
check("store heading precedes its own folders",
      _rows[0] == ("store", "steve@ap.com") and _rows[1][0] == "folder")

# a selected folder is never hidden, whatever the filters say
_sel = {"\\\\sales@ap.com\\Inbox\\INVOICES", "\\\\steve@ap.com\\Sync Issues"}
_rows2 = _app.group_folders_by_store(FOLDERS, selected=_sel, hide_empty=True, show_system=False)
_paths2 = [FOLDERS[i]["path"] for k, i in _rows2 if k == "folder"]
check("a ticked empty folder stays visible",
      "\\\\sales@ap.com\\Inbox\\INVOICES" in _paths2, _paths2)
check("a ticked system folder stays visible",
      "\\\\steve@ap.com\\Sync Issues" in _paths2, _paths2)
# toggles
_rows3 = _app.group_folders_by_store(FOLDERS, selected=(), hide_empty=False, show_system=False)
check("hide_empty off reveals empty folders",
      any(FOLDERS[i]["name"] == "Drafts" for k, i in _rows3 if k == "folder"))
_rows4 = _app.group_folders_by_store(FOLDERS, selected=(), hide_empty=True, show_system=True)
check("show_system on reveals plumbing (non-empty)",
      any(FOLDERS[i]["name"] == "Sync Issues" for k, i in _rows4 if k == "folder"))
check("a busy system folder is still hidden by default",
      not any(FOLDERS[i]["name"] == "Sync Issues" for k, i in _rows if k == "folder"))
_rows5 = _app.group_folders_by_store(FOLDERS, selected=(), hide_empty=False, show_system=True)
check("both toggles off hides nothing",
      len([1 for k, _ in _rows5 if k == "folder"]) == len(FOLDERS))
check("folder_visible: empty+unselected hidden",
      _app.folder_visible({"path": "p", "count": 0, "system": False}, (), True, False) is False)
check("folder_visible: empty+selected shown",
      _app.folder_visible({"path": "p", "count": 0, "system": False}, {"p"}, True, False) is True)
check("folder_visible: unknown count (-1) not treated as empty",
      _app.folder_visible({"path": "p", "count": -1, "system": False}, (), True, False) is True)
check("group handles an empty folder list", _app.group_folders_by_store([], (), True, False) == [])


# ---- 27. v1.10.0: bulk actions ---------------------------------------------
# pure resolution
_r = _app.bulk_resolution(clf.CAT_QUOTE_ACK, _app.BULK_KEEP)
check("keep-AI resolves to accept at the row's own category",
      _r == (rec.ACTION_ACCEPTED, clf.CAT_QUOTE_ACK, False), _r)
_r = _app.bulk_resolution(clf.CAT_NEED_INFO, clf.CAT_NO_QUOTE)
check("override resolves to recategorized + regenerate",
      _r == (rec.ACTION_RECATEGORIZED, clf.CAT_NO_QUOTE, True), _r)
_r = _app.bulk_resolution(clf.CAT_NO_QUOTE, clf.CAT_NO_QUOTE)
check("override matching the row's own category is still an accept",
      _r == (rec.ACTION_ACCEPTED, clf.CAT_NO_QUOTE, False), _r)
_r = _app.bulk_resolution(clf.CAT_QUOTE_ACK, clf.CAT_NO_REPLY)
check("no_reply override routes to move-to-no-reply",
      _r == (rec.ACTION_MOVED_NO_REPLY, clf.CAT_NO_REPLY, False), _r)
_r = _app.bulk_resolution(clf.CAT_NO_REPLY, _app.BULK_KEEP)
check("keep-AI on a no_reply row routes to move-to-no-reply",
      _r[0] == rec.ACTION_MOVED_NO_REPLY, _r)

# --- _apply_bulk against a real store ---
_bdir = tempfile.mkdtemp(prefix="rp_bulk_")
_bstore = rec.RecordStore(directory=_bdir)
_mids = []
for _i, _cat in enumerate([clf.CAT_QUOTE_ACK, clf.CAT_QUOTE_ACK,
                           clf.CAT_NEED_INFO, clf.CAT_JOB_NAME]):
    _m = "<bulk%d@test>" % _i
    _bstore.upsert_intake(_m, "2026-07-25T09:0%d:00+00:00" % _i,
                          "RFQ %d" % _i, "c%d@x.com" % _i, {}, True, _cat,
                          0.7, "draft %d" % _i, "heuristic", "please quote")
    _mids.append(_m)

class _AutoStub:
    def __init__(self): self.cancelled = []
    def cancel(self, mid): self.cancelled.append(mid); return True
class _BulkApp:
    _apply_bulk = _app.ReplyPilotApp._apply_bulk
    def __init__(self, store):
        self.store = store; self.auto = _AutoStub()
        self.settings = dict(settings); self.checked = set(_mids)
        self.sent = []; self.refreshed = 0; self.ai_queue = {}
    def _refresh_lists(self): self.refreshed += 1
    # no learning store in this stub, so no voice examples — the same thing
    # the real app returns when the corpus has nothing confirmed for a
    # category, which is what makes drafting fall back to the template
    def _voice_for(self, category): return []
    def send_reply_async(self, mid, sender, subject, body):
        self.sent.append((mid, body))

# keep-AI across a mixed selection
_ba = _BulkApp(_bstore)
_rec, _sent, _skip = _ba._apply_bulk(_mids, _app.BULK_KEEP, send=False)
check("bulk keep-AI records every row", _rec == 4 and _sent == 0, (_rec, _sent))
check("bulk keep-AI preserves each row's own category",
      [_bstore.get(m)["final_category"] for m in _mids] ==
      [clf.CAT_QUOTE_ACK, clf.CAT_QUOTE_ACK, clf.CAT_NEED_INFO, clf.CAT_JOB_NAME])
check("bulk keep-AI records them as UNCHANGED (graduation signal intact)",
      all(_bstore.get(m)["changed_by_user"] == 0 for m in _mids))
check("bulk keep-AI sends nothing", _ba.sent == [])
check("bulk cancels any scheduled auto-send", set(_ba.auto.cancelled) == set(_mids))
check("bulk clears the ticked set", _ba.checked == set())
_st = _bstore.category_stats()
check("bulk-accepted rows count toward graduation",
      _st[clf.CAT_QUOTE_ACK]["samples"] == 2 and _st[clf.CAT_QUOTE_ACK]["unchanged"] == 2, _st.get(clf.CAT_QUOTE_ACK))

# override across a mixed selection
_mids2 = []
for _i, _cat in enumerate([clf.CAT_QUOTE_ACK, clf.CAT_NO_QUOTE]):
    _m = "<bulkb%d@test>" % _i
    _bstore.upsert_intake(_m, "2026-07-25T10:0%d:00+00:00" % _i, "RFQ b%d" % _i,
                          "d%d@x.com" % _i, {}, True, _cat, 0.7,
                          "old draft", "heuristic", "please quote")
    _mids2.append(_m)
_ba2 = _BulkApp(_bstore)
_ba2._apply_bulk(_mids2, clf.CAT_NO_QUOTE, send=False)
check("override sets the chosen category on all rows",
      all(_bstore.get(m)["final_category"] == clf.CAT_NO_QUOTE for m in _mids2))
check("row that already matched is recorded unchanged",
      _bstore.get(_mids2[1])["changed_by_user"] == 0)
check("row that was overridden is recorded changed",
      _bstore.get(_mids2[0])["changed_by_user"] == 1)
check("overridden row's draft regenerated for the new category",
      _bstore.get(_mids2[0])["final_draft"] != "old draft" and
      "quote" in _bstore.get(_mids2[0])["final_draft"].lower(),
      _bstore.get(_mids2[0])["final_draft"][:60])

# accept & send
_mids3 = []
for _i in range(3):
    _m = "<bulkc%d@test>" % _i
    _bstore.upsert_intake(_m, "2026-07-25T11:0%d:00+00:00" % _i, "RFQ c%d" % _i,
                          "e%d@x.com" % _i, {}, True, clf.CAT_QUOTE_ACK, 0.9,
                          ("" if _i == 2 else "a real draft"), "heuristic", "quote")
    _mids3.append(_m)
_ba3 = _BulkApp(_bstore)
_r3, _s3, _k3 = _ba3._apply_bulk(_mids3, _app.BULK_KEEP, send=True)
check("accept&send records all rows", _r3 == 3, _r3)
check("accept&send sends only rows that have a draft", _s3 == 2 and len(_ba3.sent) == 2, (_s3, len(_ba3.sent)))
check("accept&send reports the empty-draft row as skipped", _k3 == 1, _k3)
check("accept&send sends the row's own draft text",
      all(b == "a real draft" for _m, b in _ba3.sent), _ba3.sent)
# a row deleted between selection and action is skipped, not fatal
_ba4 = _BulkApp(_bstore)
_r4, _s4, _k4 = _ba4._apply_bulk(["<not-there@test>"], _app.BULK_KEEP, send=False)
check("missing row skipped without raising", (_r4, _k4) == (0, 1), (_r4, _k4))
check("bulk refreshes the lists once per call", _ba4.refreshed == 1)
_bstore.close()


# ---- 28. v1.11.0: quote_in_process + UI plumbing ---------------------------
# the exact screenshot case: a chase whose quoted original is a full RFQ
_chase_body = ("This coming over soon? thanks.\n\n"
               "From: Sales <sales@americanpoweresc.com>\n"
               "Sent: Friday, July 24, 2026 4:35 PM\n"
               "To: Robert Bell <rbell@hellmanelectric.com>\n"
               "Subject: RE: 2109 T6 Req 273\n\n"
               "Please quote the following: (3) QO2100 breakers, need pricing and lead time.")
_r = clf.classify("RE: 2109 T6 Req 273", "rbell@hellmanelectric.com", _chase_body)
check("chase outranks the RFQ quoted beneath it",
      _r["category"] == clf.CAT_QUOTE_IN_PROCESS, _r["category"])
for _txt in ["Any update on this one?", "Just checking in on the pricing.",
             "Still waiting on that quote", "Did you get a chance to look at this?",
             "Any word on the panel pricing?"]:
    check("chase: %s" % _txt[:30],
          clf.classify("RE: quote", "c@x.com", _txt)["category"] == clf.CAT_QUOTE_IN_PROCESS)
check("a fresh RFQ is still quote_ack, not in_process",
      clf.classify("RFQ", "m@x.com", "Please quote the following: (3) QO2100 breakers qty 3")["category"] == clf.CAT_QUOTE_ACK)
check("chase detection only reads the top of the body",
      "chase" in clf.extract_features("s", "f", "hello")
      and clf.extract_features("s", "f", "x" * 500 + " any update")["chase"] is False)

# template + taxonomy wiring
_d, _ = drafts.make_draft(clf.CAT_QUOTE_IN_PROCESS, "Robert", "rbell@x.com", settings=settings)
check("in_process template confirms work underway",
      "being worked on" in _d and "shortly" in _d.lower(), _d[:70])
check("in_process draft greets by first name",
      _d.splitlines()[0].endswith("Robert,"), _d[:30])
check("in_process is a UI reply choice", clf.CAT_QUOTE_IN_PROCESS in clf.REPLY_CATEGORIES)
check("quote family grouped for the dropdown",
      set(clf.QUOTE_CATEGORIES) == {clf.CAT_QUOTE_ACK, clf.CAT_QUOTE_IN_PROCESS,
                                    clf.CAT_QUOTE_DELIVERED, clf.CAT_NO_QUOTE},
      clf.QUOTE_CATEGORIES)
check("every quote category is also a reply category",
      all(c in clf.REPLY_CATEGORIES for c in clf.QUOTE_CATEGORIES))
check("no_reply stays out of the quote dropdown",
      clf.CAT_NO_REPLY not in clf.QUOTE_CATEGORIES)
check("every category has a UI label",
      all(c in _app.CATEGORY_LABELS for c in clf.CATEGORIES),
      [c for c in clf.CATEGORIES if c not in _app.CATEGORY_LABELS])

# reply-side discrimination (learning import)
check("'Yes this is being worked on...patience' -> in_process",
      le.infer_from_reply_text("Yes this is being worked on and I will get it over to you shortly. Thank you for your patience!")[0] == clf.CAT_QUOTE_IN_PROCESS)
check("plain 'being worked on' still reads as quote_ack",
      le.infer_from_reply_text("Good afternoon, this is being worked on for you and should have it over shortly")[0] == clf.CAT_QUOTE_ACK)

# bulk resolution understands the new category
check("bulk keep-AI works for in_process",
      _app.bulk_resolution(clf.CAT_QUOTE_IN_PROCESS, _app.BULK_KEEP) ==
      (rec.ACTION_ACCEPTED, clf.CAT_QUOTE_IN_PROCESS, False))
check("bulk override to in_process regenerates",
      _app.bulk_resolution(clf.CAT_QUOTE_ACK, clf.CAT_QUOTE_IN_PROCESS) ==
      (rec.ACTION_RECATEGORIZED, clf.CAT_QUOTE_IN_PROCESS, True))

# label text: no doubled ampersand anywhere
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "replypilot.pyw"), encoding="utf-8").read()
check("no '&&' left in button labels", 'Accept && Send' not in _src)
check("both Accept & Send buttons present", _src.count('Accept & Send"') >= 2, _src.count('Accept & Send"'))
# picker dialogs take the app (for geometry) rather than a bare parent
import inspect as _insp
check("_pick_bulk_category takes app for geometry binding",
      list(_insp.signature(_app._pick_bulk_category).parameters)[0] == "app")
check("_pick_category takes app for geometry binding",
      list(_insp.signature(_app._pick_category).parameters)[0] == "app")
check("_pick_from_list takes app for geometry binding",
      list(_insp.signature(_app._pick_from_list).parameters)[0] == "app")


# ---- 29. v1.12.0: needs_input flag + AI review queue -----------------------
# the exact screenshot case: a rep asking the USER for facts only they hold
_ni_body = ("Hi Jeremy,\n\nI'm not sure if they will do 15% but I will try.\n"
            "Do you have a job name and the competition?\n\n"
            "Sincerely,\nJon Del Vecchio\nTri-Tech Sales Associates, Inc.")
_r = clf.classify("RE: Purchase Order P000020783", "jdelvecchio@tri-techsales.com", _ni_body)
check("screenshot email flagged needs_input", _r["needs_input"] is True, _r)
check("needs_input is orthogonal — a category is still assigned",
      _r["category"] in clf.CATEGORIES, _r["category"])
for _t in ["Who else is bidding this?", "What is your landed cost?",
           "Do you have a job name and the competition?",
           "Who is the competition on this one?",
           "Do you know who the contractor is?"]:
    check("needs_input: %s" % _t[:34],
          clf.detect_needs_input("s", _t) is True)
for _t in ["Please quote QO2100 qty 3", "Any update on this one?",
           "Attached is your quote.", "Thanks!"]:
    check("not needs_input: %s" % _t[:30],
          clf.detect_needs_input("s", _t) is False)
check("needs_input never set on automated mail",
      clf.classify("Shipment Notification", "noreply@ups.com",
                   "automated message, do not reply. who is the competition?")
      ["needs_input"] is False)

# persistence + migration
_nidir = tempfile.mkdtemp(prefix="rp_ni_")
_nis = rec.RecordStore(directory=_nidir)
_cols = {r[1] for r in _nis._conn.execute("PRAGMA table_info(decisions)")}
check("needs_input column exists", "needs_input" in _cols)
_nis.upsert_intake("<ni1@t>", "2026-07-27T08:27:00+00:00", "RE: PO", "j@t.com",
                   {}, True, clf.CAT_JOB_NAME, 0.9, "draft", "heuristic",
                   _ni_body, needs_input=True)
check("needs_input persisted on intake", _nis.get("<ni1@t>")["needs_input"] == 1)
_nis.upsert_intake("<ni2@t>", "2026-07-27T08:28:00+00:00", "RFQ", "k@t.com",
                   {}, True, clf.CAT_QUOTE_ACK, 0.95, "draft", "heuristic", "quote please")
check("default is not-flagged", _nis.get("<ni2@t>")["needs_input"] == 0)
_nis.set_needs_input("<ni2@t>", True)
check("set_needs_input flags a row", _nis.get("<ni2@t>")["needs_input"] == 1)
_nis.set_needs_input("<ni2@t>", False)
check("set_needs_input unflags a row", _nis.get("<ni2@t>")["needs_input"] == 0)

# the safety property: needs_input blocks auto-send above graduation
_nis.set_needs_input("<ni2@t>", True)
_nis.set_auto_send_override(clf.CAT_QUOTE_ACK, True)
_nia = {"auto_send_master": True, "auto_send_delay_sec": 60,
        "auto_send_min_conf": 0.5, "office_hours_enabled": False}
_nieng = auto.AutoSendEngine(_nis, _nia)
_elig = [r["message_id"] for r in _nieng.eligible_rows()]
check("needs_input row is NOT auto-send eligible", "<ni2@t>" not in _elig, _elig)
check("needs_input listed in the engine's hard blocks",
      "needs_input" in open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "replypilot_auto_engine.py"),
                            encoding="utf-8").read())
_nis.set_needs_input("<ni2@t>", False)
check("clearing the flag restores eligibility",
      "<ni2@t>" in [r["message_id"] for r in _nieng.eligible_rows()])
# regression: a manual override on a category with no decided rows yet was
# silently ignored, because stats were built only from decided data
check("manual override works before any decided rows exist",
      _nis.category_stats().get(clf.CAT_QUOTE_ACK, {}).get("auto_send") is True,
      _nis.category_stats().get(clf.CAT_QUOTE_ACK))
check("override-only category reports zero samples honestly",
      _nis.category_stats()[clf.CAT_QUOTE_ACK]["samples"] == 0 and
      _nis.category_stats()[clf.CAT_QUOTE_ACK]["graduated"] is False)
_nis.close()

# migration from a DB that predates the column
_m2 = tempfile.mkdtemp(prefix="rp_mig2_")
_c2 = _sq.connect(os.path.join(_m2, "replypilot.db"))
_c2.executescript("""CREATE TABLE decisions (message_id TEXT PRIMARY KEY, received_at TEXT,
 subject TEXT, sender TEXT, features TEXT, ai_needs_reply INTEGER, ai_category TEXT,
 ai_confidence REAL, ai_draft TEXT, ai_source TEXT, user_action TEXT NOT NULL DEFAULT 'pending',
 final_category TEXT, final_draft TEXT, changed_by_user INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, decided_at TEXT, body_preview TEXT, body_full TEXT,
 origin TEXT NOT NULL DEFAULT 'live');""")
_c2.execute("INSERT INTO decisions (message_id, created_at, user_action, ai_category) "
            "VALUES ('<pre@x>','2026-01-01','pending','quote_ack')")
_c2.commit(); _c2.close()
_om = rec.RecordStore(directory=_m2)
check("migration adds needs_input to a pre-v1.4 DB",
      "needs_input" in {r[1] for r in _om._conn.execute("PRAGMA table_info(decisions)")})
check("existing rows default to not-flagged", _om.get("<pre@x>")["needs_input"] == 0)
_om.close()

# tab indices stay consistent with the seven-tab layout
check("tab indices distinct and ordered",
      [_app._TAB_AUTOSEND, _app._TAB_QUEUE, _app._TAB_INPUT,
       _app._TAB_AIREVIEW, _app._TAB_NOREPLY, _app._TAB_DELETED,
       _app._TAB_DECIDED] == [0, 1, 2, 3, 4, 5, 6])
check("Auto-Send leads: what is about to leave is seen first",
      _app._TAB_AUTOSEND == 0)
# the countdown the Auto-Send tab renders
check("countdown formats seconds under a minute", _app._fmt_countdown(45) == "45s")
check("countdown formats minutes:seconds", _app._fmt_countdown(125) == "2:05")
check("countdown says due at zero, not sending", _app._fmt_countdown(0) == "due")
# office hours: gates eligibility, not just firing
_MON = lambda h, m=0: _dt0.datetime(2026, 7, 27, h, m)   # a Monday
_SAT = lambda h: _dt0.datetime(2026, 8, 1, h, 0)         # a Saturday
import datetime as _dt0


class _OhStore:
    def pending(self): return []
    def category_stats(self): return {}
    def get(self, m): return None


_oh = lambda **kw: auto.AutoSendEngine(_OhStore(), dict({"auto_send_master": True}, **kw))
check("office hours: 09:00 Mon is open", _oh().within_office_hours(_MON(9)))
check("office hours: 06:59 Mon is closed", not _oh().within_office_hours(_MON(6, 59)))
check("office hours: 17:00 Mon is closed", not _oh().within_office_hours(_MON(17)))
check("office hours: Saturday is closed all day",
      not any(_oh().within_office_hours(_SAT(h)) for h in range(24)))
check("office hours off = always open (the holiday switch)",
      _oh(office_hours_enabled=False).within_office_hours(_dt0.datetime(2026, 8, 2, 3, 0)))
check("office hours: a window written backwards spans midnight",
      _oh(office_hours_start="22:00", office_hours_end="06:00").within_office_hours(_MON(2)))
check("office hours: rubbish falls back instead of raising",
      all(_oh(office_hours_start=_b).within_office_hours(_MON(9)) is not None
          for _b in ("", "abc", None, "99:99")))
check("office hours: empty day list falls back to Mon-Fri",
      _oh(office_hours_days=[]).within_office_hours(_MON(9)))
# greeting: two halves split at noon, and the model cannot move it
import datetime as _dt
_gr = lambda h: drafts.greeting_for("Roger", "r@x.com", now=_dt.datetime(2026, 7, 29, h, 0))
check("before noon greets Good morning", _gr(9) == "Good morning Roger,", _gr(9))
check("11:59 is still morning",
      drafts.greeting_for("R", "r@x.com", now=_dt.datetime(2026, 7, 29, 11, 59)).startswith("Good morning"))
check("noon flips to Good afternoon", _gr(12) == "Good afternoon Roger,", _gr(12))
check("evening stays afternoon (never used in the corpus)", _gr(19) == "Good afternoon Roger,", _gr(19))
check("no hour produces Good evening",
      all("evening" not in _gr(h) for h in range(24)))
_tpl_g = "Good morning Roger,\n\nWill advise. Thanks!\n\nSteve Berson"
for _bad in ("Good afternoon Roger,", "Good morning Barry,",
             "Good afternoon Barry,", "Hi Roger,"):
    _out = drafts._repair_greeting(_bad + "\n\nWill advise.\n\nSteve Berson", _tpl_g)
    check("greeting repaired: %s" % _bad,
          _out.splitlines()[0] == "Good morning Roger,", _out.splitlines()[0])
check("countdown tolerates rubbish", _app._fmt_countdown(None) == "")
for _fn in ("ai_run_auto", "ai_run_local", "ai_run_host", "ai_cancel_run",
            "ai_remove_selected", "ai_clear_queue", "bulk_needs_input"):
    check("app exposes %s" % _fn, callable(getattr(_app.ReplyPilotApp, _fn, None)))
check("only one ai_review entry point remains",
      open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "replypilot.pyw"),
           encoding="utf-8").read().count("    def ai_review(self)") == 1)


# ---- 30. v1.12.1: one row, one tab ----------------------------------------
_P = [
    {"message_id": "<a>", "ai_needs_reply": 1, "needs_input": 0},
    {"message_id": "<b>", "ai_needs_reply": 1, "needs_input": 1},
    {"message_id": "<c>", "ai_needs_reply": 0, "needs_input": 0},
    {"message_id": "<d>", "ai_needs_reply": 1, "needs_input": 0},
]
_q, _in, _nr, _ai = _app.partition_pending(_P, ())
check("no AI queue: rows split queue/input/no-reply",
      ([r["message_id"] for r in _q], [r["message_id"] for r in _in],
       [r["message_id"] for r in _nr], _ai) ==
      (["<a>", "<d>"], ["<b>"], ["<c>"], []),
      ([r["message_id"] for r in _q], [r["message_id"] for r in _in]))
_q, _in, _nr, _ai = _app.partition_pending(_P, {"<a>", "<b>"})
check("queued rows leave the auto-reply queue",
      [r["message_id"] for r in _q] == ["<d>"], [r["message_id"] for r in _q])
check("queued rows leave the needs-input tab too",
      [r["message_id"] for r in _in] == [], [r["message_id"] for r in _in])
check("queued rows appear in AI Review",
      [r["message_id"] for r in _ai] == ["<a>", "<b>"],
      [r["message_id"] for r in _ai])
check("every pending row lands in exactly one bucket",
      sorted(r["message_id"] for r in _q + _in + _nr + _ai) ==
      ["<a>", "<b>", "<c>", "<d>"])
check("AI queue outranks needs_input",
      "<b>" in [r["message_id"] for r in _ai])
_q, _in, _nr, _ai = _app.partition_pending(_P, {"<a>", "<b>", "<c>", "<d>"})
check("all queued: other tabs empty", (_q, _in, _nr) == ([], [], []))
check("unknown ids in the queue set are harmless",
      len(_app.partition_pending(_P, {"<zzz>"})[3]) == 0)
check("empty pending list is fine",
      _app.partition_pending([], {"<a>"}) == ([], [], [], []))

# decided rows drop out of the queue dict rather than lingering
class _AiApp(_BulkApp):
    pass
_adir = tempfile.mkdtemp(prefix="rp_aiq_")
_astore = rec.RecordStore(directory=_adir)
_am = []
for _i in range(3):
    _m = "<aiq%d@t>" % _i
    _astore.upsert_intake(_m, "2026-07-27T09:0%d:00+00:00" % _i, "RFQ %d" % _i,
                          "z%d@x.com" % _i, {}, True, clf.CAT_QUOTE_ACK, 0.8,
                          "draft", "heuristic", "quote please")
    _am.append(_m)
_aa = _AiApp(_astore)
_aa.checked = set(_am)
_aa.ai_queue = {m: "queued" for m in _am}
_aa._apply_bulk(_am[:2], _app.BULK_KEEP, send=False)
check("deciding a row removes it from the AI queue",
      set(_aa.ai_queue) == {_am[2]}, list(_aa.ai_queue))
check("undecided row stays queued", _aa.ai_queue[_am[2]] == "queued")
# and a decided row is no longer pending, so it can't reappear anywhere
_q2, _in2, _nr2, _ai2 = _app.partition_pending(_astore.pending(), _aa.ai_queue)
check("decided rows vanish from every pending tab",
      all(r["message_id"] == _am[2] for r in _q2 + _in2 + _nr2 + _ai2),
      [r["message_id"] for r in _q2 + _in2 + _nr2 + _ai2])
_astore.close()


# ---- 31. v1.13.0: purchase orders, paperwork, determinism, reclassify ------
# subjects taken verbatim from a live production queue
_PO_SUBJECTS = ["Purchase Order P000020783", "PO: CPCJ338", "PO: CPCJ335",
                "PO# 421184-10691  Job #10691", "Re: PO: NY-33318 / Job: NYC26012",
                "Re: Purchase Order P000020803"]
for _s in _PO_SUBJECTS:
    _r = clf.classify(_s, "buyer@x.com", "Please process this order. Ship to site.")
    check("PO: %s" % _s[:34], _r["category"] == clf.CAT_PURCHASE_ORDER, _r["category"])
# a bid that merely cites a PO number is NOT an order
for _s, _b in [("Re: Bid S100100277  PO# RFQ-503628", "Please quote this bid, QO2100 qty 5"),
               ("Bid S100100309  PO# 240950", "RFQ attached, need pricing on TQD22200 qty 2")]:
    _r = clf.classify(_s, "gc@x.com", _b)
    check("bid citing a PO number stays a quote: %s" % _s[:28],
          _r["category"] != clf.CAT_PURCHASE_ORDER, _r["category"])
check("PO detection anchored to the subject, not a passing mention",
      clf.classify("Re: Bayview THHN", "c@x.com",
                   "We will send a purchase order once you quote. Please price 500ft THHN.")
      ["category"] != clf.CAT_PURCHASE_ORDER)
# routine paperwork
for _s in ["Proof of Delivery G062812-01 Cust P/O P000020698",
           "RE: ASN: 1418949 (P000019760)",
           "Invoice S100100107.002  PO# 1590-075",
           "Packing slip for order 4471", "Remittance advice 88213"]:
    _r = clf.classify(_s, "ap@x.com", "Attached for your records.")
    check("paperwork: %s" % _s[:34], _r["category"] == clf.CAT_TRANSACTIONAL, _r["category"])
# a genuine RFQ is untouched by either new rule
check("RFQ still classifies as quote_ack",
      clf.classify("RFQ - DELIVERY TO 333 EAST 38TH ST", "gc@x.com",
                   "Please quote QO2100 qty 3, need pricing and lead time")
      ["category"] == clf.CAT_QUOTE_ACK)
# taxonomy wiring for the two new categories
for _c in (clf.CAT_PURCHASE_ORDER, clf.CAT_TRANSACTIONAL):
    check("%s is a reply choice" % _c, _c in clf.REPLY_CATEGORIES)
    check("%s has a UI label" % _c, _c in _app.CATEGORY_LABELS)
    _d, _ = drafts.make_draft(_c, "Danny", "danny@x.com", settings=settings)
    check("%s has a real template" % _c, len(_d) > 40 and settings["signature"] in _d)
check("PO template acknowledges an order, does not promise a quote",
      "order" in drafts.make_draft(clf.CAT_PURCHASE_ORDER, "D", "d@x.com", settings=settings)[0].lower())

# determinism: classification pins temperature, polish does not
_src_ce = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "replypilot_classify_engine.py"), encoding="utf-8").read()
check("ollama_call pins temperature when deterministic",
      '"temperature": 0' in _src_ce)
check("deterministic defaults to True", 'deterministic=True' in _src_ce)
_src_de = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "replypilot_draft_engine.py"), encoding="utf-8").read()
check("draft polish opts out of determinism", 'deterministic=False' in _src_de)

# reclassify_pending: rewrites pending, refuses decided
_rdir = tempfile.mkdtemp(prefix="rp_recl_")
_rs = rec.RecordStore(directory=_rdir)
_rs.upsert_intake("<r1@t>", "2026-07-27T08:00:00+00:00", "Purchase Order P1",
                  "b@x.com", {}, True, clf.CAT_QUOTE_ACK, 0.75, "old draft",
                  "heuristic", "Please process this order.")
_rs.upsert_intake("<r2@t>", "2026-07-27T08:01:00+00:00", "RFQ", "c@x.com", {},
                  True, clf.CAT_QUOTE_ACK, 0.75, "old draft", "heuristic", "quote please")
_rs.record_decision("<r2@t>", rec.ACTION_ACCEPTED)
check("reclassify rewrites a pending row",
      _rs.reclassify_pending("<r1@t>", clf.CAT_PURCHASE_ORDER, 0.7, "new draft",
                             "heuristic/template") is True)
_row = _rs.get("<r1@t>")
check("reclassified row carries the new verdict",
      _row["ai_category"] == clf.CAT_PURCHASE_ORDER and _row["ai_draft"] == "new draft")
check("reclassify refuses a decided row",
      _rs.reclassify_pending("<r2@t>", clf.CAT_PURCHASE_ORDER, 0.7, "x", "y") is False)
check("decided row's verdict is preserved exactly",
      _rs.get("<r2@t>")["ai_category"] == clf.CAT_QUOTE_ACK and
      _rs.get("<r2@t>")["ai_draft"] == "old draft")
check("reclassify can set needs_input",
      _rs.reclassify_pending("<r1@t>", clf.CAT_PURCHASE_ORDER, 0.7, "d", "s",
                             needs_input=True) and _rs.get("<r1@t>")["needs_input"] == 1)
check("reclassify on an unknown id is a no-op",
      _rs.reclassify_pending("<nope@t>", clf.CAT_QUOTE_ACK, 0.5, "", "") is False)
check("app exposes reclassify_pending",
      callable(getattr(_app.ReplyPilotApp, "reclassify_pending", None)))
_rs.close()


# ---- 32. v1.14.0: diagnostic bridge ----------------------------------------
os.environ["REPLYIT_DIAG"] = "1"
import importlib as _il
import replyit_diag_bridge as _diag
_il.reload(_diag)
import urllib.request as _ur, urllib.error as _ue
check("bridge is off unless REPLYIT_DIAG=1",
      (os.environ.pop("REPLYIT_DIAG"), _diag.enabled())[1] is False)
os.environ["REPLYIT_DIAG"] = "1"
check("bridge enabled by the env switch", _diag.enabled() is True)

# earlier sections leave the endpoint config wherever they finished, so set
# it explicitly rather than asserting against inherited state
clf.apply_ai_settings({"ai_host": "10.9.9.9", "ai_port": "11434",
                       "ai_host_model": "gemma3:27b",
                       "ai_local_host": "127.0.0.1", "ai_local_port": "11434",
                       "ai_local_model": "gemma3:27b"})
_bdir = tempfile.mkdtemp(prefix="rp_br_")
_bstore2 = rec.RecordStore(directory=_bdir)
_bstore2.upsert_intake("<real@t>", "2026-07-27T08:00:00+00:00", "RFQ real",
                       "c@x.com", {}, True, clf.CAT_QUOTE_ACK, 0.75, "draft",
                       "heuristic", "Please quote QO2100 qty 3")

class _RootStub:
    def after(self, ms, fn):
        _thr.Thread(target=fn, daemon=True).start()
class _DiagApp:
    def __init__(self):
        self.root = _RootStub(); self.store = _bstore2
        self.settings = {"signature": settings["signature"],
                         "auto_send_master": False,
                         "auto_send_delay_sec": 60,
                         "auto_send_min_conf": 0.85,
                         "office_hours_enabled": False}
        self.busy = False; self.ai_queue = {}
        self.auto = auto.AutoSendEngine(_bstore2, self.settings)
        self.learn = le.LearningStore(_bstore2)
        self.refreshes = 0
    def _refresh_lists(self): self.refreshes += 1

_dapp = _DiagApp()
_br = _diag.DiagBridge(_dapp, app_module=_app).start()

def _call(path, payload=None, tok=None, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    r = _ur.Request(_br.base_url() + path, data=data,
                    method=method or ("POST" if data is not None else "GET"))
    r.add_header("X-Diag-Token", _br.token if tok is None else tok)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with _ur.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except _ue.HTTPError as e:
        return e.code, json.loads(e.read().decode())

try:
    check("binds loopback only", _br.httpd.server_address[0] == "127.0.0.1",
          _br.httpd.server_address)
    check("token is long enough to be unguessable", len(_br.token) >= 30)
    check("token file written for local tooling",
          os.path.exists(_br.token_path()))
    _tf = json.load(open(_br.token_path()))
    check("token file carries token/port/pid",
          _tf["token"] == _br.token and _tf["port"] == _br.port and "pid" in _tf)
    # auth
    check("health needs no token", _call("/health", tok="")[0] == 200)
    check("health leaks no data", "rows" not in _call("/health", tok="")[1])
    check("wrong token rejected", _call("/state", tok="nope")[0] == 401)
    check("missing token rejected", _call("/state", tok="")[0] == 401)
    _sc, _sb = _call("/state")
    check("valid token accepted", _sc == 200, (_sc, str(_sb)[:300]))
    # verb mismatch is reported as such, not as a missing endpoint
    _s, _r = _call("/reclassify")
    check("GET on a POST endpoint returns 405", _s == 405 and "POST-only" in _r["error"], _r)
    _s, _r = _call("/state", {})
    check("POST on a GET endpoint returns 405", _s == 405 and "GET-only" in _r["error"], _r)
    check("unknown endpoint returns 404 with an index",
          _call("/nope")[0] == 404 and "GET" in _call("/nope")[1])
    # reads
    _s, _st = _call("/state")
    check("state reports tab counts", _st["tabs"]["queue"] == 1, _st["tabs"])
    check("state reports the data dir", _st["data_dir"] == _bdir)
    _s, _cfg = _call("/config")
    check("config lists both endpoints",
          [e["label"] for e in _cfg["endpoints"]] == ["host", "local"])
    check("config redacts the signature",
          _cfg["settings"]["signature"].startswith("<") and
          "Berson" not in _cfg["settings"]["signature"],
          _cfg["settings"]["signature"])
    # scoring without writing
    _before = len(_bstore2.pending())
    _s, _r = _call("/classify", {"emails": [
        {"subject": "Purchase Order P000020783", "sender": "b@x.com",
         "body": "Please process this order.", "expect": "purchase_order"},
        {"subject": "Proof of Delivery G062812-01", "sender": "a@x.com",
         "body": "Attached.", "expect": "transactional"},
        {"subject": "RFQ", "sender": "c@x.com",
         "body": "Please quote QO2100 qty 3", "expect": "quote_ack"},
    ]})
    check("classify scores against expectations",
          _r["summary"]["scored"] == 3 and _r["summary"]["correct"] == 3,
          _r["summary"])
    check("classify reports accuracy", _r["summary"]["accuracy"] == 1.0)
    check("classify NEVER writes to the corpus",
          len(_bstore2.pending()) == _before, len(_bstore2.pending()))
    _s, _r = _call("/classify", {"emails": [
        {"subject": "x", "sender": "y@z.com", "body": "hello",
         "expect": "quote_ack"}]})
    check("classify lists misses with what it actually said",
          _r["summary"]["misses"] and
          _r["summary"]["misses"][0]["expected"] == "quote_ack", _r["summary"])
    # inject runs the real pipeline
    _s, _r = _call("/inject", {"emails": [
        {"subject": "PO: CPCJ338", "sender": "buyer@x.com",
         "body": "Please process."},
        {"subject": "Do you have a job name and the competition?",
         "sender": "rep@x.com", "body": "Need this to price."}]})
    check("inject runs the real classifier",
          [i["category"] for i in _r["injected"]][0] == clf.CAT_PURCHASE_ORDER,
          _r["injected"])
    check("inject carries the needs_input flag through",
          _r["injected"][1]["needs_input"] is True, _r["injected"][1])
    check("injected rows are really stored",
          len(_bstore2.pending()) == _before + 2)
    check("injected ids are marked synthetic",
          all(i["message_id"].startswith(_diag.DiagBridge.SYNTH_PREFIX)
              for i in _r["injected"]))
    check("inject refreshed the UI via the main thread",
          _dapp.refreshes >= 1)
    # reclassify: pending only
    _bstore2.record_decision("<real@t>", rec.ACTION_ACCEPTED)
    _s, _r = _call("/reclassify", {})
    check("reclassify reports changed/unchanged",
          "changed" in _r and "unchanged" in _r, _r)
    check("reclassify leaves a decided row's verdict alone",
          _bstore2.get("<real@t>")["ai_category"] == clf.CAT_QUOTE_ACK)
    # purge only touches synthetic ids
    _s, _r = _call("/purge_synthetic", {})
    check("purge removes exactly the injected rows", _r["purged"] == 2, _r)
    check("purge leaves real mail alone",
          _bstore2.get("<real@t>") is not None)
    # remaining read endpoints answer
    for _ep in ("/stats", "/pending", "/decided", "/autosend", "/learn",
                "/opslog", "/"):
        check("endpoint %s answers 200" % _ep, _call(_ep)[0] == 200)
    check("email lookup by id",
          _call("/email?id=<real@t>")[1]["message_id"] == "<real@t>")
    check("email lookup reports a miss honestly",
          "error" in _call("/email?id=<nope@t>")[1])
    check("no endpoint imports Outlook COM",
          "win32com" not in open(os.path.join(
              os.path.dirname(os.path.abspath(__file__)),
              "replyit_diag_bridge.py"), encoding="utf-8").read())
finally:
    _br.stop()
    _bstore2.close()
    os.environ.pop("REPLYIT_DIAG", None)
check("token file removed on shutdown", not os.path.exists(_br.token_path()))
check("bridge disabled again once the switch is cleared",
      _diag.enabled() is False)

# ---- 10. COM guarded --------------------------------------------------------
check("COM correctly unavailable on this box", mail.COM_AVAILABLE is False)
try:
    mail.fresh_outlook()
    check("fresh_outlook raises without COM", False)
except RuntimeError:
    check("fresh_outlook raises without COM", True)

print()
if SKIPPED:
    print("SKIPPED: %d section(s) — %s" % (len(SKIPPED), "; ".join(SKIPPED)))
print("RESULT: %s (%d failures)" % ("ALL PASS" if not FAILS else "FAILED", len(FAILS)))
sys.exit(1 if FAILS else 0)
