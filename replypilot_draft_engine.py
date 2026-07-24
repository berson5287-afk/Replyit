# replypilot_draft_engine.py
# ReplyPilot Draft Engine v1.0.0
# Deterministic templates per category, optionally polished by the local LLM.
# Template output is ALWAYS available — the LLM is a refinement layer, never
# a dependency. Settings (signature, company name) live in settings.json in
# the ReplyPilot data dir and are created with defaults on first run.

ENGINE_VERSION = "1.2.0"  # v1.2.0: polish_draft with reasons, output
                          # repair pipeline, ollama_reachable preflight
                          # v1.1.0: acknowledgement template, save_settings,
                          # auto-send settings defaults

import os
import re
import json
import urllib.request

from replypilot_classify_engine import (
    CAT_QUOTE_ACK, CAT_NO_QUOTE, CAT_NEED_INFO, CAT_JOB_NAME,
    CAT_ESCALATE, CAT_ACK, CAT_NO_REPLY, OLLAMA_HOST, OLLAMA_PORT,
    OLLAMA_MODEL, OLLAMA_TIMEOUT, NO_LLM,
)

SETTINGS_NAME = "settings.json"

DEFAULT_SETTINGS = {
    "signature": "Steve Berson\nAmerican Power Electrical Supply",
    "company": "American Power Electrical Supply",
    "use_llm_polish": False,   # off by default: templates first, trust later
    # v1.1.0: auto-send engine settings (master off by default — categories
    # must also be graduated or manually overridden before anything fires)
    "auto_send_master": False,
    "auto_send_delay_sec": 60,
    "auto_send_min_conf": 0.85,
}


def save_settings(directory, settings):
    """v1.1.0: persist settings dict (atomic replace to avoid torn writes)."""
    path = os.path.join(directory, SETTINGS_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    os.replace(tmp, path)


def load_settings(directory):
    path = os.path.join(directory, SETTINGS_NAME)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2)
        return dict(DEFAULT_SETTINGS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            s = json.load(f)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(s if isinstance(s, dict) else {})
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def _first_name(sender_name, sender_addr):
    name = (sender_name or "").strip()
    if name:
        # "Last, First" handling
        if "," in name:
            parts = [p.strip() for p in name.split(",")]
            if len(parts) == 2 and parts[1]:
                return parts[1].split()[0]
        token = name.split()[0]
        if not re.search(r"[@\d]", token):
            return token
    if sender_addr and "@" in sender_addr:
        local = sender_addr.split("@")[0]
        local = re.split(r"[._-]", local)[0]
        if local and local.isalpha():
            return local.capitalize()
    return ""


TEMPLATES = {
    CAT_QUOTE_ACK: (
        "Hi {greet}\n\n"
        "Thanks for the RFQ — received. I'll get back to you with pricing "
        "and availability on this shortly.\n\n{sig}"
    ),
    CAT_NO_QUOTE: (
        "Hi {greet}\n\n"
        "Thanks for reaching out. Unfortunately we won't be able to quote "
        "this one, but I appreciate you thinking of us — please keep us in "
        "mind for the next one.\n\n{sig}"
    ),
    CAT_NEED_INFO: (
        "Hi {greet}\n\n"
        "Thanks for the request. Before I can price this out I need a little "
        "more information — part numbers (or manufacturer and description) "
        "and quantities. Once I have that I'll turn the quote around "
        "quickly.\n\n{sig}"
    ),
    CAT_JOB_NAME: (
        "Hi {greet}\n\n"
        "Thanks for the RFQ. Can you send me the job name so I can register "
        "it and get you the best possible pricing? I'll get moving on it as "
        "soon as I have that.\n\n{sig}"
    ),
    CAT_ESCALATE: (
        "Hi {greet}\n\n"
        "Thanks for your email — I want to make sure this gets handled "
        "properly, so I'm looking into it personally and will follow up "
        "with you today.\n\n{sig}"
    ),
    CAT_ACK: (
        "Hi {greet}\n\n"
        "Thank you — received. Appreciate the quick turnaround. I'll review "
        "and follow up if I have any questions.\n\n{sig}"
    ),
    CAT_NO_REPLY: "",
}


def render_template(category, sender_name="", sender_addr="", settings=None):
    settings = settings or dict(DEFAULT_SETTINGS)
    tpl = TEMPLATES.get(category, "")
    if not tpl:
        return ""
    first = _first_name(sender_name, sender_addr)
    greet = ("%s," % first) if first else "there,"
    return tpl.format(greet=greet, sig=settings.get(
        "signature", DEFAULT_SETTINGS["signature"]))


# ------------------------------------------------------------------ LLM polish

_POLISH_SYSTEM = (
    "You lightly personalize a short business email reply for an electrical "
    "supply distributor. Keep the SAME meaning, commitment, and rough length "
    "as the template. Reference the customer's email naturally where it "
    "helps. Never invent prices, part availability, dates, or promises not "
    "in the template. Plain text only, no subject line, no markdown. "
    "Keep the signature exactly as given, at the end."
)


def polish_with_llm(template_text, original_subject, original_body,
                    timeout=None):
    """Back-compat wrapper around polish_draft. Returns text or None."""
    text, _reason = polish_draft(template_text, original_subject,
                                 original_body, timeout=timeout)
    return text


def ollama_reachable(timeout=3):
    """v1.2.0: fast preflight so the AI Review pass can fail loudly and
    immediately instead of silently eating a 30s timeout per email."""
    try:
        req = urllib.request.Request(
            "http://%s:%d/api/tags" % (OLLAMA_HOST, OLLAMA_PORT))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(200)
        return True
    except Exception:
        return False


_PREAMBLE_RE = re.compile(
    r"^\s*(here('s| is)( (the|a|your))?[^\n]*|sure[.,!]?|certainly[.,!]?|"
    r"okay[.,!]?|of course[.,!]?)\s*:?\s*\n+", re.I)


def _clean_polish_output(text, template_text, signature):
    """v1.2.0: pure post-processor for LLM polish output. Instead of
    discarding on any deviation (the old behavior — which made AI Review
    look dead), repair what's repairable and only reject what's unusable.
    Returns (cleaned_text_or_None, reason)."""
    if not text or not text.strip():
        return None, "empty"
    t = text.strip()
    # order matters: chatty preamble can precede the fence ("Here's ...:\n
    # ```..."), so strip preamble, then fences, then preamble once more
    t = _PREAMBLE_RE.sub("", t, count=1)
    t = re.sub(r"^```[a-zA-Z]*\s*\n?", "", t)
    t = re.sub(r"\n?```\s*$", "", t)
    t = _PREAMBLE_RE.sub("", t.strip(), count=1)
    # strip wrapping quotes
    t = t.strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        t = t[1:-1].strip()
    if len(t) < 20:
        return None, "too_short"
    if len(t) > max(800, len(template_text) * 4):
        return None, "too_long"
    # signature: re-append if the model dropped or mangled it, rather than
    # rejecting the whole draft
    sig = (signature or "").strip()
    if sig and sig not in t:
        tail = sig.splitlines()[-1].strip()
        if not (tail and tail in t):
            t = t.rstrip() + "\n\n" + sig
    return t, "ok"


def polish_draft(template_text, original_subject, original_body,
                 settings=None, timeout=None):
    """v1.2.0: the real polish entry point. Returns (text_or_None, reason)
    where reason is 'ok', 'no_llm', 'empty_template', 'llm_error', or a
    cleaner rejection ('empty'/'too_short'/'too_long')."""
    if NO_LLM:
        return None, "no_llm"
    if not template_text:
        return None, "empty_template"
    prompt = ("CUSTOMER EMAIL:\nSubject: %s\n%s\n\nTEMPLATE REPLY:\n%s"
              % (original_subject or "", (original_body or "")[:2000],
                 template_text))
    url = "http://%s:%d/api/chat" % (OLLAMA_HOST, OLLAMA_PORT)
    payload = json.dumps({
        "model": OLLAMA_MODEL, "stream": False,
        "messages": [{"role": "system", "content": _POLISH_SYSTEM},
                     {"role": "user", "content": prompt}],
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout or OLLAMA_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        raw = ((data.get("message") or {}).get("content") or "")
    except Exception as e:
        return None, "llm_error:%s" % e.__class__.__name__
    sig = (settings or {}).get("signature") or DEFAULT_SETTINGS["signature"]
    return _clean_polish_output(raw, template_text, sig)


def make_draft(category, sender_name="", sender_addr="", subject="",
               body="", settings=None):
    """Public entry point. Returns (draft_text, source) where source is
    'template' or 'llm'."""
    base = render_template(category, sender_name, sender_addr, settings)
    if not base:
        return "", "template"
    if settings and settings.get("use_llm_polish"):
        polished, _reason = polish_draft(base, subject, body,
                                         settings=settings)
        if polished:
            return polished, "llm"
    return base, "template"
