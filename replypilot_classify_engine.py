# replypilot_classify_engine.py
# ReplyPilot Classification Engine v1.0.0
# Two-stage classifier: deterministic heuristics always run first and produce
# a full result; the LLM (Ollama, gemma3 on tillium-bridge) refines it when
# reachable. If the LLM is down, slow, or returns garbage, heuristics stand.
#
# Closed taxonomy — categories are FIXED. This is what makes the learning
# loop measurable. Do not add free-form categories.

ENGINE_VERSION = "1.1.0"  # v1.1.0: acknowledgement category (vendor P&A
                          # deliveries) + delivery-phrase/price features
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

CATEGORIES = (CAT_QUOTE_ACK, CAT_NO_QUOTE, CAT_NEED_INFO,
              CAT_JOB_NAME, CAT_ESCALATE, CAT_ACK, CAT_NO_REPLY)

# Categories that appear as reply choices in the UI (everything but no_reply)
REPLY_CATEGORIES = (CAT_QUOTE_ACK, CAT_NO_QUOTE, CAT_NEED_INFO,
                    CAT_JOB_NAME, CAT_ACK, CAT_ESCALATE)

# ------------------------------------------------------------------ LLM config
OLLAMA_HOST = os.environ.get("REPLYPILOT_OLLAMA_HOST", "100.89.98.118")
OLLAMA_PORT = int(os.environ.get("REPLYPILOT_OLLAMA_PORT", "11434"))
OLLAMA_MODEL = os.environ.get("REPLYPILOT_OLLAMA_MODEL", "gemma3:27b")
OLLAMA_TIMEOUT = int(os.environ.get("REPLYPILOT_OLLAMA_TIMEOUT", "30"))
NO_LLM = os.environ.get("REPLYPILOT_NO_LLM", "") == "1"

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
        "delivery_phrase": bool(_DELIVERY_RE.search(body or "")),  # v1.1.0
        "price_count": len(_PRICE_RE.findall(body or "")),         # v1.1.0
    }
    return feats


def classify_heuristic(subject, sender, body):
    """Returns (needs_reply, category, confidence, features)."""
    f = extract_features(subject, sender, body)

    if f["noreply_sender"] or f["noreply_subject"] or f["noreply_body"]:
        return False, CAT_NO_REPLY, 0.9, f
    if f["thanks_only"]:
        return False, CAT_NO_REPLY, 0.85, f
    if f["escalate"]:
        return True, CAT_ESCALATE, 0.7, f
    if f["delivery_phrase"] and f["price_count"] >= 1:
        # v1.1.0: vendor delivered pricing/quote TO us -> thank-you, not
        # "I'll get back to you". Must come before the quoteish branch since
        # P&A bodies are full of quote language.
        return True, CAT_ACK, 0.7, f
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
    'need_info, job_name, acknowledgement, escalate, no_reply>", '
    '"confidence": 0.0-1.0, "reason": "<max 15 words>"}. '
    "Category meanings: quote_ack = customer requests pricing/quote and gave "
    "enough detail, acknowledge and quote later; need_info = wants pricing "
    "but details are missing; job_name = construction project/bid where the "
    "job name is needed; acknowledgement = a vendor or contact DELIVERED "
    "pricing/quote/info we asked for, reply is a brief thank-you; "
    "no_quote = should politely decline to quote; "
    "escalate = complaint/legal/unusual, human must handle; no_reply = "
    "automated mail, FYI, or simple thanks needing no response."
)


def _ollama_chat(prompt, timeout=None):
    url = "http://%s:%d/api/chat" % (OLLAMA_HOST, OLLAMA_PORT)
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout or OLLAMA_TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    return (data.get("message") or {}).get("content", "")


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


def classify(subject, sender, body):
    """Public entry point.
    Returns dict: needs_reply, category, confidence, features, source."""
    h_needs, h_cat, h_conf, feats = classify_heuristic(subject, sender, body)

    # Hard heuristic wins: automated senders never get LLM'd (cheap + certain)
    if h_cat == CAT_NO_REPLY and h_conf >= 0.85:
        return {"needs_reply": False, "category": CAT_NO_REPLY,
                "confidence": h_conf, "features": feats, "source": "heuristic"}

    llm = classify_llm(subject, sender, body)
    if llm is not None:
        needs, cat, conf = llm
        return {"needs_reply": needs, "category": cat, "confidence": conf,
                "features": feats, "source": "llm"}

    return {"needs_reply": h_needs, "category": h_cat, "confidence": h_conf,
            "features": feats, "source": "heuristic"}
