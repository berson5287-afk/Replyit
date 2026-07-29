# Replyit

**AI-assisted email reply trainer that learns how you actually respond.**

Replyit classifies inbound mail into a closed set of response types, drafts a
reply for each, and records every decision you make. As your agreement with a
category climbs, that category can graduate to sending automatically — but
only on evidence you generated, never on assumption.

Built for an electrical distributor's RFQ and order workflow. Windows +
Outlook for live mail; `.eml` import works anywhere.

---

## Requirements

- Python 3.10+
- `pywin32` for live Outlook scanning/sending (`pip install pywin32`) —
  optional; `.eml` import and everything else run without it
- Optional: an [Ollama](https://ollama.com) endpoint for LLM classification
  and draft polish. Heuristics always produce a full result on their own, so
  the LLM is a refinement layer, never a dependency.

## Run

```
pythonw replypilot.pyw
```

Data lives in `%LOCALAPPDATA%\ReplyPilot` (database, audit log, settings,
outbox drafts) — deliberately outside OneDrive to avoid sync file locks.

`settings.json` is read with `utf-8-sig`, so a byte-order mark (which
PowerShell's `Out-File -Encoding utf8` and several editors add) does not make
it unreadable. If it genuinely cannot be parsed it is moved to
`settings.json.corrupt` rather than left in place: the app would otherwise run
on defaults — no scan folders, default AI host, default signature — and the
next ordinary save would write those defaults over the real file, so one bad
byte would destroy the configuration permanently.

## Files

| File | Role |
|---|---|
| `replypilot.pyw` | Tkinter app — tabs, review, bulk actions, settings |
| `replypilot_classify_engine.py` | Heuristics + Ollama refinement, taxonomy |
| `replypilot_draft_engine.py` | Per-category templates, LLM polish, signatures |
| `replypilot_mail_engine.py` | `.eml` parsing, Outlook COM scan/send (guarded) |
| `replypilot_record_engine.py` | SQLite corpus + JSONL audit, graduation math |
| `replypilot_auto_engine.py` | Auto-send eligibility and delay scheduling |
| `replypilot_learn_engine.py` | Sent-mail import, staging, confirm/correct |
| `replyit_diag_bridge.py` | Loopback diagnostic HTTP surface (off by default) |
| `selftest_harness.py` | Regression harness — `python selftest_harness.py` |
| `DIAG_BRIDGE.md` | Bridge endpoint reference |

---

## Workflow

Import `.eml` files, or scan selected Outlook folders. New mail is classified
and lands in one of several tabs:

- **Auto-Send** — scheduled to send, counting down; open or delete to cancel
- **Auto-Reply Queue** — needs a reply, ready to review
- **Needs Your Input** — asks something only you know ("who's the
  competition?"). Never auto-sends, whatever its category
- **AI Review Queue** — staged for LLM draft tailoring
- **No Reply / Deleted / Decided**

Double-click any row to see the original alongside the draft. Pick a
different response type and the draft regenerates. Accept, Accept & Send,
Decline, or Move to No Reply — individually, or in bulk from the toolbar.

Every decision is recorded with an unchanged/changed flag. That record is the
product; the automation is downstream of it.

## Taxonomy

Closed set — do not add free-form categories, because graduation counts are
per category and a category that means several things cannot graduate
honestly.

| Category | Meaning |
|---|---|
| `quote_ack` | New RFQ received — will follow up with pricing |
| `quote_in_process` | Sender is chasing an RFQ already in hand |
| `quote_delivered` | Replying with the actual price/availability now |
| `no_quote` | Declining to quote |
| `purchase_order` | An inbound order to acknowledge, not an enquiry |
| `transactional` | Proof of delivery, invoice, ASN, packing slip |
| `need_info` | Can't proceed without more detail from them |
| `job_name` | Need the job name to register/price |
| `acknowledgement` | They delivered what was asked for — brief thanks |
| `escalate` | Complaint, legal, or unusual — a human must handle it |
| `no_reply` | Automated mail or a bare thanks |

Separately, a `needs_input` **flag** marks any email whose answer requires a
fact only you hold. It is a flag rather than a category because it is
orthogonal — a genuine `quote_ack` can still need a job name before it can be
sent — and because the property that matters, *never auto-send*, applies
whatever the category turns out to be.

## Graduation and auto-send

A category graduates at **≥50 decided samples and ≥95% unchanged** by default.
The Stats window shows the numbers; double-click a row to force a manual
override.

Both numbers are editable in **Settings → Auto-send → Graduation bar**, because
50 at 95% is a judgement call rather than a law: it suits a high-volume
category and is unreachable for a rare one, and the person carrying the risk of
a wrong reply is the one who should set the bar. Floors of **1 sample and 50%
agreement** hold whatever is entered — an agreement bar of zero would mean
"graduate a category you have never once agreed with", which is not a threshold
at all. A value above 1 is read as a percentage, so 95 and 0.95 both work.

The settings panel previews the consequence rather than just taking a number,
because a threshold on its own tells you nothing and the real question is which
reply types are about to become sendable:

> At this bar these would graduate now: Acknowledgement, Quote delivered

Lowering the bar never bypasses the other gates. A graduated category still has
to be ticked in Settings, sit outside `escalate`/`no_reply`, clear the
confidence threshold, hold a draft, and not be flagged `needs_input`.

Note that **deleting counts as agreement**, not disagreement — `deleted` is in
`UNCHANGED_ACTIONS`. Binning a row says the category was not wrong, only that
no reply was wanted; recategorising is the action that says the classifier
missed.

Auto-send fires only when every gate passes:

1. Master switch on in Settings (off by default)
2. Category ticked in **Settings → Auto-send → Reply types**
3. Category graduated, or manually overridden
4. Category is not `escalate` or `no_reply` — hard-excluded regardless
5. Row is not flagged `needs_input` — hard-excluded regardless
6. Confidence ≥ the configured threshold
7. A non-empty draft exists
8. Still pending at both schedule time **and** fire time

Gate 2 only ever **narrows**. Ticking a reply type is a preference; graduation
is evidence, and it is the evidence that makes sending safe — so a ticked type
that has not graduated still sends nothing. Leaving every type ticked stores no
restriction at all, which is how an existing settings file behaves. Unticking
even one writes an explicit list, and an empty list means nothing sends.

`escalate` and `no_reply` are not offered in that list, because no amount of
ticking could make them eligible.

## The Auto-Send tab

Everything scheduled to send waits on the first tab, with a live countdown, so
what is about to leave the building is the first thing visible rather than
something you go looking for. Opening or deleting a row cancels its send.

Double-click a queued row to read and edit it, exactly as on any other tab — a
reply about to go out unattended is the one most worth being able to change
first. Opening **cancels the scheduled send before the window appears**, which
is the only safe order: editing a draft while its countdown runs would race the
send, and the row could leave mid-edit.

Closing that window without deciding does not delete anything. The row is still
pending and still eligible, so the next scan re-queues it — with a **fresh full
hold**, not the remainder of the old one. Reviewing therefore buys back the
whole undo window every time. To stop it going out at all, decide it: Accept,
Decline, Delete, or Move to No Reply.

The hold is set in minutes (**Settings → Auto-send**), because a window you
would actually use to catch a bad reply is minutes long. Switching the hold off
does not mean zero — `MIN_DELAY_SEC` still applies. Every gate is re-checked at
the moment of firing, and removing the window entirely would remove the only
chance to stop a wrong reply.

### The drip

The hold decides **when** a reply may go. The drip decides **how fast** replies
are actually released: one per interval (default 60s), never a sweep — the same
shape as the MaINbox triage drip.

Without it, `evaluate_and_schedule()` gives every newly eligible row the *same*
`fire_at`, so a category graduating against a backlog put the whole backlog on
one countdown and fired it together. On a real queue that was 138 replies
leaving in one instant, with no gap in which to notice the first was wrong. A
per-item hold is an undo window, not a rate limit; it says nothing about how
many land at once.

With the drip on, a wrong rule costs one email and the time to spot it rather
than the whole queue. Rows whose hold has expired but whose turn has not come
stay in `scheduled` — still listed on the Auto-Send tab, still cancellable, and
shown as **due** rather than "sending", because telling you ten replies are
sending when nine are queued behind a drip would misreport the one thing that
tab exists to show.

Turning the drip off restores the old sweep, which is occasionally what you
want on a small, trusted category.

## Auto-refresh

The selected mailboxes are rescanned on a timer (default 90s, minimum 15,
**Settings → Auto-send → Auto-refresh**). A scan already in progress is never
interrupted and no second scan is queued behind it: with draft polish on, one
pass over a full folder can outlast the interval, and queuing would mean the
app never stopped scanning. The timer path raises no dialogs, since an
unattended timer must not put a modal in front of you.

## Learn from Sent

Imports a sent-mail JSON export and turns real replies into training data —
but only with explicit confirmation.

Genuine replies are kept (`in_reply_to`, or an `RE:`/`FW:` subject), the typed
reply is split from signature and quoted original, the inbound email is
recovered from the quoted headers, and a response type is inferred and staged.

**Staged rows are inert.** They live in a separate table that the graduation
math never reads, so an AI guess sitting in staging cannot push anything
toward auto-send. Only Confirm (accepted) or Correct (recategorized) promotes
a row into the corpus; ignored and untouched rows affect nothing. Promoted
records carry `origin='import'` so they stay distinguishable from live
decisions permanently.

**Rows the importer could not read cannot be Confirmed.** Inference returns
`escalate` as its "could not tell" answer in four places — an internal
colleague thread, an outbound follow-up, an empty reply, and the final
fall-through — because such a row needs a human. It is never returned as a
positive verdict; no rule here concludes "this is an escalation". Confirm
promotes the *inferred* category, so agreeing with one of those recorded a
full-strength training sample asserting something nobody ever concluded. In
the live corpus 9 of 10 `escalate` samples arrived this way, giving the
category a perfect 10/10 agreement rate built almost entirely on shrugs.
Confirm now refuses them; Correct still admits them, because naming a
category is the judgement the sentinel was standing in for.

Inference reads the **reply** first rather than the inbound email: the label
needed is which response was chosen, and the reply is that response. Measured
against a real export, inbound-first collapsed 8 of 9 records to `quote_ack`
while the replies were declines, job-name asks and delivered prices;
reply-first got 8 of 9 right.

## Writing in your voice

Three layers, in order of how reliably they apply.

**1. The templates themselves.** Measured over 71 real replies from the sent
corpus:

| | Measured | Old templates |
|---|---|---|
| Length | median 14 words, one sentence; 82% under 30 | 25-40 words, 2-3 sentences |
| Opener | 59% none; "Good morning/afternoon \<Name\>," 33% | always "Hi \<Name\>," — used 1% of the time |
| Sign-off | 63% none; "Thanks!" 28% | none |

The templates are now written to that shape, using the recurring phrasing the
corpus actually contains — "Will advise", "This is being worked on",
"Attached is your quote", "What job is this for". This layer needs no LLM and
always applies, which matters because the template is what ships whenever
polish is off, the endpoint is down, or a draft is rejected. Greetings are
generated by time of day, and a role address (`estimating@`, `sales@`) gets no
name rather than "Good morning Estimating,".

**2. `VOICE_PROFILE`** — the same measurements restated as rules, sent with
every polish request. It covers the categories the corpus has no confirmed
replies for yet, which is why `purchase_order` now drafts "Got the order,
will process shortly." instead of house style.

**3. Retrieved examples.** `phrasing_examples()` returns the five most recent
confirmed replies for the category, added as further evidence when they exist.
Only promoted rows qualify, so an unconfirmed import guess can never shape a
draft. `make_draft` reports `source='voice'` when examples were used.

**The examples are for cadence, never content.** They are real replies, so they
carry real prices, part numbers and customer names, and a small model handed
five of them will reuse that material rather than merely the rhythm — observed
directly: a draft addressed to one contact came back greeting a different one because two
exemplars opened that way. Output is therefore validated before it ships, and
anything the template did not already say is treated as fabricated:

| Check | Action |
|---|---|
| Greeting names the wrong person | repaired to the template's addressee |
| A `$` figure not in the template | rejected |
| A part number appearing in an example but not the template | rejected |
| A delivery commitment not in the template | rejected |
| Prompt scaffolding echoed back | rejected |

The commitment check earns its place: a `quote_ack` whose template says only
"Will advise." came back "Will send quote by end of day." Nobody committed to
end of day, and a date invented by a 3B model is a promise made to a customer
on the user's behalf — a business problem rather than a style one.

A rejection falls back to the template, which is always correct if plainer.
That trade is deliberate: a template beats a reply carrying another
customer's price.

Requires `use_llm_polish` — with it off, templates are used verbatim and the
examples are never consulted. Every draft then costs one LLM call, so the
configured endpoint needs to actually answer; see `/config?probe=1` in
`DIAG_BRIDGE.md` for whether it does.

## LLM configuration

Host-first with automatic local fallback. Classification and draft polish try
the configured host first, then a local Ollama if the host is down **or** busy
and timing out. Everything is editable in **Settings → AI Settings**, with a
Test connection button and a **Load installed models** button that reads each
endpoint's `/api/tags` and warns when a configured model isn't actually
installed.

Classification pins `temperature: 0`. Ollama defaults to 0.8, which means the
same email could come back with a different category run to run — and an
unstable label cannot be learned from, because the agreement rate would be
measuring sampling noise as much as judgement. Draft polish keeps its
variation, where it is harmless.

## Arbitration: how the two classifiers combine

The heuristics and the LLM are independent readers — one is regex over an
electrical distributor's vocabulary, the other a general model reading the
prose — so they are combined rather than ranked:

| Outcome | Category | Confidence |
|---|---|---|
| Both agree | that category | boosted, capped at 0.95 |
| They disagree, heuristic matched a rule | the LLM's | capped at 0.50 |
| They disagree, heuristic only fell through | the LLM's | capped at 0.80 |
| LLM unreachable | the heuristic's | unchanged |

Agreement between two independent readers is real corroboration and is the
only path to a high confidence. Disagreement means the email is hard, and a
hard email is exactly the one a human should see — so a contested row is
capped below any sane auto-send threshold instead of being resolved by fiat.

This matters because confidence is not a display detail: it is gate 5 of the
auto-send engine. Previously the LLM's answer replaced the heuristic outright
and carried the model's self-reported confidence straight to that gate. That
number is not calibrated — on the live queue gemma3 reported 0.90 or 1.00 for
43 of 67 emails — and the override was unconditional, so an answer the model
itself scored 0.00 displaced a heuristic holding 0.75. Confidences outside a
plausible band are now treated as unstated rather than believed.

The heuristic's findings are also passed to the model as named signals
(`po`, `chase`, `pricing_delivered`, part-number counts …), phrased as
evidence rather than as an answer — a model told the answer cannot
corroborate it.

### Endpoint cooldown

An endpoint that answers `/api/tags` is not an endpoint that can answer a
chat. Seen in practice: a host listed every model and passed every
reachability check while `llama-server` died on a CUDA fault for all of them,
a 1B model included. Each call probed the host, waited out the crash, and only
then fell back — 10-45 seconds of dead time on every email, which is what made
draft polish impractical to leave switched on.

A failed chat now puts that endpoint in a cooldown (`ENDPOINT_COOLDOWN_SEC`,
300s, override with `REPLYPILOT_ENDPOINT_COOLDOWN`) and the walk skips it
until the cooldown lapses, at which point one call pays the cost again to
find out whether it recovered. Measured on the broken host: **56.5s on the
first call, 0.9s on every call after**. `GET /config` reports what is cooling.

Environment overrides: `REPLYPILOT_OLLAMA_HOST` / `_PORT` / `_MODEL` /
`_TIMEOUT` for the host, `REPLYPILOT_LOCAL_HOST` / `_LOCAL_PORT` /
`_LOCAL_MODEL` for the fallback, `REPLYPILOT_HOST_PROBE` for the reachability
probe, and `REPLYPILOT_NO_LLM=1` to force heuristics only. Saved settings
override the environment defaults.

## Signatures

On send, Outlook's own signature is preserved. `Item.Reply()` already contains
your configured signature with logos and images; the draft is prepended inside
`HTMLBody` rather than assigning `.Body`, which would collapse that HTML
document to plain text and lose them. The plain-text signature in Settings is
stripped from the draft first so it isn't duplicated, and remains the fallback
when Outlook isn't available.

## Keys and threading

Internet Message-ID is the only message key — EntryID churns under Cached
Exchange Mode. Folder selections are stored by Outlook `FolderPath` for the
same reason. Outlook send locates the original via a DASL filter on
`PR_INTERNET_MESSAGE_ID`.

All COM runs on worker threads under
`outlook_thread_init` / `outlook_thread_uninit` / `fresh_outlook`. The review
loop itself touches no COM at all.

## Diagnostics

`replyit_diag_bridge.py` exposes a loopback HTTP surface for inspecting a
running instance. Off unless `REPLYIT_DIAG=1`; binds `127.0.0.1` only; fresh
token per boot. No endpoint touches Outlook COM. See `DIAG_BRIDGE.md`.

`POST /classify` is the useful one for accuracy work: it runs the real
classifier and writes nothing, so a batch can be scored against expected
categories without the test polluting the corpus it is measuring.

---

## Privacy

**Real mail never belongs in this repository.** Sent-mail exports contain live
customer and vendor addresses, job names and pricing. `.gitignore` excludes
`*sent_samples*.json`, `*.eml`, `*.msg`, exported corpora and the runtime
database. Check `git status` before every commit.

The learning-engine tests run against a private sent-mail fixture. Without it
they are skipped, not failed — that is expected on a clone, and the rest of
the suite still runs.

## Test status — honest disclosure

Compile- and harness-verified on Linux/Python 3.12: `.eml` parsing, every
heuristic path, drafts, the record store, idempotent re-intake, undo,
graduation math, overrides, export, audit format, COM guards against a stubbed
folder tree, the learning pipeline against a real export, auto-send gating,
and the diagnostic bridge over real HTTP.

**Not live-tested:** Outlook COM scan and send (Windows-only), the HTMLBody
signature path, live Ollama calls, and the Tkinter UI itself. Auto-send has
never fired against real mail. Exercise those on your own machine — and send
one test reply to yourself — before trusting them.

## Changelog

| Version | Change |
|---|---|
| 1.21.0 | Auto-sends are dripped one at a time instead of firing as a burst |
| 1.20.0 | Configurable graduation bar with a live preview of what it would release; timeouts no longer cool an endpoint like a crash |
| 1.19.0 | Auto-Send tab with live countdown; per-category auto-send opt-in; hold window in minutes; timed auto-refresh |
| 1.18.0 | Templates rewritten to the user's measured voice; `VOICE_PROFILE` applied to every polish; invented-commitment guard; role addresses get no first name |
| 1.17.0 | Endpoint cooldown so a broken host costs one call, not every call; settings survive a BOM and are quarantined rather than overwritten |
| 1.16.0 | Drafts written in the user's voice from confirmed replies, with fact-leak validation; outbound vendor asks excluded from learning |
| 1.15.0 | Heuristic/LLM arbitration and calibrated confidence; bulk-mail and ticketing detection; attachment-priced deliveries; Confirm refuses unread imports |
| 1.14.0 | Diagnostic bridge for external inspection |
| 1.13.0 | Deterministic classification; `purchase_order` and `transactional`; Reclassify Pending |
| 1.12.x | `needs_input` flag + tab; AI Review Queue with cancel; one row, one tab |
| 1.11.0 | `quote_in_process`; quote dropdown; Enter/Escape hotkeys |
| 1.10.0 | Bulk actions with keep-AI-choice default |
| 1.9.x | Multi-mailbox folder selection, grouped and filtered |
| 1.8.0 | Outlook signature preserved on send; model discovery |
| 1.7.0 | Per-window geometry; toolbar editor; signature import |
| 1.6.x | Learn from Sent with inert staging; retuned on real data |
| 1.5.0 | AI Settings; window geometry |
| 1.4.0 | Host-first Ollama with local fallback |
| 1.3.0 | Manifest toolbar; select all/none; AI Review made usable |
| 1.2.0 | `acknowledgement`; AI Review; auto-send engine |
| 1.1.0 | Theme, multi-select, Deleted bucket |
| 1.0.0 | Initial release |
