# replypilot_classify_engine.py
# ReplyPilot Classification Engine v1.0.0
# Two-stage classifier: deterministic heuristics always run first and produce
# a full result; the LLM (Ollama, gemma3 on tillium-bridge) refines it when
# reachable. If the LLM is down, slow, or returns garbage, heuristics stand.
#
# Closed taxonomy — categories are FIXED. This is what makes the learning
# loop measurable. Do not add free-form categories.

ENGINE_VERSION = "1.9.0"  # v1.9.0: purchase_order + transactional categories
                          # v1.8.0: deterministic classification (temp 0)
                          # v1.7.0: needs_input flag (orthogonal to category)
                          # v1.6.0: quote_in_process (chase detection)
                          # v1.5.0: list_models / model_available
                          # v1.4.0: quote_delivered category (real sent-mail
                          # evidence: answering with the price right now)
                          # v1.3.0: runtime-configurable AI settings
                          # (apply_ai_settings) driven by the Settings UI
                          # v1.2.0: host-first Ollama with automatic local
                          # fallback (tillium down -> local), MaINbox pattern
                          # v1.1.0: acknowledgement category
                          # v1.0.1: stock-check phrasing + part-in-question rule

import os
import re
import json
import urllib.request

# ------------------------------------------------------------------- taxonomy
CAT_QUOTE_ACK = "quote_ack"    # RFQ received -> "I'll get back to you shortly"
CAT_NO_QUOTE = "no_quote"      # we decline to quote
CAT_NEED_INFO = "need_info"    # can't price without more detail
CAT_JOB_NAME = "job_name"      # need the job name to register/price
CAT_ESCALATE = "escalate"      # human must handle (complaint, legal, odd)
CAT_NO_REPLY = "no_reply"      # no response needed
CAT_ACK = "acknowledgement"    # v1.1.0: vendor delivered P&A/quote/info we
                               # asked for -> brief thank-you
CAT_QUOTE_DELIVERED = "quote_delivered"
# v1.4.0: answering with the actual price/availability right now, rather than
# promising to follow up. Added because the real sent-mail export showed this
# is a distinct, frequent response ("$15.73. We have these in stock. TY" /
# "Your cost is $100 we have this all in stock.") that the taxonomy had no
# home for — those replies were being mis-binned as quote_ack, which says the
# opposite thing to the customer.

CAT_PURCHASE_ORDER = "purchase_order"
# v1.9.0: an inbound ORDER, not a request to quote. Roughly a sixth of a real
# production queue was purchase orders being answered "I'll get back to you
# with pricing and availability" — which tells a customer who just bought
# something that their order was read as an enquiry. A PO needs
# acknowledgement and confirmation, which is a different reply entirely.
CAT_TRANSACTIONAL = "transactional"
# v1.9.0: proof of delivery, invoices, ASNs, packing slips, remittances.
# Routine paperwork that usually needs routing rather than a reply, and
# certainly not a quote.
CAT_QUOTE_IN_PROCESS = "quote_in_process"
# v1.6.0: the customer is CHASING an RFQ already in hand ("This coming over
# soon?"), so the reply confirms work is underway rather than acknowledging a
# new request. Distinct from quote_ack, which is the first response to a
# fresh RFQ — telling a chaser "thanks for the RFQ, received" reads as though
# their original was never seen.

CATEGORIES = (CAT_QUOTE_ACK, CAT_QUOTE_IN_PROCESS, CAT_QUOTE_DELIVERED,
              CAT_NO_QUOTE, CAT_PURCHASE_ORDER, CAT_TRANSACTIONAL,
              CAT_NEED_INFO, CAT_JOB_NAME, CAT_ESCALATE,
              CAT_ACK, CAT_NO_REPLY)

# Categories that appear as reply choices in the UI (everything but no_reply)
REPLY_CATEGORIES = (CAT_QUOTE_ACK, CAT_QUOTE_IN_PROCESS, CAT_QUOTE_DELIVERED,
                    CAT_NO_QUOTE, CAT_PURCHASE_ORDER, CAT_TRANSACTIONAL,
                    CAT_NEED_INFO, CAT_JOB_NAME, CAT_ACK, CAT_ESCALATE)

# The quote family — grouped under one dropdown in the review UI so the
# response-type row stops overflowing as the taxonomy grows.
QUOTE_CATEGORIES = (CAT_QUOTE_ACK, CAT_QUOTE_IN_PROCESS,
                    CAT_QUOTE_DELIVERED, CAT_NO_QUOTE)

# ------------------------------------------------------------------ LLM config
# Host-first with automatic local fallback, mirroring MaINbox: heavy calls
# prefer the Tailscale host (tillium-bridge) and fall back to a local Ollama
# on this PC when the host is down OR busy/timing out. Endpoints are tried in
# order; the first that answers wins.
OLLAMA_HOST = os.environ.get("REPLYPILOT_OLLAMA_HOST", "100.89.98.118")
OLLAMA_PORT = int(os.environ.get("REPLYPILOT_OLLAMA_PORT", "11434"))
OLLAMA_MODEL = os.environ.get("REPLYPILOT_OLLAMA_MODEL", "gemma3:27b")
OLLAMA_TIMEOUT = int(os.environ.get("REPLYPILOT_OLLAMA_TIMEOUT", "30"))
NO_LLM = os.environ.get("REPLYPILOT_NO_LLM", "") == "1"

# Local fallback (this PC). Model can differ from the host's — a lighter
# local model is fine since it only runs when the host is unavailable.
LOCAL_OLLAMA_HOST = os.environ.get("REPLYPILOT_LOCAL_HOST", "127.0.0.1")
LOCAL_OLLAMA_PORT = int(os.environ.get("REPLYPILOT_LOCAL_PORT", "11434"))
LOCAL_OLLAMA_MODEL = os.environ.get("REPLYPILOT_LOCAL_MODEL", OLLAMA_MODEL)
# Fallback probe is short so a dead host doesn't burn the full timeout before
# we even try local.
OLLAMA_HOST_PROBE = int(os.environ.get("REPLYPILOT_HOST_PROBE", "3"))


def _endpoints():
    """Ordered (label, host, port, model) list: host first, then local.
    If host and local resolve to the same host:port, the duplicate is
    dropped so we don't call the same dead endpoint twice."""
    eps = [("host", OLLAMA_HOST, OLLAMA_PORT, OLLAMA_MODEL)]
    if (LOCAL_OLLAMA_HOST, LOCAL_OLLAMA_PORT) != (OLLAMA_HOST, OLLAMA_PORT):
        eps.append(("local", LOCAL_OLLAMA_HOST, LOCAL_OLLAMA_PORT,
                    LOCAL_OLLAMA_MODEL))
    return eps


def endpoint_reachable(host, port, timeout=None):
    try:
        req = urllib.request.Request("http://%s:%d/api/tags" % (host, port))
        with urllib.request.urlopen(
                req, timeout=timeout or OLLAMA_HOST_PROBE) as r:
            r.read(200)
        return True
    except Exception:
        return False


def any_endpoint_reachable(timeout=None):
    """v1.2.0: True if host OR local answers — used by AI Review preflight so
    it only fails when BOTH are down."""
    return any(endpoint_reachable(h, p, timeout) for _l, h, p, _m in _endpoints())


def list_models(host, port, timeout=5):
    """v1.5.0: model tags actually installed on an endpoint.

    This is what makes "model not found" preventable rather than a runtime
    HTTPError — the UI can offer the real list instead of a typed guess.
    Returns a sorted list of names, or [] if the endpoint can't be reached.
    """
    try:
        req = urllib.request.Request("http://%s:%d/api/tags" % (host, port))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return []
    names = []
    for m in (data.get("models") or []):
        n = (m.get("name") or m.get("model") or "").strip()
        if n:
            names.append(n)
    return sorted(set(names))


def endpoint_models(timeout=5):
    """(host_models, local_models) for the currently configured endpoints."""
    eps = {label: (h, p) for label, h, p, _m in _endpoints()}
    host = list_models(*eps["host"], timeout=timeout) if "host" in eps else []
    local = (list_models(*eps["local"], timeout=timeout)
             if "local" in eps else host)
    return host, local


def model_available(host, port, model, timeout=5):
    """True if `model` is installed on that endpoint (tag match, and a bare
    name matches its :latest tag)."""
    installed = list_models(host, port, timeout)
    if not installed:
        return False
    if model in installed:
        return True
    base = model.split(":")[0]
    return any(m == base or m.startswith(base + ":") for m in installed)


def active_endpoint_label(timeout=None):
    """Which endpoint would serve right now ('host'/'local'/None)."""
    for label, h, p, _m in _endpoints():
        if endpoint_reachable(h, p, timeout):
            return label
    return None


# ---- v1.3.0: runtime-configurable AI settings (driven by the Settings UI) --
# Env vars above are the DEFAULTS; saved settings override them at startup and
# whenever the AI Settings tab is saved. Keys mirror MaINbox naming so the two
# apps feel the same.
AI_SETTINGS_KEYS = (
    "ai_host", "ai_port", "ai_host_model",
    "ai_local_host", "ai_local_port", "ai_local_model",
    "ai_timeout", "ai_host_probe",
)


def ai_settings_defaults():
    """The current effective config as a settings dict — used to seed the UI
    and settings.json on first run."""
    return {
        "ai_host": OLLAMA_HOST,
        "ai_port": OLLAMA_PORT,
        "ai_host_model": OLLAMA_MODEL,
        "ai_local_host": LOCAL_OLLAMA_HOST,
        "ai_local_port": LOCAL_OLLAMA_PORT,
        "ai_local_model": LOCAL_OLLAMA_MODEL,
        "ai_timeout": OLLAMA_TIMEOUT,
        "ai_host_probe": OLLAMA_HOST_PROBE,
    }


def apply_ai_settings(settings):
    """v1.3.0: override the module config from a saved settings dict. Blank/
    missing fields fall back to the current default (MaINbox behavior — blank
    host fields fall back to defaults). Safe to call repeatedly."""
    global OLLAMA_HOST, OLLAMA_PORT, OLLAMA_MODEL, OLLAMA_TIMEOUT
    global LOCAL_OLLAMA_HOST, LOCAL_OLLAMA_PORT, LOCAL_OLLAMA_MODEL
    global OLLAMA_HOST_PROBE
    if not settings:
        return

    def _s(key, cur):
        v = settings.get(key)
        if v is None:
            return cur
        v = str(v).strip()
        return v if v else cur

    def _i(key, cur):
        v = settings.get(key)
        if v is None or str(v).strip() == "":
            return cur
        try:
            return int(str(v).strip())
        except ValueError:
            return cur

    OLLAMA_HOST = _s("ai_host", OLLAMA_HOST)
    OLLAMA_PORT = _i("ai_port", OLLAMA_PORT)
    OLLAMA_MODEL = _s("ai_host_model", OLLAMA_MODEL)
    LOCAL_OLLAMA_HOST = _s("ai_local_host", LOCAL_OLLAMA_HOST)
    LOCAL_OLLAMA_PORT = _i("ai_local_port", LOCAL_OLLAMA_PORT)
    # local model blank -> mirror the host model (same default rule as import)
    lm = settings.get("ai_local_model")
    LOCAL_OLLAMA_MODEL = (str(lm).strip() if lm and str(lm).strip()
                          else OLLAMA_MODEL)
    OLLAMA_TIMEOUT = _i("ai_timeout", OLLAMA_TIMEOUT)
    OLLAMA_HOST_PROBE = _i("ai_host_probe", OLLAMA_HOST_PROBE)

# ------------------------------------------------------------------ heuristics
_NOREPLY_SENDER_RE = re.compile(
    r"(no[-_.]?reply|donotreply|do[-_.]?not[-_.]?reply|notifications?@|"
    r"mailer-daemon|postmaster@|newsletter@|marketing@|alerts?@|billing@.*auto)",
    re.I)

_NOREPLY_SUBJECT_RE = re.compile(
    r"^(automatic reply|auto(matic)?[- ]?reply|out of (the )?office|"
    r"delivery status|undeliverable|read:|delivered:|recall:)|"
    r"(order (confirmation|shipped)|shipment notification|tracking number|"
    r"payment received|invoice attached|statement (is )?ready|"
    r"password reset|verify your email|webinar|unsubscribe)",
    re.I)

_NOREPLY_BODY_RE = re.compile(
    r"(this is an automated (message|email)|do not reply to this|"
    r"unsubscribe|out of the office|auto[- ]?generated)", re.I)

# v1.10.0: bulk-mail and ticketing infrastructure.
#
# These were the single largest source of wrong answers in the live corpus:
# 78 of 117 deleted rows had been filed as quote_ack, and the recurring
# offenders were a scheduled ERP report notice (six copies), a support
# case-closed notice, a helpdesk ticket thread and a supplier service notice.
# None of them look automated to the existing rules —
# they arrive from a plausible human-looking address with ordinary prose and
# no "do not reply" boilerplate — so whatever product vocabulary they happen
# to contain ("in stock", "availability") fell through to the quoteish branch
# and became an RFQ acknowledgement.
#
# Matching on the delivery infrastructure instead of on tone is what makes
# this reliable: a click-tracking redirect, a helpdesk ticket URL or a
# "view this email in your browser" line is emitted by bulk-sending software
# and effectively never appears in a hand-written RFQ.
_BULK_INFRA_RE = re.compile(
    r"(view (this email|it) in your (browser|web browser)|"
    r"\.ct\.sendgrid\.net|sendgrid\.net/wf/open|"
    r"click\.[\w.-]+/open\.aspx|"
    r"/helpdesk/tickets/\d+|/servicedesk/|\.zendesk\.com/|"
    r"your case has been (closed|updated|created|assigned)|"
    r"(report|export) is complete\.)", re.I)

# Automation that identifies itself by domain rather than by local-part, which
# is all _NOREPLY_SENDER_RE looks at.
_BULK_SENDER_RE = re.compile(
    r"@([\w-]*\.)?(service-now\.com|freshdesk\.com|zendesk\.com|"
    r"sendgrid\.net|mailchimp(app)?\.com|"
    r"constantcontact\.com|salesforce\.com)", re.I)

# A ticket reference carried in the subject, e.g. "Re: [#12345] - ...".
_TICKET_SUBJECT_RE = re.compile(
    r"(\[#\d{3,}\]|\bcase\s+[A-Z]{2}\d{6,}\b|\bticket\s*#\s*\d{3,})", re.I)

_THANKS_ONLY_RE = re.compile(
    r"^\s*(thanks?( you)?|thank you|got it|received|perfect|great,?\s*thanks?|"
    r"sounds good|will do|ok(ay)?|appreciate it)[.!\s]*$", re.I)

_QUOTE_RE = re.compile(
    r"\b(rfq|request for (a )?quote|quote|quotation|pricing|price and avail|"
    r"p\s*&\s*a|p/a|bid|proposal|need (a )?price|price on|how much (for|is)|"
    r"cost (for|of|on)|lead ?time|in stock|availability|"
    r"do you (stock|have|carry))\b", re.I)  # v1.0.1: stock-check phrasing

_JOB_RE = re.compile(
    r"\b(job|project|bid date|bid due|plans? (and|&) specs?|spec section|"
    r"submittal)\b", re.I)

_ESCALATE_RE = re.compile(
    r"\b(complaint|unacceptable|attorney|lawyer|legal action|lawsuit|"
    r"extremely (disappointed|frustrated)|escalat|damaged shipment|"
    r"wrong (parts?|material) (was|were)? ?(shipped|sent)|refund|"
    r"cancel (the|our|my) (order|po))\b", re.I)

# Part-number-ish token: letters+digits mixed, length >= 4 (e.g. QO2100, TQD22200)
_PART_TOKEN_RE = re.compile(r"\b(?=[A-Z0-9-]{4,20}\b)(?=[A-Z0-9-]*\d)"
                            r"[A-Z][A-Z0-9-]{3,19}\b")
_QTY_RE = re.compile(r"\b(qty|quantity|\(\d+\)|\d+\s*(pcs?|each|ea\b))", re.I)

# v1.1.0: vendor-delivery signals for the acknowledgement category.
# Conservative two-signal rule (same principle as MaINbox auto-grouping):
# a delivery phrase alone or a price alone never fires; both must be present.
_DELIVERY_RE = re.compile(
    r"(please (find|see)|attached (is|are|you.ll find)|see attached|"
    r"(quote|pricing|p\s*[&/]\s*a|price and avail\w*)[^.\n]{0,25}"
    r"(below|attached|enclosed)|below (is|are) (the|our|your)|"
    r"here (is|are) (the|our|your) (quote|pricing|numbers)|"
    r"let us know if you have any questions)", re.I)
_PRICE_RE = re.compile(r"\$\s*\d[\d,]*(\.\d{1,4})?")

# v1.10.0: the two-signal rule above (delivery phrase AND a $ figure) misses
# the most common shape of a real delivery, because vendors send pricing as an
# attached PDF and write no figure in the body at all. Both "Re: P&A" rows the
# user recategorized were exactly this: a vendor answering with the P&A
# attached, filed as quote_ack — which tells someone who just sent you pricing
# that you will get back to them with pricing.
#
# The missing signal is direction. A reply carrying "pricing attached" is a
# delivery; the same words in a fresh thread are not. So this fires only on a
# reply, and only when the delivery phrase names the pricing itself rather
# than being a generic "see attached".
#
# Matched against the LEAD of the body only, for the same reason _CHASE_RE is:
# a reply carries the whole thread quoted underneath it, and any thread about
# pricing contains "quote attached" somewhere further down. Scanning the full
# body turned genuine quote_delivered replies into acknowledgements by reading
# the history instead of the message.
_PRICING_DELIVERED_RE = re.compile(
    r"((quote|quotation|pricing|price and avail\w*|p\s*[&/]\s*a)\b"
    r"[^.\n]{0,30}(attached|enclosed|below|is ready)|"
    r"attach\w*\s+(is\s+|are\s+)?(our|the|your)?\s*"
    r"(quote|quotation|pricing|proposal)|"
    r"here('?s| is| are)\s+(the|our|your)\s+"
    r"(quote|quotation|pricing|numbers|price))", re.I)

# An explicit request to BE quoted. Used to veto delivery/order readings —
# however much pricing language a mail carries, an explicit ask outranks it.
_ASK_TO_QUOTE_RE = re.compile(
    r"\b(rfq|request for quote|please quote|need (a )?(price|quote)|"
    r"bid due|quote request|can you (quote|price)|"
    r"please provide (a )?(quote|pricing|price))\b", re.I)

_REPLY_SUBJECT_RE = re.compile(r"^\s*(re|fw|fwd)\s*:", re.I)

# v1.9.0: an inbound purchase order. Anchored to the SUBJECT, because a
# quote request routinely mentions a PO number in passing ("Re: Bid
# S100100277 PO# RFQ-503628" is a bid, not an order) — the subject leading
# with the order is the reliable signal.
_PO_SUBJECT_RE = re.compile(
    r"^\s*(re|fw|fwd)?\s*:?\s*(new\s+)?(purchase\s*order|p\.?\s*o\.?)\b"
    r"[\s:#]", re.I)
_PO_BODY_RE = re.compile(
    r"(please find (attached |enclosed )?(our |the )?(purchase order|p\.?o\.?)"
    r"|(purchase order|p\.?o\.?) (is )?(attached|enclosed)"
    r"|please (process|enter|acknowledge) (this|our|the) order"
    r"|here is (our|the) (purchase order|po)\b)", re.I)
# A request to price it is not an order, however many PO numbers it cites.
# v1.10.0: this veto is _ASK_TO_QUOTE_RE (defined above), which says the same
# thing the delivery branch needs and says it slightly wider.

# v1.9.0: routine transactional paperwork
_TRANSACTIONAL_RE = re.compile(
    r"(proof of delivery|packing (slip|list)|advance ship(ping)? notice|"
    r"\basn\b|bill of lading|\bbol\b|remittance|statement of account|"
    r"credit memo|debit memo|invoice\s*(#|no\.?|number)?\s*[\w-]*\d)",
    re.I)

# v1.6.0: a customer chasing an RFQ already submitted. Deliberately matched
# on the FIRST part of the body only — a chase quotes the original request
# underneath it, and scanning the whole thing would just re-detect the RFQ.
_CHASE_RE = re.compile(
    r"(any update|an update on|status (on|of)|still waiting|"
    r"coming (over )?soon|any word|any luck|where are we|"
    r"did you (get|have) a chance|following up|follow(ing)? up on|"
    r"checking in|just checking|friendly reminder|gentle reminder|"
    r"have you had a chance|when can (i|we) expect|"
    r"is this (coming|ready|done)|how('?s| is) this coming|"
    r"any progress|bumping this)", re.I)


def extract_features(subject, sender, body):
    """Deterministic feature extraction — stored verbatim in the record so
    the corpus stays reproducible even if heuristics change later."""
    text = "%s\n%s" % (subject or "", body or "")
    parts = _PART_TOKEN_RE.findall((body or "").upper())
    feats = {
        "noreply_sender": bool(_NOREPLY_SENDER_RE.search(sender or "")),
        "noreply_subject": bool(_NOREPLY_SUBJECT_RE.search(subject or "")),
        "noreply_body": bool(_NOREPLY_BODY_RE.search(body or "")),
        "thanks_only": bool(_THANKS_ONLY_RE.match((body or "").strip()[:120])),
        "quoteish": bool(_QUOTE_RE.search(text)),
        "jobish": bool(_JOB_RE.search(text)),
        "escalate": bool(_ESCALATE_RE.search(text)),
        "has_question": "?" in (body or ""),
        "part_tokens": len(set(parts)),
        "has_qty": bool(_QTY_RE.search(body or "")),
        "body_len": len(body or ""),
        "chase": bool(_CHASE_RE.search((body or "")[:400])),        # v1.6.0
        "po": bool(_PO_SUBJECT_RE.search(subject or "")
                   or _PO_BODY_RE.search(body or "")),              # v1.9.0
        "transactional": bool(_TRANSACTIONAL_RE.search(
            "%s\n%s" % (subject or "", (body or "")[:600]))),       # v1.9.0
        "needs_input": bool(_NEEDS_INPUT_RE.search(
            "%s\n%s" % (subject or "", body or ""))),                # v1.7.0
        "delivery_phrase": bool(_DELIVERY_RE.search(body or "")),  # v1.1.0
        "price_count": len(_PRICE_RE.findall(body or "")),         # v1.1.0
        # v1.10.0
        "bulk_infra": bool(_BULK_INFRA_RE.search(body or "")
                           or _BULK_SENDER_RE.search(sender or "")
                           or _TICKET_SUBJECT_RE.search(subject or "")),
        "is_reply": bool(_REPLY_SUBJECT_RE.search(subject or "")),
        "pricing_delivered": bool(
            _PRICING_DELIVERED_RE.search((body or "")[:400])),
        "asks_to_quote": bool(_ASK_TO_QUOTE_RE.search(text)),
    }
    return feats


def classify_heuristic(subject, sender, body):
    """Returns (needs_reply, category, confidence, features)."""
    f = extract_features(subject, sender, body)

    if f["noreply_sender"] or f["noreply_subject"] or f["noreply_body"]:
        return False, CAT_NO_REPLY, 0.9, f
    # v1.10.0: bulk/ticketing infrastructure, before anything else reads the
    # prose. These carry ordinary sentences and would otherwise be scored on
    # their vocabulary alone.
    if f["bulk_infra"]:
        return False, CAT_NO_REPLY, 0.88, f
    if f["thanks_only"]:
        return False, CAT_NO_REPLY, 0.85, f
    if f["escalate"]:
        return True, CAT_ESCALATE, 0.7, f
    # an order, or routine paperwork, before any quote reasoning — both were
    # being answered as though the sender wanted a price
    if f["po"] and not f["asks_to_quote"]:
        return True, CAT_PURCHASE_ORDER, 0.7, f
    if f["transactional"]:
        return True, CAT_TRANSACTIONAL, 0.6, f
    if f["chase"]:
        # a follow-up on work already in hand — must precede the quoteish
        # branch, since the quoted original below a chase is full of RFQ
        # language and would otherwise win
        return True, CAT_QUOTE_IN_PROCESS, 0.65, f
    if f["delivery_phrase"] and f["price_count"] >= 1:
        # v1.1.0: vendor delivered pricing/quote TO us -> thank-you, not
        # "I'll get back to you". Must come before the quoteish branch since
        # P&A bodies are full of quote language.
        return True, CAT_ACK, 0.7, f
    if f["is_reply"] and f["pricing_delivered"] and not f["asks_to_quote"]:
        # v1.10.0: the same delivery, priced in an attachment rather than in
        # the body. Requires a reply subject so a fresh "pricing attached"
        # thread isn't swept up, and defers to an explicit ask to be quoted.
        return True, CAT_ACK, 0.65, f
    if f["quoteish"]:
        if f["jobish"] and f["part_tokens"] == 0:
            # project/bid language but nothing concrete to price
            return True, CAT_JOB_NAME, 0.6, f
        if f["part_tokens"] == 0 and not f["has_qty"]:
            return True, CAT_NEED_INFO, 0.6, f
        return True, CAT_QUOTE_ACK, 0.75, f
    if f["has_question"] and f["part_tokens"] >= 1:
        # v1.0.1: concrete part referenced in a question -> quotable
        return True, CAT_QUOTE_ACK, 0.55, f
    if f["has_question"]:
        return True, CAT_NEED_INFO, 0.4, f
    if f["body_len"] < 40:
        return False, CAT_NO_REPLY, 0.5, f
    # Unclassifiable substantive mail -> human eyes
    return True, CAT_ESCALATE, 0.3, f


# ------------------------------------------------------------------------ LLM

_LLM_SYSTEM = (
    "You classify inbound emails for an electrical supply distributor. "
    "Respond with ONLY a JSON object, no markdown, no preamble, exactly: "
    '{"needs_reply": true|false, "category": "<one of: quote_ack, no_quote, '
    'need_info, job_name, quote_delivered, quote_in_process, '
    'purchase_order, transactional, '
    'acknowledgement, escalate, '
    'no_reply>", '
    '"confidence": 0.0-1.0, "reason": "<max 15 words>"}. '
    "Category meanings: quote_ack = customer requests pricing/quote and gave "
    "enough detail, acknowledge and quote later; need_info = wants pricing "
    "but details are missing; job_name = construction project/bid where the "
    "job name is needed; purchase_order = an inbound ORDER to acknowledge, "
    "not a request to be quoted; transactional = routine paperwork such as "
    "proof of delivery, invoice, ASN or packing slip; "
    "quote_in_process = the sender is chasing a quote "
    "already requested, so confirm it is being worked on; "
    "quote_delivered = replying with the actual price "
    "and availability now rather than promising to follow up; "
    "acknowledgement = a vendor or contact DELIVERED "
    "pricing/quote/info we asked for, reply is a brief thank-you; "
    "no_quote = should politely decline to quote; "
    "escalate = complaint/legal/unusual, human must handle; no_reply = "
    "automated mail, FYI, or simple thanks needing no response."
)


def ollama_call(messages, fmt=None, timeout=None, probe=True,
                deterministic=True):
    """v1.2.0: shared low-level Ollama caller with host-first fallback.
    Walks _endpoints() in order; skips an endpoint fast if a short probe
    says it's down (so a dead host doesn't burn the full timeout), then
    tries the actual /api/chat. Returns (content_str, endpoint_label) on
    success, or (None, reason) where reason is 'no_endpoint' (nothing
    reachable) or 'error:<Type>' (all reachable endpoints errored).

    v1.8.0: `deterministic` pins temperature to 0. Ollama defaults to 0.8,
    so the SAME email could come back with a different category on different
    runs — and a label that changes run to run cannot be learned from, since
    the agreement rate would be measuring sampling noise as much as
    judgement. Draft polish passes deterministic=False, where variation is
    harmless."""
    last_reason = "no_endpoint"
    tried_any = False
    for label, host, port, model in _endpoints():
        if probe and not endpoint_reachable(host, port):
            continue
        tried_any = True
        url = "http://%s:%d/api/chat" % (host, port)
        body = {"model": model, "stream": False,
                "messages": messages}
        if fmt:
            body["format"] = fmt
        if deterministic:
            body["options"] = {"temperature": 0, "top_p": 1, "seed": 7}
        payload = json.dumps(body).encode("utf-8")
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(
                    req, timeout=timeout or OLLAMA_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            return ((data.get("message") or {}).get("content", ""), label)
        except Exception as e:
            last_reason = "error:%s" % e.__class__.__name__
            continue  # fall through to next endpoint (host busy -> local)
    return None, (last_reason if tried_any else "no_endpoint")


def _ollama_chat(prompt, timeout=None):
    content, _label = ollama_call(
        [{"role": "system", "content": _LLM_SYSTEM},
         {"role": "user", "content": prompt}],
        fmt="json", timeout=timeout)
    return content or ""


def classify_llm(subject, sender, body):
    """Returns (needs_reply, category, confidence) or None on any failure."""
    if NO_LLM:
        return None
    prompt = ("From: %s\nSubject: %s\n\n%s"
              % (sender or "", subject or "", (body or "")[:4000]))
    try:
        raw = _ollama_chat(prompt)
        raw = raw.strip().removeprefix("```json").removeprefix("```")\
                 .removesuffix("```").strip()
        obj = json.loads(raw)
        cat = str(obj.get("category", "")).strip()
        if cat not in CATEGORIES:
            return None
        needs = bool(obj.get("needs_reply", cat != CAT_NO_REPLY))
        conf = float(obj.get("confidence", 0.5))
        conf = max(0.0, min(1.0, conf))
        return needs, cat, conf
    except Exception:
        return None


# v1.7.0: questions aimed AT the user that only the user can answer. This is
# a FLAG, not a category, and deliberately so:
#   - it is orthogonal — "quote this, and who's the competition?" is a real
#     quote_ack that still needs a human fact before it can be sent;
#   - a tenth category would split the graduation counts without describing
#     a different kind of *reply*;
#   - the property that actually matters is "never auto-send this", which
#     applies whatever the category turns out to be.
# Note the direction: "Do you have a job name?" asked BY them needs the
# user's knowledge, whereas the job_name category is the user asking THEM.
_NEEDS_INPUT_RE = re.compile(
    r"(the competition\b|who('?s| is| are) (the )?(competition|bidding|"
    r"quoting|you up against)|who else is (bidding|quoting|involved)|"
    r"are (we|you) competitive|"
    r"do you have (a |the )?job name|what('?s| is) the job name|"
    r"job name and|name of the job\?|"
    r"your (landed )?cost\b|what did you pay|your margin|"
    r"will they do \d+\s*%|can (they|you) do \d+\s*%|"
    r"who is (the|your) (customer|contractor|end user)|"
    r"which (contractor|customer|distributor)|"
    r"do you know (who|what|if)|can you find out|"
    r"any (idea|thoughts) (on|who|what))", re.I)


def detect_needs_input(subject, body):
    """True when answering requires a fact only the user holds."""
    text = "%s\n%s" % (subject or "", body or "")
    return bool(_NEEDS_INPUT_RE.search(text))


def classify(subject, sender, body):
    """Public entry point.
    Returns dict: needs_reply, category, confidence, features, source."""
    h_needs, h_cat, h_conf, feats = classify_heuristic(subject, sender, body)

    # Hard heuristic wins: automated senders never get LLM'd (cheap + certain)
    ni = detect_needs_input(subject, body)
    if h_cat == CAT_NO_REPLY and h_conf >= 0.85:
        return {"needs_reply": False, "category": CAT_NO_REPLY,
                "confidence": h_conf, "features": feats,
                "source": "heuristic", "needs_input": False}

    llm = classify_llm(subject, sender, body)
    if llm is not None:
        needs, cat, conf = llm
        return {"needs_reply": needs, "category": cat, "confidence": conf,
                "features": feats, "source": "llm", "needs_input": ni}

    return {"needs_reply": h_needs, "category": h_cat, "confidence": h_conf,
            "features": feats, "source": "heuristic", "needs_input": ni}
