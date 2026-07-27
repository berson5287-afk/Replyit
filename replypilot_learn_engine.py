# replypilot_learn_engine.py
# Replyit Learning Engine v1.0.0
# Imports MaINbox sent-email JSON exports and turns them into training data —
# but ONLY with explicit user confirmation.
#
# Core safety property (the whole point of this design):
#   Staged rows live in their OWN table (learn_staging). Nothing in that table
#   is visible to category_stats() or the graduation math. An AI-inferred
#   category sitting in staging is INERT — it cannot move a category toward
#   auto-send. Only when the user confirms (or corrects) does a row cross over
#   into the real decisions corpus. Untouched rows affect nothing, forever.
#
# Verified against a real MaINbox export (15 records). Schema per record:
#   subject, to, cc, sent_on, sender_name, sender_email, entry_id,
#   conversation_id, internet_message_id, in_reply_to, body
#
# Findings that shaped the parser, all confirmed against that real sample:
#   - in_reply_to is a STRONGER reply signal than the subject prefix, and the
#     two agreed on 15/15 records. Either satisfies the reply filter.
#   - Reply bodies contain the QUOTED ORIGINAL below the reply, with parseable
#     "From:/Sent:/To:/Subject:" headers. So the incoming email is recoverable
#     and categorization can run on it — the same input the live path uses,
#     rather than guessing from the reply text alone.
#   - Pure forwards with no typed reply (and signature-only sends) exist and
#     carry no phrasing signal; they are excluded as unusable.

ENGINE_VERSION = "1.3.0"  # v1.3.0: quote_in_process on the reply side
# v1.2.0: second production retune — quote-delivery outranks decline clauses,
#         "but we can supply" withdraws a decline, outbound stock-checks,
#         wider commitment phrasing, stable fallback stage_id
# v1.1.0: retuned against a real production import — short "<Name>,"
#         sign-off stripping, greeting stripping before anchored matches,
#         attachment-based quote delivery, "will advise" vs "please advise",
#         wider outbound detection, and internal own-domain threads flagged.

import os
import re
import json
import hashlib
import sqlite3
import threading
from datetime import datetime, timezone

import replypilot_classify_engine as clf

# ------------------------------------------------------------------ statuses
STATUS_STAGED = "staged"        # inert — no effect on anything
STATUS_CONFIRMED = "confirmed"  # user agreed with the inferred category
STATUS_CORRECTED = "corrected"  # user picked a different category
STATUS_IGNORED = "ignored"      # user explicitly dismissed it

INERT_STATUSES = (STATUS_STAGED, STATUS_IGNORED)
TRAINING_STATUSES = (STATUS_CONFIRMED, STATUS_CORRECTED)

# ------------------------------------------------------------------- parsing
# Quoted-original header block, Outlook plain-text style. Kept tolerant of
# leading whitespace and ">" quote markers.
_QUOTE_START_RE = re.compile(r"^[ \t>]*From:\s*.+$", re.M)
_QUOTE_HDR_RE = re.compile(
    r"^[ \t>]*From:\s*(?P<from>.+?)\s*$\s*"
    r"(?:^[ \t>]*Sent:\s*(?P<sent>.+?)\s*$\s*)?"
    r"(?:^[ \t>]*To:\s*(?P<to>.+?)\s*$\s*)?"
    r"(?:^[ \t>]*(?:Cc|CC):\s*.+?\s*$\s*)?"
    r"(?:^[ \t>]*Subject:\s*(?P<subject>.+?)\s*$)", re.M)
# "On <date> <someone> wrote:" style, incl. the MaINbox-known case where
# Outlook wraps this marker across two lines.
_ON_WROTE_RE = re.compile(r"^[ \t>]*On\s.{0,200}?\bwrote:\s*$", re.M | re.S)

_ADDR_RE = re.compile(r"<([^<>@\s]+@[^<>\s]+)>")
_BARE_ADDR_RE = re.compile(r"\b([^<>@\s]+@[^<>\s]+\.[A-Za-z]{2,})\b")

# Default signature markers. Overridable per install — a different user has a
# different signature, so this is configuration, not a hardcoded assumption.
DEFAULT_SIGNATURE_MARKERS = (
    r"\*\*NOW HIRING",
    r"Managing Member",
    r"Inquiries & referrals welcome",
)

# v1.1.0: real exports showed a SHORT sign-off variant ("...Thanks! Steve
# Berson,") that carries none of the long-signature markers, so the name was
# leaking into reply_text and polluting both the inference and the phrasing
# library. Stripped separately, anchored to the end of the reply.
DEFAULT_SIGNOFF_NAMES = ("Steve Berson",)

_GREETING_RE = re.compile(
    r"^\s*(good (morning|afternoon|evening)|hi|hey|hello|dear)\b\s*,?\s*"
    # optionally a first name right after the greeting ("Good afternoon,
    # Matt,"). Guarded by a stopword list so ordinary sentence openers
    # ("Good afternoon, The last item...") are not mistaken for a name.
    r"(?:(?!(?:The|This|That|These|Not|Please|Attached|Enclosed|Sorry|We|I|"
    r"It|My|Your|Here|Just|Can|Could|Would|Should|Thanks|Thank|Also|As|At|"
    r"On|In|For|If|No|Yes|Per|Any|Do|Did|Is|Are|Was|Were|Let|Sure)\b)"
    r"[A-Z][a-zA-Z'-]{1,15}\s*,\s*)?",
    re.I)   # NOT re.X — verbose mode would swallow the literal spaces in
            # "good afternoon" and the pattern would never match


def strip_greeting(text):
    """Drop a leading salutation, including a first name if one follows.

    Anchored patterns (a reply that *opens* with "Thanks") were failing on
    real mail because nearly every reply starts "Good afternoon,". Removing
    the greeting first is what lets those patterns see the actual opener.
    """
    if not text:
        return ""
    return _GREETING_RE.sub("", text, count=1).strip()


def strip_signoff(text, names=None):
    """Remove a trailing "<Name>," sign-off block.

    Only whitespace and separator commas are consumed ahead of the name —
    eating "!" or "." would take punctuation that belongs to the message
    ("Thanks! Steve Berson," must reduce to "Thanks!", not "Thanks").
    """
    if not text:
        return ""
    out = text
    for name in (names or DEFAULT_SIGNOFF_NAMES):
        out = re.sub(r"[ \t\r\n]*,?[ \t\r\n]*" + re.escape(name) +
                     r"\s*[,.]?\s*$", "", out, flags=re.I)
    return out.strip()

_REPLY_PREFIX_RE = re.compile(r"^\s*(RE|FW|FWD)\s*:", re.I)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def looks_like_reply(record):
    """True if this sent item is a reply/forward of an inbound email.
    in_reply_to is the primary signal (drift-proof, set by the mail system);
    the RE:/FW: subject prefix is the fallback for records that lack it."""
    if (record.get("in_reply_to") or "").strip():
        return True
    return bool(_REPLY_PREFIX_RE.match(record.get("subject") or ""))


def strip_signature(text, markers=None):
    """Cut everything from the first signature marker onward."""
    if not text:
        return ""
    earliest = None
    for pat in (markers or DEFAULT_SIGNATURE_MARKERS):
        m = re.search(pat, text, re.I)
        if m and (earliest is None or m.start() < earliest):
            earliest = m.start()
    return text[:earliest] if earliest is not None else text


def _normalize(text):
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def split_body(body, signature_markers=None):
    """Split a sent body into (reply_text, quoted_original).

    reply_text is what the user actually typed, with the signature removed.
    quoted_original is the inbound email that was replied to, headers and all.
    """
    if not body:
        return "", ""
    starts = [m.start() for m in (_QUOTE_START_RE.search(body),
                                  _ON_WROTE_RE.search(body)) if m]
    if starts:
        cut = min(starts)
        reply_raw, original = body[:cut], body[cut:]
    else:
        reply_raw, original = body, ""
    reply_raw = strip_signature(reply_raw, signature_markers)
    reply = _normalize(reply_raw)
    reply = strip_signoff(reply)   # v1.1.0: short "Steve Berson," sign-off
    return reply, _normalize(original)


def parse_quoted_headers(quoted_original):
    """Pull From/Sent/To/Subject out of the quoted block.
    Returns dict with from_name, from_email, sent, subject (may be blank)."""
    out = {"from_name": "", "from_email": "", "sent": "", "subject": ""}
    if not quoted_original:
        return out
    m = _QUOTE_HDR_RE.search(quoted_original)
    if not m:
        return out
    frm = (m.group("from") or "").strip()
    out["sent"] = (m.group("sent") or "").strip()
    out["subject"] = (m.group("subject") or "").strip()
    am = _ADDR_RE.search(frm) or _BARE_ADDR_RE.search(frm)
    if am:
        out["from_email"] = am.group(1)
        out["from_name"] = frm[:am.start()].strip().strip('"').strip()
    else:
        out["from_name"] = frm.strip('"')
    return out


def original_body_text(quoted_original):
    """The inbound email's body — quoted block minus its header lines."""
    if not quoted_original:
        return ""
    m = _QUOTE_HDR_RE.search(quoted_original)
    if m:
        return _normalize(quoted_original[m.end():])
    # header parse failed; drop the first line and use the rest
    parts = quoted_original.split("\n", 1)
    return _normalize(parts[1]) if len(parts) > 1 else ""


def parse_sent_record(record, signature_markers=None):
    """Parse one MaINbox sent record into a staging candidate dict.
    Returns the dict, or None if the record is unusable for learning."""
    body = record.get("body") or ""
    reply_text, quoted = split_body(body, signature_markers)
    if not reply_text.strip():
        # pure forward with no typed reply, or signature-only send — there is
        # no phrasing to learn and no decision to label
        return None
    hdrs = parse_quoted_headers(quoted)
    return {
        "sender_email": (record.get("sender_email") or "").strip(),
        "source_message_id": (record.get("internet_message_id") or "").strip(),
        "in_reply_to": (record.get("in_reply_to") or "").strip(),
        "sent_on": (record.get("sent_on") or "").strip(),
        "to_addr": (record.get("to") or "").strip(),
        "subject": (record.get("subject") or "").strip(),
        "reply_text": reply_text,
        "orig_from_name": hdrs["from_name"],
        "orig_from_email": hdrs["from_email"],
        "orig_subject": hdrs["subject"],
        "orig_body": original_body_text(quoted),
        "has_original": bool(quoted.strip()),
    }


def load_sent_json(path):
    """Read a MaINbox sent export. Accepts a bare list or a dict wrapping one
    under a common key, since exporters vary."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("records", "items", "sent", "emails", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    raise ValueError("Unrecognized sent-export structure: expected a list "
                     "of records or a dict containing one")


def select_candidates(records, limit=None, replies_only=True,
                      signature_markers=None, own_domain=""):
    """Filter to usable reply records and cap at `limit` (newest first).
    Returns (candidates, stats_dict)."""
    stats = {"total": len(records), "not_reply": 0, "no_reply_text": 0,
             "outbound_followup": 0, "internal": 0, "usable": 0}
    # the user's own domain, taken from what they send as
    if not own_domain:
        own_domain = derive_own_domain(records)
    stats["own_domain"] = own_domain
    rows = list(records)
    rows.sort(key=lambda r: (r.get("sent_on") or ""), reverse=True)
    out = []
    for r in rows:
        if replies_only and not looks_like_reply(r):
            stats["not_reply"] += 1
            continue
        parsed = parse_sent_record(r, signature_markers)
        if parsed is None:
            stats["no_reply_text"] += 1
            continue
        parsed["outbound_followup"] = looks_outbound_followup(parsed)
        if parsed["outbound_followup"]:
            stats["outbound_followup"] += 1
        parsed["internal"] = looks_internal(parsed, own_domain)
        if parsed["internal"]:
            stats["internal"] += 1
        out.append(parsed)
        if limit and len(out) >= int(limit):
            break
    stats["usable"] = len(out)
    return out, stats


# --------------------------------------------------------------- inference
def normalize_quotes(text):
    """Fold smart punctuation to ASCII before matching.

    Not cosmetic: real Outlook mail is full of U+2019 curly apostrophes, and
    a pattern written with a straight quote silently fails to match "don’t".
    That single character was mis-binning genuine no_quote replies.
    """
    if not text:
        return ""
    return (text.replace("\u2019", "'").replace("\u2018", "'")
                .replace("\u201c", '"').replace("\u201d", '"')
                .replace("\u2013", "-").replace("\u2014", "-")
                .replace("\u00a0", " "))


# The user's own outbound-RFQ voice. A reply continuing a thread the user
# STARTED (asking a vendor to quote *them*) is the opposite direction from
# what Replyit learns — Replyit responds to inbound mail. Real example from
# the export: "Sorry forgot an item please add below." is an addendum to the
# user's own P&A request, not a response to a customer.
_R_OUTBOUND_RE = re.compile(
    r"(please provide me with price and avail|provide me with p\s*&\s*a|"
    r"please (quote|add|price) (the |below|these)|forgot an item|"
    r"please add below|add (this|these) to (my|the) (rfq|request))", re.I)


def looks_outbound_followup(candidate):
    """True if this reply continues the user's own outbound request."""
    t = normalize_quotes(candidate.get("reply_text", ""))
    if _R_OUTBOUND_RE.search(t):
        return True
    # no recoverable inbound sender + outbound phrasing in the quoted thread
    if not candidate.get("orig_from_email"):
        if _R_OUTBOUND_RE.search(normalize_quotes(
                candidate.get("orig_body", ""))):
            return True
    return False


_TO_HDR_RE = re.compile(r"^[ \t>]*To:\s*(?P<to>.+?)\s*$", re.M)
_ANY_ADDR_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def derive_own_domain(records):
    """Work out the user's own mail domain from the export.

    Not from sender_email: on Exchange that field is a legacy DN
    ("/O=EXCHANGELABS/OU=.../CN=RECIPIENTS/CN=...") with no domain in it at
    all — the same class of trap as EntryID. The dependable signal is the
    quoted "To:" line of the inbound mail, because inbound mail is addressed
    TO the user. Most common domain there wins.
    """
    counts = {}
    for r in records:
        _reply, quoted = split_body(r.get("body") or "")
        m = _TO_HDR_RE.search(quoted or "")
        if not m:
            continue
        for addr in _ANY_ADDR_RE.findall(m.group("to")):
            dom = addr.split("@", 1)[1].lower()
            counts[dom] = counts.get(dom, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda kv: kv[1])[0]


def email_domain(addr):
    a = (addr or "").strip().lower()
    return a.split("@", 1)[1] if "@" in a else ""


def looks_internal(candidate, own_domain=""):
    """True if this exchange is with a colleague on the user's own domain.

    Internal coordination ("Matt, can you match 13/16 @ $1.10?") is not a
    customer reply, and training on it would teach Replyit to answer clients
    the way the user talks to their own team.
    """
    if not own_domain:
        return False
    for addr in (candidate.get("orig_from_email"), candidate.get("to_addr")):
        if addr and email_domain(addr) == own_domain:
            return True
    return False


def infer_category(candidate):
    """Infer which RESPONSE TYPE the user chose for this email.

    The reply text is primary, and that ordering is deliberate. Classifying
    the inbound email tells you what kind of mail arrived — but the label we
    actually need is which response the user picked, and the reply *is* that
    response. Measured against the real export, inbound-first collapsed
    almost everything to quote_ack (the mail was nearly all quote requests)
    while the replies themselves were declines, job-name asks, and delivered
    prices. Inbound classification is kept as a fallback for records whose
    reply is too terse to read.

    Returns (category, confidence, source).
    """
    if candidate.get("internal"):
        # colleague thread — surface it, don't learn client voice from it
        return clf.CAT_ESCALATE, 0.15, "internal_thread"
    if candidate.get("outbound_followup"):
        # wrong direction to learn from — surface it, don't guess a category
        return clf.CAT_ESCALATE, 0.15, "outbound_followup"
    cat, conf = infer_from_reply_text(candidate.get("reply_text", ""))
    if conf >= 0.4:
        return cat, conf, "reply_text"
    if candidate.get("orig_body") or candidate.get("orig_subject"):
        res = clf.classify(candidate.get("orig_subject", ""),
                           candidate.get("orig_from_email", ""),
                           candidate.get("orig_body", ""))
        # inbound is a weaker proxy for the chosen response — cap its claim
        return res["category"], min(res["confidence"], 0.45), \
            "inbound/" + res["source"]
    return cat, conf, "reply_text"


# Reply-side cues, ordered by how decisive they are. Everything here still
# requires user confirmation before it counts for anything.
# v1.1.0: rebuilt against a real production import — the first pass leaned on
# phrasings that barely occur in this user's actual mail.

# Delivered pricing. Two routes, because real quote deliveries frequently
# carry NO dollar figure at all — the numbers are in the attachment
# ("attached is your quote"). Requiring a "$" missed those entirely.
_R_PRICE_RE = re.compile(r"\$\s*\d[\d,]*(\.\d{1,4})?")
_R_STOCKISH_RE = re.compile(
    r"(in stock|we have (these|this|it)|your cost|our cost|lead ?time|"
    r"stock oh|available)", re.I)
_R_QUOTE_ATTACHED_RE = re.compile(
    r"((attached|enclosed) (is|are|you.ll find) (your|the|our)?\s*"
    r"(revised |updated )?(quote|quotation|pricing|proposal)|"
    r"(your|the) (revised |updated )?(quote|quotation) is attached|"
    r"here (is|are) (your|the) (revised |updated )?(quote|quotation|pricing)|"
    r"attached is your (revised |updated )?quote)", re.I)

# Asking someone ELSE to quote — the user is the requester here, which is the
# opposite direction from what Replyit learns.
_R_OUTBOUND_RE = re.compile(
    r"(please provide me with price and avail|provide me with p\s*&\s*a|"
    r"please (quote|add|price) (the |below|these)|forgot an item|"
    r"please add below|add (this|these) to (my|the) (rfq|request)|"
    r"see if we can quote|can we quote|are we able to quote|"
    r"please quote this for me|quote this for me|"
    r"take a look at below and see|"
    # asking a vendor about their stock / for their pricing
    r"if so,? please quote|do you have stock on|do you have any stock|"
    r"is this all in stock|are these in stock|do you (have|carry|stock) "
    r"(this|these|any)|can you supply|are you able to supply)", re.I)

_R_NOQUOTE_RE = re.compile(
    r"(we don'?t (sell|stock|carry)|we do not (sell|stock|carry)|"
    r"not able to quote|can'?t quote|cannot quote|won'?t be able to quote|"
    r"we'?ll pass|no bid|came up empty|empty handed|"
    r"(couldn'?t|could not|can'?t|cannot) find (it|this|any|them)|"
    r"no luck (finding|on)|not something we)", re.I)
# A decline that turns positive: "we don't stock much BUT we can supply them."
# The negative clause is real but the message is not a decline, so the
# no_quote verdict is withdrawn when a capability clause follows.
_R_BUT_CAN_RE = re.compile(
    r"but we (can|could|do|are able to)|we can (still |certainly )?"
    r"(supply|get|order|bring)|we can supply|able to supply", re.I)

_R_JOBNAME_RE = re.compile(
    r"(what job (is )?this is for|what job this is|which job|job name|"
    r"what job|name of the job)", re.I)
# "will advise" = I will get back to you. Distinct from "please advise",
# which is the user asking THEM — that one is need_info, below.
_R_WORKING_RE = re.compile(
    r"(being worked on|working on (it|this)|i am working on|"
    r"i am still working|get back to you|have it over shortly|over shortly|"
    r"will send|send it over|will advise|let me get (this|that|you)|"
    r"let me check|looking into (it|this)|i'?ll look into|i'?ll check|"
    r"i'?ll find out|i will (get|have|update)|i'?ll get (this|that|your|it)|"
    r"will get (this|that|your|it)|keep you posted|forwarded your request)",
    re.I)
# The user asking THEM for something back.
_R_PLEASE_ADVISE_RE = re.compile(
    r"(please[^.?!\n]{0,60}\badvise|please confirm|please clarify|"
    r"please let me know|please review|please fill|please send me|"
    r"please provide|if you don'?t mind[^.?!\n]{0,40}please)", re.I)
_R_QUESTION_RE = re.compile(
    r"\?|can you (please )?(let me know|confirm|send|advise|match|verify)|"
    r"do you want|which one|need more", re.I)
_R_ACK_RE = re.compile(
    r"^(ty\b|thank you|thanks|received|got it|will do|noted|perfect|"
    r"sounds good|appreciate it)", re.I)
# v1.3.0: "Yes — this is being worked on..." answers a chase
_R_CONFIRM_OPEN_RE = re.compile(r"^(yes|yep|yup|correct|it is)\b", re.I)


def infer_from_reply_text(reply_text):
    """Infer the chosen response type from the reply. Returns (cat, conf).

    Order is load-bearing. Delivering a quote is checked before declining,
    because a real delivery routinely contains a decline clause about one
    line item: "Attached is your revised quote, left off the D rings which
    we don't stock..." is a quote delivery, and reading it as no_quote tells
    the customer the opposite of what was actually sent.
    """
    raw = normalize_quotes((reply_text or "").strip())
    t = strip_signoff(strip_greeting(raw))
    if not t:
        return clf.CAT_ESCALATE, 0.2
    # Direction first: if the user is asking someone else to quote or to
    # check stock, no response-type label applies at all.
    if _R_OUTBOUND_RE.search(t):
        return clf.CAT_ESCALATE, 0.15
    # Delivered quote outranks both the "Thanks" opener and any embedded
    # decline clause.
    if _R_QUOTE_ATTACHED_RE.search(t):
        return clf.CAT_QUOTE_DELIVERED, 0.75
    if _R_PRICE_RE.search(t) and _R_STOCKISH_RE.search(t):
        return clf.CAT_QUOTE_DELIVERED, 0.7
    if _R_NOQUOTE_RE.search(t) and not _R_BUT_CAN_RE.search(t):
        return clf.CAT_NO_QUOTE, 0.7
    if _R_JOBNAME_RE.search(t):
        return clf.CAT_JOB_NAME, 0.7
    if _R_WORKING_RE.search(t):
        if _R_JOBNAME_RE.search(t):
            return clf.CAT_JOB_NAME, 0.7
        # a reply that OPENS by confirming ("Yes, this is being worked on")
        # is answering a chase, not acknowledging a new RFQ
        if _R_CONFIRM_OPEN_RE.match(t) or "patience" in t.lower():
            return clf.CAT_QUOTE_IN_PROCESS, 0.65
        return clf.CAT_QUOTE_ACK, 0.65
    if _R_PLEASE_ADVISE_RE.search(t):
        return clf.CAT_NEED_INFO, 0.65
    if _R_QUESTION_RE.search(t):
        return clf.CAT_NEED_INFO, 0.6
    # Pure thanks, and short — a long message that merely opens with "Thanks"
    # is doing something else, and the checks above have already had a look.
    if _R_ACK_RE.match(t) and len(t) <= 120:
        return clf.CAT_ACK, 0.6
    return clf.CAT_ESCALATE, 0.25


# ------------------------------------------------------------ staging store
_STAGING_SCHEMA = """
CREATE TABLE IF NOT EXISTS learn_staging (
    stage_id          TEXT PRIMARY KEY,
    source_message_id TEXT,
    in_reply_to       TEXT,
    sent_on           TEXT,
    to_addr           TEXT,
    subject           TEXT,
    reply_text        TEXT,
    orig_from_name    TEXT,
    orig_from_email   TEXT,
    orig_subject      TEXT,
    orig_body         TEXT,
    has_original      INTEGER NOT NULL DEFAULT 0,
    ai_category       TEXT,
    ai_confidence     REAL,
    ai_source         TEXT,
    status            TEXT NOT NULL DEFAULT 'staged',
    final_category    TEXT,
    created_at        TEXT NOT NULL,
    decided_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_staging_status ON learn_staging(status);
"""


def stage_id_for(candidate):
    """Stable id so re-importing the same export never duplicates rows.

    The fallback deliberately excludes reply_text. Hashing the parsed reply
    made the id move whenever the parser improved, so the same email staged
    again as a second row after a retune — visible as duplicate entries in
    the review list. Only fields that describe the message itself are used.
    """
    basis = (candidate.get("source_message_id")
             or "%s|%s|%s" % (candidate.get("sent_on", ""),
                              candidate.get("subject", ""),
                              candidate.get("to_addr", "")))
    return hashlib.md5(basis.encode("utf-8", "replace")).hexdigest()


class LearningStore:
    """Staging area for imported sent replies.

    Shares the Replyit SQLite file but lives in its own table. No method here
    writes to `decisions` except promote(), which runs only on an explicit
    user confirm/correct.
    """

    def __init__(self, record_store):
        self.rs = record_store
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.rs.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_STAGING_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------- ingestion
    def stage(self, candidates):
        """Insert candidates with inferred categories. Idempotent per
        stage_id. Returns (new_count, skipped_count)."""
        new, skipped = 0, 0
        for cand in candidates:
            sid = stage_id_for(cand)
            with self._lock:
                cur = self._conn.execute(
                    "SELECT 1 FROM learn_staging WHERE stage_id=?", (sid,))
                if cur.fetchone() is not None:
                    skipped += 1
                    continue
            cat, conf, src = infer_category(cand)
            with self._lock:
                self._conn.execute(
                    """INSERT INTO learn_staging
                       (stage_id, source_message_id, in_reply_to, sent_on,
                        to_addr, subject, reply_text, orig_from_name,
                        orig_from_email, orig_subject, orig_body,
                        has_original, ai_category, ai_confidence, ai_source,
                        status, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (sid, cand.get("source_message_id", ""),
                     cand.get("in_reply_to", ""), cand.get("sent_on", ""),
                     cand.get("to_addr", ""), cand.get("subject", ""),
                     cand.get("reply_text", ""),
                     cand.get("orig_from_name", ""),
                     cand.get("orig_from_email", ""),
                     cand.get("orig_subject", ""), cand.get("orig_body", ""),
                     1 if cand.get("has_original") else 0,
                     cat, float(conf), src, STATUS_STAGED, _now()))
                self._conn.commit()
            new += 1
        self.rs._audit("learn_import", {"new": new, "skipped": skipped})
        return new, skipped

    # ---------------------------------------------------------------- reads
    def by_status(self, status):
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM learn_staging WHERE status=? "
                "ORDER BY sent_on DESC", (status,))
            return [dict(r) for r in cur.fetchall()]

    def get(self, stage_id):
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM learn_staging WHERE stage_id=?", (stage_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def counts(self):
        with self._lock:
            cur = self._conn.execute(
                "SELECT status, COUNT(*) n FROM learn_staging GROUP BY status")
            return {r["status"]: r["n"] for r in cur.fetchall()}

    # ------------------------------------------------------------ decisions
    def confirm(self, stage_id, final_category=None):
        """Confirm (or correct) a staged row and PROMOTE it into the real
        corpus. This is the only path by which imported data affects
        learning. Returns (ok, action) where action is 'confirmed' or
        'corrected'."""
        row = self.get(stage_id)
        if row is None or row["status"] in TRAINING_STATUSES:
            return False, None
        inferred = row["ai_category"]
        final = final_category or inferred
        corrected = (final != inferred)
        status = STATUS_CORRECTED if corrected else STATUS_CONFIRMED
        ok = self.rs.import_decided_record(
            message_id="learn:%s" % stage_id,
            received_at=row["sent_on"] or "",
            subject=row["orig_subject"] or row["subject"] or "",
            sender=row["orig_from_email"] or row["to_addr"] or "",
            features={"imported": True,
                      "has_original": bool(row["has_original"])},
            ai_category=inferred,
            ai_confidence=row["ai_confidence"] or 0.0,
            ai_draft=row["reply_text"] or "",
            ai_source="import/%s" % (row["ai_source"] or ""),
            final_category=final,
            final_draft=row["reply_text"] or "",
            corrected=corrected,
            body_full=row["orig_body"] or "")
        if not ok:
            return False, None
        with self._lock:
            self._conn.execute(
                "UPDATE learn_staging SET status=?, final_category=?, "
                "decided_at=? WHERE stage_id=?",
                (status, final, _now(), stage_id))
            self._conn.commit()
        self.rs._audit("learn_promote", {"stage_id": stage_id,
                                         "inferred": inferred,
                                         "final": final,
                                         "corrected": corrected})
        return True, status

    def ignore(self, stage_id):
        """Dismiss a staged row. Still inert — nothing is promoted."""
        with self._lock:
            self._conn.execute(
                "UPDATE learn_staging SET status=?, decided_at=? "
                "WHERE stage_id=? AND status=?",
                (STATUS_IGNORED, _now(), stage_id, STATUS_STAGED))
            self._conn.commit()
        return True

    def unstage_all(self):
        """Drop every row that has NOT been promoted. Confirmed/corrected
        rows are left alone — they are already part of the corpus."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM learn_staging WHERE status IN (?,?)",
                INERT_STATUSES)
            n = cur.rowcount
            self._conn.commit()
        self.rs._audit("learn_unstage_all", {"removed": n})
        return n

    # ------------------------------------------------------- phrasing library
    def phrasing_examples(self, category, limit=5):
        """Confirmed reply texts for a category — how the user actually
        phrases this kind of response. Only promoted rows qualify, so
        unconfirmed inferences never leak into drafting either."""
        ph = ",".join("?" * len(TRAINING_STATUSES))
        with self._lock:
            cur = self._conn.execute(
                "SELECT reply_text FROM learn_staging "
                "WHERE final_category=? AND status IN (%s) "
                "AND LENGTH(reply_text) BETWEEN 15 AND 600 "
                "ORDER BY sent_on DESC LIMIT ?" % ph,
                (category,) + TRAINING_STATUSES + (int(limit),))
            return [r["reply_text"] for r in cur.fetchall()]

    def close(self):
        with self._lock:
            self._conn.close()
