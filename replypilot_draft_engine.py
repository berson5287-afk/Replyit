# replypilot_draft_engine.py
# ReplyPilot Draft Engine v1.0.0
# Deterministic templates per category, optionally polished by the local LLM.
# Template output is ALWAYS available — the LLM is a refinement layer, never
# a dependency. Settings (signature, company name) live in settings.json in
# the ReplyPilot data dir and are created with defaults on first run.

ENGINE_VERSION = "1.13.0"  # v1.13.0: drip + graduation-bar settings defaults
                          # v1.12.0: graduation bar settings defaults
                          # v1.11.0: hold in minutes, per-category opt-in and
                          # auto-refresh settings defaults
                          # v1.10.0: templates rewritten to the user's
                          # measured voice; VOICE_PROFILE on every polish;
                          # role addresses get no first name
                          # v1.9.0: confirmed replies used as voice exemplars,
                          # with validation against fact leaks
                          # v1.8.1: settings survive a BOM; an unparseable
                          # settings file is quarantined, not overwritten
                          # v1.8.0: purchase_order + transactional templates
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
import datetime
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
    # v1.11.0: the undo window, expressed the way it is actually reasoned
    # about. Off still leaves MIN_DELAY_SEC — see AutoSendEngine.delay_sec.
    "auto_send_delay_enabled": True,
    "auto_send_delay_min": 1,
    # None means "no per-category restriction", which is what an existing
    # settings file implies. An explicit [] means the user opted nothing in.
    "auto_send_categories": None,
    # v1.11.0: periodic rescan. Guarded by the busy flag, so a scan that runs
    # long can never stack another on top of itself.
    "auto_refresh_enabled": True,
    "auto_refresh_sec": 90,
    # v1.12.0: the graduation bar. Defaults match the original constants, so
    # an existing install is unchanged. Floors are enforced in RecordStore.
    "graduation_min_samples": 50,
    "graduation_min_agreement": 0.95,
    # v1.13.0: releases are dripped one at a time rather than swept, so a
    # backlog cannot leave in a single burst. See AutoSendEngine.due().
    "auto_send_drip_enabled": True,
    "auto_send_drip_sec": 60,
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
    """Load settings, tolerating a BOM and never silently discarding a file.

    Falling back to defaults on any read error looks safe and is not: the app
    then runs on a default AI host with no scan folders and no signature, and
    the next ordinary save (moving a window is enough) writes those defaults
    over the real file. A single unreadable byte thereby destroys the
    configuration permanently.

    Two changes. utf-8-sig, because a BOM is what any number of editors and
    PowerShell's Out-File put at the front of a JSON file, and it is not
    corruption — the settings behind it are perfectly good. And when the file
    genuinely cannot be parsed, it is moved aside rather than left in place to
    be overwritten, so the contents survive for recovery.
    """
    path = os.path.join(directory, SETTINGS_NAME)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2)
        return dict(DEFAULT_SETTINGS)
    try:
        # utf-8-sig reads with or without a BOM
        with open(path, "r", encoding="utf-8-sig") as f:
            s = json.load(f)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(s if isinstance(s, dict) else {})
        return merged
    except Exception:
        _quarantine_settings(path)
        return dict(DEFAULT_SETTINGS)


def _quarantine_settings(path):
    """Move an unparseable settings file aside so a save cannot clobber it."""
    for n in range(1, 100):
        dest = "%s.corrupt%s" % (path, "" if n == 1 else str(n))
        if not os.path.exists(dest):
            try:
                os.replace(path, dest)
            except OSError:
                pass
            return dest
    return ""


# Local-parts that name a function, not a person. Deriving a first name from
# the address is right for dave@ and wrong for estimating@ — "Good morning
# Estimating," is a worse opening than no name at all, and these role
# addresses are most of an RFQ inbox.
_ROLE_LOCALPART = frozenset((
    "sales", "info", "estimating", "estimator", "purchasing", "purchase",
    "accounts", "accounting", "orders", "order", "service", "support",
    "admin", "office", "quotes", "quote", "bids", "bid", "ap", "ar",
    "billing", "invoices", "contact", "team", "help", "enquiries",
    "inquiries", "noreply", "no-reply", "mail", "email", "shipping",
    "receiving", "warehouse", "dispatch", "customerservice", "cs",
))


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
        whole = sender_addr.split("@")[0].lower()
        local = re.split(r"[._-]", whole)[0]
        if whole in _ROLE_LOCALPART or local in _ROLE_LOCALPART:
            return ""       # a role address has no first name to use
        if local and local.isalpha():
            return local.capitalize()
    return ""


# v1.10.0: templates written in the user's measured voice.
#
# These were house style, not his, and the gap was not a matter of taste — it
# is measurable. Across 71 real replies from his sent mail:
#
#   length     median 14 words, ONE sentence; 82% under 30 words
#   opener     59% none at all; "Good morning/afternoon <Name>," 33%;
#              "Hi <Name>," — which every template used — 1%
#   sign-off   63% none; "Thanks!" 28%
#
# The old templates ran 25-40 words over two or three clauses and opened with
# the one greeting he essentially never uses. They also matter more than the
# LLM path does: a template is what ships whenever polish is off, the endpoint
# is down, or the voice safety check rejects a draft, so this is the voice the
# app falls back to and therefore the one it needs to get right.
#
# His own recurring phrasing is used where he has it — "Will advise",
# "this is being worked on", "Attached is your quote", "What job is this for"
# — while each category keeps exactly the commitment it made before.
TEMPLATES = {
    CAT_QUOTE_ACK: (
        "{greet}\n\n"
        "Will advise. Thanks!\n\n{sig}"
    ),
    CAT_NO_QUOTE: (
        "{greet}\n\n"
        "Sorry, we can't quote this one. Thanks!\n\n{sig}"
    ),
    CAT_NEED_INFO: (
        "{greet}\n\n"
        "Can you send me part numbers and quantities? I'll get it priced "
        "up. Thanks!\n\n{sig}"
    ),
    CAT_JOB_NAME: (
        "{greet}\n\n"
        "What job is this for? Thanks!\n\n{sig}"
    ),
    CAT_ESCALATE: (
        "{greet}\n\n"
        "Sorry about that — I'm looking into it and will get back to you "
        "today.\n\n{sig}"
    ),
    CAT_PURCHASE_ORDER: (
        "{greet}\n\n"
        "Got the order — I'll confirm pricing and delivery shortly. "
        "Thanks!\n\n{sig}"
    ),
    CAT_TRANSACTIONAL: (
        "{greet}\n\n"
        "Received, thank you — I'll get this to the right person.\n\n{sig}"
    ),
    CAT_QUOTE_IN_PROCESS: (
        "{greet}\n\n"
        "This is being worked on and I should have it over to you shortly. "
        "Thanks!\n\n{sig}"
    ),
    CAT_QUOTE_DELIVERED: (
        "{greet}\n\n"
        "Attached is your quote.\n\n"
        "[ add pricing / lead time ]\n\n"
        "Thanks!\n\n{sig}"
    ),
    CAT_ACK: (
        "{greet}\n\n"
        "Thank you! Appreciate it.\n\n{sig}"
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


def greeting_for(sender_name="", sender_addr="", now=None):
    """The user's own opener: time-of-day, name attached when known.

    Measured over his sent mail, his explicit greetings are "Good morning
    <Name>," and "Good afternoon <Name>," in almost equal number and together
    a third of all replies; "Hi <Name>," — which every template used — appears
    once in 71. The remaining 59% open with no greeting at all, but a template
    is a starting point the user edits, and dropping the greeting entirely
    reads as brusque on a draft he has not looked at yet.

    Note the comma placement: he writes "Good morning Dave," with the comma
    after the name, and "Good morning," when there is no name.
    """
    first = _first_name(sender_name, sender_addr)
    hour = (now or datetime.datetime.now()).hour
    part = ("Good morning" if hour < 12
            else "Good afternoon" if hour < 17
            else "Good evening")
    return "%s %s," % (part, first) if first else "%s," % part


def render_template(category, sender_name="", sender_addr="", settings=None,
                    now=None):
    settings = settings or dict(DEFAULT_SETTINGS)
    tpl = TEMPLATES.get(category, "")
    if not tpl:
        return ""
    return tpl.format(greet=greeting_for(sender_name, sender_addr, now),
                      sig=settings.get("signature",
                                       DEFAULT_SETTINGS["signature"]))


# ------------------------------------------------------------------ LLM polish

_POLISH_SYSTEM = (
    "You lightly personalize a short business email reply for an electrical "
    "supply distributor. Keep the SAME meaning, commitment, and rough length "
    "as the template. Reference the customer's email naturally where it "
    "helps. Never invent prices, part availability, dates, or promises not "
    "in the template. Plain text only, no subject line, no markdown. "
    "Keep the signature exactly as given, at the end."
)

# v1.9.0: the same instruction, plus the user's own past replies as a style
# reference. Separate from _POLISH_SYSTEM because the constraints differ: with
# examples in front of it the model's pull is to reuse their CONTENT, and the
# content of a past reply is a specific price, part number or job — sending
# one of those to a different customer would be worse than any template.
#
# The examples are for cadence only: how they open, how long they run, how
# they commit, how they sign off. Measured against the real corpus that gap is
# wide — the quote_ack template is "Thanks for the RFQ — received. I'll get
# back to you with pricing and availability on this shortly", where the actual
# reply is "Good morning Dave, Will advise. Thanks!".
_VOICE_SYSTEM = (
    "You lightly personalize a short business email reply for an electrical "
    "supply distributor, matching how THIS sender habitually writes. "
    "You are given measured rules for their style, and sometimes real replies "
    "they have sent as further reference. "
    "Match their greeting, sentence length, directness and sign-off. "
    "CRITICAL: copy their manner of writing ONLY. Never copy any fact from an "
    "example — no price, part number, quantity, lead time, job name or "
    "customer name from an example may appear in your output; those belong to "
    "other conversations and would be wrong here. "
    "Keep the SAME meaning and commitment as the template, and never invent "
    "prices, availability, dates or promises the template does not make. "
    "Prefer their brevity: if their replies are short, yours must be short. "
    "Plain text only, no subject line, no markdown. "
    "Keep the signature exactly as given, at the end."
)


# Debris that survives reply-text extraction: Outlook's plain-text divider
# rule, and the mobile taglines that sit below the typed reply rather than
# inside the signature block the parser strips. Harmless in the corpus, but
# these are about to be held up to a model as "how this person writes", and
# a tagline in an exemplar is a tagline it may reproduce.
_VOICE_DEBRIS_RE = re.compile(
    r"(_{5,}|-{5,}|"
    r"get outlook for (ios|android)[^\n]*|"
    r"sent from my (iphone|ipad|android|mobile)[^\n]*|"
    r"<https?://[^>]*>|https?://\S+)", re.I)


# v1.10.0: the measured profile, as rules rather than samples.
#
# Retrieved examples only cover categories the corpus has confirmed replies
# for — purchase_order and transactional had none, so those drafts fell back
# to generic polish and came out in house style. These numbers hold across
# every category, so they apply whether or not an example exists, and they
# state the brevity explicitly because that is the trait a model reverts on
# first: told to "match the style" it still writes three polite sentences.
VOICE_PROFILE = (
    "\n\nHOUSE RULES FOR THIS SENDER (measured from 71 of their real "
    "replies — follow these even where the examples are thin):\n"
    "- LENGTH IS THE MAIN THING: their median reply is 14 words and a single "
    "sentence; 82% are under 30 words. Two short sentences is the maximum. "
    "Do not pad, do not explain, do not add pleasantries.\n"
    "- Keep the greeting line exactly as the template has it.\n"
    "- End with \"Thanks!\" or nothing at all. Never \"Best regards\", "
    "\"Kind regards\", or \"Please don't hesitate\".\n"
    "- Plain and direct. They ask a bare question when they need something "
    "(\"What job is this for?\") rather than framing it politely.\n"
    "- No corporate filler: never \"I appreciate you reaching out\", "
    "\"at your earliest convenience\", \"please be advised\", or "
    "\"thank you for your patience\"."
)


def _voice_block(examples, limit=5, max_chars=400):
    """Format past replies as a style reference, newest first."""
    picked = []
    for ex in (examples or []):
        t = " ".join(_VOICE_DEBRIS_RE.sub(" ", ex or "").split())
        if len(t) < 10:
            continue
        picked.append(t[:max_chars])
        if len(picked) >= limit:
            break
    if not picked:
        return ""
    return ("\n\nHOW THIS SENDER WRITES (style reference only — never reuse "
            "any fact, price, part or name from these):\n"
            + "\n".join("- %s" % p for p in picked))


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


_SCAFFOLD_RE = re.compile(
    r"(CUSTOMER EMAIL:|TEMPLATE REPLY:|HOW THIS SENDER WRITES|"
    r"STYLE REFERENCE|style reference only)", re.I)

# A token that looks like a part number: mixed letters and digits, or a long
# digit run. Used to catch an exemplar's specifics landing in a new reply.
_SPECIFIC_TOKEN_RE = re.compile(r"\b(?=[\w-]*\d)[A-Za-z][\w-]{3,}\b|\b\d{4,}\b")
_MONEY_RE = re.compile(r"\$\s*\d[\d,]*(?:\.\d{1,4})?")

# A promise about WHEN. Observed: a quote_ack whose template says only "Will
# advise." came back "Will send quote by end of day." Nobody committed to end
# of day, and a delivery date invented by a 3B model is a commitment made to a
# customer on the user's behalf — a business problem, not a style one.
_COMMITMENT_RE = re.compile(
    r"\b(end of (the )?day|eod|close of business|cob|"
    r"today|tonight|tomorrow|first thing|noon|"
    r"(mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"this (morning|afternoon|evening|week)|next week|"
    r"within (the )?(\d+|a|an|one|two|three|four|five|24|48|72)\s*"
    r"(minute|hour|day|week|business day)s?|"
    r"in (\d+|one|two|three|four|five)\s*"
    r"(minute|hour|day|week|business day)s?)\b", re.I)
# Group 1 is the addressee. The inner alternations are non-capturing so the
# name keeps a stable group number.
_GREETING_RE = re.compile(
    r"^\s*(?:hi|hello|hey|good (?:morning|afternoon|evening))"
    r"[ \t]+([A-Za-z][a-zA-Z'-]{1,20})\s*[,:]", re.I)


def _voice_safety_check(text, template_text, examples):
    """Reject output that borrowed FACTS from the style examples.

    The examples are real replies, so they carry real prices, part numbers and
    customer names — and a 3B model handed five of them will reuse that
    content, not merely the cadence. Observed directly: a draft addressed to
    one contact came back greeting a different one, because two exemplars
    opened that way, and
    another echoed the prompt scaffolding verbatim.

    Anything the template did not already say is treated as fabricated. That
    is strict on purpose: the fallback is a correct template, and a template
    beats a reply carrying another customer's price every time.
    """
    if _SCAFFOLD_RE.search(text):
        return False, "echoed_prompt"

    tpl = template_text or ""
    if set(_MONEY_RE.findall(text)) - set(_MONEY_RE.findall(tpl)):
        return False, "invented_price"

    # finditer, not findall: the pattern has groups, so findall would return
    # tuples of them rather than the matched phrase
    def _commitments(s):
        return {m.group(0).lower() for m in _COMMITMENT_RE.finditer(s or "")}

    if _commitments(text) - _commitments(tpl):
        return False, "invented_commitment"

    ex_blob = " ".join(examples or [])
    ex_tokens = {t.lower() for t in _SPECIFIC_TOKEN_RE.findall(ex_blob)}
    tpl_tokens = {t.lower() for t in _SPECIFIC_TOKEN_RE.findall(tpl)}
    for tok in _SPECIFIC_TOKEN_RE.findall(text):
        t = tok.lower()
        if t in ex_tokens and t not in tpl_tokens:
            return False, "leaked_specific:%s" % tok
    return True, "ok"


def _repair_greeting(text, template_text):
    """Force the greeting back to the template's addressee.

    The name is not the model's to choose — render_template already resolved
    it from the actual sender. When an exemplar's name wins instead, the reply
    opens by calling the customer someone else, so this is repaired rather
    than rejected: the rest of the draft is usually fine.
    """
    want = _GREETING_RE.match(template_text or "")
    got = _GREETING_RE.match(text or "")
    if not got:
        return text
    if not want:
        # template greeted no one by name; drop a name the model invented
        return text[:got.start(1)] + text[got.end(1):].lstrip()
    if got.group(1).lower() != want.group(1).lower():
        return text[:got.start(1)] + want.group(1) + text[got.end(1):]
    return text


def polish_draft(template_text, original_subject, original_body,
                 settings=None, timeout=None, voice_examples=None):
    """v1.3.0: real polish entry point. Routes through the shared host-first
    /local-fallback caller (ollama_call). Returns (text_or_None, reason)
    where reason is 'ok', 'no_llm', 'empty_template', 'no_endpoint' (both
    host and local down), 'error:<Type>', or a cleaner rejection.

    v1.9.0: `voice_examples` are the user's own past replies for this
    category, from the confirmed corpus. Given any, the draft is shaped to
    how they actually write instead of to the template's house style."""
    if NO_LLM:
        return None, "no_llm"
    if not template_text:
        return None, "empty_template"
    voice = _voice_block(voice_examples)
    # The profile always applies; the examples are extra evidence when the
    # corpus happens to have some for this category.
    prompt = ("CUSTOMER EMAIL:\nSubject: %s\n%s\n\nTEMPLATE REPLY:\n%s%s%s"
              % (original_subject or "", (original_body or "")[:2000],
                 template_text, VOICE_PROFILE, voice))
    raw, label = ollama_call(
        [{"role": "system", "content": _VOICE_SYSTEM},
         {"role": "user", "content": prompt}],
        timeout=timeout, deterministic=False)
    if raw is None:
        return None, label   # 'no_endpoint' or 'error:<Type>'
    sig = (settings or {}).get("signature") or DEFAULT_SETTINGS["signature"]
    text, reason = _clean_polish_output(raw, template_text, sig)
    if text is None:
        return text, reason
    # Validate every polished draft, not only the ones given examples: the
    # scaffolding and invented-price checks are about what the model does, not
    # about what it was shown. With no examples the leak check is simply a
    # no-op.
    text = _repair_greeting(text, template_text)
    ok, why = _voice_safety_check(text, template_text, voice_examples)
    if not ok:
        # Returning None drops the caller back to the template, which is
        # always correct if plainer. Never ship a draft that borrowed another
        # conversation's facts.
        return None, why
    return text, reason


def make_draft(category, sender_name="", sender_addr="", subject="",
               body="", settings=None, voice_examples=None):
    """Public entry point. Returns (draft_text, source) where source is
    'template', 'llm', or 'voice' when the user's own past replies shaped it.

    v1.9.0: `voice_examples` is how the corpus reaches drafting at all. The
    learning pipeline already captured the user's real replies, stripped of
    signature and quoted thread, and LearningStore.phrasing_examples() has
    always been able to return them per category — but nothing called it, so
    every draft was one of eleven fixed templates verbatim and the voice the
    app spent every import collecting was never once used to write anything.
    """
    base = render_template(category, sender_name, sender_addr, settings)
    if not base:
        return "", "template"
    if settings and settings.get("use_llm_polish"):
        polished, _reason = polish_draft(base, subject, body,
                                         settings=settings,
                                         voice_examples=voice_examples)
        if polished:
            return polished, ("voice" if voice_examples else "llm")
    return base, "template"
