# Replyit v1.3.0

Standalone email auto-reply trainer. Reviews are entirely in-app (zero COM
while reading/deciding); COM is touched only briefly on worker threads for
scan and send. Every decision is recorded to build the training corpus that
justifies graduating categories to auto-send.

## Files
- replypilot.pyw v1.3.0 — Tkinter app (queue tabs, checkboxes, review, AI Review, settings incl. visual toolbar editor, auto-send loop)
- replypilot_classify_engine.py v1.1.0 — heuristics + Ollama refinement
- replypilot_draft_engine.py v1.2.0 — per-category templates + LLM polish (off by default)
- replypilot_mail_engine.py v1.0.0 — .eml parse, Outlook COM scan/send (guarded), .eml draft fallback
- replypilot_record_engine.py v1.2.0 — SQLite + JSONL audit, graduation math
- replypilot_auto_engine.py v1.0.0 — auto-send eligibility + delay scheduling (pure logic)
- selftest_harness.py — 92-check regression harness (run: python selftest_harness.py)

## Run
Double-click replypilot.pyw (or `pythonw replypilot.pyw`). Data lives in
%LOCALAPPDATA%\ReplyPilot (db, audit jsonl, settings.json, outbox drafts) —
deliberately not OneDrive-synced.

## Workflow
Import .eml files/folder or Scan Outlook Inbox (needs pywin32). New mail is
classified into Auto-Reply Queue or No Reply. Double-click to review:
original body + AI draft. Pick a different response type and the draft
regenerates. Accept / Accept & Send / Decline / Move to No Reply (undoable
from the No Reply tab). Everything is recorded with an unchanged/changed flag.

## Taxonomy (closed — do not add free-form categories)
quote_ack, no_quote, need_info, job_name, acknowledgement, escalate, no_reply

## Graduation
Per AI-category: >= 50 decided samples AND >= 95% unchanged (accepted or
auto_sent) => graduated. Stats window shows it; double-click a row to toggle
a manual auto-send override. v1.2.0: auto-send now EXECUTES when the Settings master switch is on
AND a category is graduated/overridden AND confidence >= threshold. Every
send waits the delay window; opening or deleting the email cancels it;
eligibility is re-verified at fire time. Escalate and No-Reply are never
auto-sent regardless of settings or overrides.

## LLM
Ollama on tillium-bridge (100.89.98.118:11434, gemma3:27b). Overrides:
REPLYPILOT_OLLAMA_HOST / _PORT / _MODEL / _TIMEOUT. REPLYPILOT_NO_LLM=1
forces heuristics-only. Heuristics always produce a full result; the LLM is
a refinement layer, never a dependency. Draft LLM polish is OFF by default
(settings.json: use_llm_polish).

## Keys & threading
Internet Message-ID is the only key (fallback = md5 of sender|subject|date|body
for mail without one). EntryID is never used. Outlook send finds the original
via DASL filter on PR_INTERNET_MESSAGE_ID. All COM follows
outlook_thread_init/uninit/fresh_outlook on worker threads.

## Test status (honest disclosure)
Compile + harness verified on Linux/Python 3.12: eml parsing, all heuristic
paths, drafts, record store, idempotent re-intake, undo, graduation math,
override, export, audit format, .eml draft fallback, COM guards. NOT live
tested: Outlook COM scan/send (Windows-only), Ollama calls, Tkinter UI
(headless container). Exercise those on your machine before trusting them.
