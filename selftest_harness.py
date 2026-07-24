# selftest_harness.py — ReplyPilot v1.0.0 end-to-end harness (no LLM, no COM)
import os, sys, json, tempfile
os.environ["REPLYPILOT_NO_LLM"] = "1"

import replypilot_mail_engine as mail
import replypilot_classify_engine as clf
import replypilot_draft_engine as drafts
import replypilot_record_engine as rec

FAILS = []
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
check("quote_ack draft greets by first name", d.startswith("Hi Mike,"), d[:30])
check("draft carries signature", settings["signature"] in d)
check("draft source is template", src == "template")
d, _ = drafts.make_draft(clf.CAT_JOB_NAME, "", "estimating@bigbuild.com", settings=settings)
check("job_name draft asks for job name", "job name" in d.lower())
d, _ = drafts.make_draft(clf.CAT_NO_REPLY, "Mike", "m@x.com", settings=settings)
check("no_reply draft is empty", d == "")
d, _ = drafts.make_draft(clf.CAT_QUOTE_ACK, "Torres, Mike", "mtorres@abcelectric.com", settings=settings)
check("Last,First name handled", d.startswith("Hi Mike,"), d[:30])

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
check("ack draft says thank you", "Thank you" in d and d.startswith("Hi Victor,"), d[:60])
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

# ---- 10. COM guarded --------------------------------------------------------
check("COM correctly unavailable on this box", mail.COM_AVAILABLE is False)
try:
    mail.fresh_outlook()
    check("fresh_outlook raises without COM", False)
except RuntimeError:
    check("fresh_outlook raises without COM", True)

print()
print("RESULT: %s (%d failures)" % ("ALL PASS" if not FAILS else "FAILED", len(FAILS)))
sys.exit(1 if FAILS else 0)
