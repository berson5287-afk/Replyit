# replypilot_draft_engine.py
# ReplyPilot Draft Engine v1.0.0
# Deterministic templates per category, optionally polished by the local LLM.
# Template output is ALWAYS available — the LLM is a refinement layer, never
# a dependency. Settings (signature, company name) live in settings.json in
# the ReplyPilot data dir and are created with defaults on first run.

ENGINE_VERSION = "1.8.0"  # v1.8.0: purchase_order + transactional templates
                          # v1.7.0: quote_in_process template
                          # v1.6.0: strip_configured_signature
                          # v1.5.0: Outlook signature discovery/import
                          # v1.4.0: quote_delivered template
                          # v1.3.0: polish routes through shared host-first
                          # /local-fallback caller; ollama_reachable checks
                          # both endpoints
                          # v1.2.0: polish_draft with reasons, output
                          # repair pipeline, ollama_reachable preflight
                          # v1.1.0: acknowledgement template, save_settings,
                          # auto-send settings defaults

import os
import re
import json
import urllib.request

from replypilot_classify_engine import (
    CAT_QUOTE_ACK, CAT_NO_QUOTE, CAT_NEED_INFO, CAT_JOB_NAME,
    CAT_ESCALATE, CAT_ACK, CAT_QUOTE_DELIVERED, CAT_QUOTE_IN_PROCESS,
    CAT_PURCHASE_ORDER, CAT_TRANSACTIONAL, CAT_NO_REPLY,
    OLLAMA_HOST, OLLAMA_PORT,
    OLLAMA_MODEL, OLLAMA_TIMEOUT, NO_LLM,
    ollama_call, any_endpoint_reachable, active_endpoint_label,
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
    # v1.6.0: let Outlook attach the real signature (with images) on send
    "use_outlook_signature": True,
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
    CAT_PURCHASE_ORDER: (
        "Hi {greet}\n\n"
        "Thank you for the order — received. I'll confirm pricing and "
        "delivery and get an acknowledgement over to you shortly.\n\n{sig}"
    ),
    CAT_TRANSACTIONAL: (
        "Hi {greet}\n\n"
        "Received, thank you — I'll get this to the right person here.\n\n"
        "{sig}"
    ),
    CAT_QUOTE_IN_PROCESS: (
        "Hi {greet}\n\n"
        "Yes — this is being worked on and I'll get it over to you shortly. "
        "Thank you for your patience!\n\n{sig}"
    ),
    CAT_QUOTE_DELIVERED: (
        "Hi {greet}\n\n"
        "Thanks for the request. Pricing and availability below — let me "
        "know if you'd like me to put this together as a formal quote.\n\n"
        "[ add pricing here ]\n\n{sig}"
    ),
    CAT_ACK: (
        "Hi {greet}\n\n"
        "Thank you — received. Appreciate the quick turnaround. I'll review "
        "and follow up if I have any questions.\n\n{sig}"
    ),
    CAT_NO_REPLY: "",
}


def outlook_signature_dir():
    """%APPDATA%\\Microsoft\\Signatures — where Outlook stores signatures.
    No COM required; they are ordinary files on disk."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return ""
    d = os.path.join(appdata, "Microsoft", "Signatures")
    return d if os.path.isdir(d) else ""


def list_outlook_signatures():
    """Return [(name, path)] for each signature that has a plain-text form.

    The .txt companion is preferred: Replyit sends plain-text replies, and
    Outlook writes a .txt alongside every .htm signature, so using it avoids
    dragging HTML markup into a draft.
    """
    d = outlook_signature_dir()
    if not d:
        return []
    out = []
    try:
        for fn in sorted(os.listdir(d)):
            base, ext = os.path.splitext(fn)
            if ext.lower() == ".txt":
                out.append((base, os.path.join(d, fn)))
    except OSError:
        return []
    return out


def read_signature_file(path, max_chars=2000):
    """Read a signature file, tolerating the encodings Outlook writes."""
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                text = f.read(max_chars + 1)
            if "\x00" in text:
                continue
            text = text.replace("\r\n", "\n").strip()
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text[:max_chars]
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return ""
    return ""


def strip_configured_signature(text, signature):
    """Remove the app's own text signature from a draft.

    Used when Outlook is adding the real signature on send: without this the
    reply would carry both the plain-text block and Outlook's HTML one.
    """
    if not text or not signature:
        return (text or "").strip()
    sig = signature.strip()
    if not sig:
        return text.strip()
    idx = text.rfind(sig)
    if idx != -1:
        return text[:idx].rstrip()
    # signature edited since the draft was written — fall back to its first
    # line (typically the sender's name), matched from the end
    first = sig.splitlines()[0].strip()
    if first:
        idx = text.rfind(first)
        if idx != -1:
            return text[:idx].rstrip()
    return text.strip()


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
    """v1.3.0: True if EITHER the host or the local fallback answers. AI
    Review only hard-fails when both are down; if just tillium is down it
    proceeds on local."""
    return any_endpoint_reachable(timeout=timeout)


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
    """v1.3.0: real polish entry point. Routes through the shared host-first
    /local-fallback caller (ollama_call). Returns (text_or_None, reason)
    where reason is 'ok', 'no_llm', 'empty_template', 'no_endpoint' (both
    host and local down), 'error:<Type>', or a cleaner rejection."""
    if NO_LLM:
        return None, "no_llm"
    if not template_text:
        return None, "empty_template"
    prompt = ("CUSTOMER EMAIL:\nSubject: %s\n%s\n\nTEMPLATE REPLY:\n%s"
              % (original_subject or "", (original_body or "")[:2000],
                 template_text))
    raw, label = ollama_call(
        [{"role": "system", "content": _POLISH_SYSTEM},
         {"role": "user", "content": prompt}],
        timeout=timeout, deterministic=False)
    if raw is None:
        return None, label   # 'no_endpoint' or 'error:<Type>'
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
