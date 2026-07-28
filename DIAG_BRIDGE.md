# Replyit Diagnostic Bridge

A loopback HTTP surface for inspecting and exercising a **running** Replyit,
so assessment doesn't depend on screenshots.

## Turning it on

```powershell
$env:REPLYIT_DIAG="1"
pythonw replypilot.pyw
```

Optional: `$env:REPLYIT_DIAG_PORT="8765"` (default 8765; if taken, the bridge
walks up to the next free port and records which one it took).

That walk depends on the bind actually failing, which needed fixing to work
on Windows. `HTTPServer` sets `allow_reuse_address = 1`, and there SO_REUSEADDR
lets a second process bind a port another process is already listening on —
silently, with no `OSError`. Since MaINbox's bridge defaults to the same 8765,
Replyit bound straight over it and which process received a given connection
was arbitrary; the symptom was a 401 against the *other* app's token rather
than a bind error. The bridge now binds with `allow_reuse_address = False`, so
a collision raises and the walk lands on 8766 as intended.

It is **off** unless `REPLYIT_DIAG=1`. Nothing listens otherwise.

## Getting the token

On start the bridge writes:

```
%LOCALAPPDATA%\ReplyPilot\diag_token.json
```

```json
{ "token": "...", "port": 8765, "pid": 12345,
  "started_at": "...", "base_url": "http://127.0.0.1:8765" }
```

A fresh token per boot; the file is deleted on clean shutdown. Send it as
`X-Diag-Token:` or `?token=`.

```powershell
$t = (Get-Content "$env:LOCALAPPDATA\ReplyPilot\diag_token.json" | ConvertFrom-Json)
curl.exe -H "X-Diag-Token: $($t.token)" "$($t.base_url)/state"
```

## Security posture

- Binds `127.0.0.1` only — never reachable off the machine
- Random per-boot token, compared in constant time
- `/health` is the one unauthenticated route and returns liveness only
- **No endpoint touches Outlook COM.** Nothing here can send mail.
- Reads go straight to SQLite; anything that mutates app state is marshalled
  onto the Tk main thread and waited on, because Tk is not thread-safe and a
  background write corrupts the widget tree silently rather than failing

## GET

| Endpoint | Purpose |
|---|---|
| `/health` | liveness, no auth, no data |
| `/` | endpoint index |
| `/state` | tab counts, action counts, AI queue, origin split |
| `/config` | effective AI settings + resolved endpoints (signature redacted); `?probe=1` also issues one real chat call |
| `/stats` | per-category graduation numbers |
| `/pending` | pending rows — `?category=`, `?max_conf=`, `?limit=`, `?full=1` |
| `/decided` | decided rows — `?action=`, `?limit=` |
| `/email?id=<message_id>` | one full record |
| `/autosend` | master switch, scheduled sends, what is eligible right now |
| `/learn` | learning-staging counts and a sample |
| `/opslog` | audit log tail — `?lines=`, `?event=` |

## POST

| Endpoint | Purpose |
|---|---|
| `/classify` | score text **without storing anything** |
| `/draft` | render a template for a category |
| `/inject` | push synthetic emails through the real pipeline |
| `/reclassify` | re-run classification over pending rows only |
| `/decide` | record a decision (never sends) |
| `/set_needs_input` | flag/unflag a row |
| `/purge_synthetic` | remove everything `/inject` created |

A verb mismatch returns 405 naming the correct verb, rather than a confusing
404.

## The endpoint that matters most: `/classify`

It runs the real classifier and **writes nothing**, so accuracy can be
measured without the test polluting the corpus it is measuring.

```json
POST /classify
{
  "emails": [
    {"subject": "Purchase Order P000020783", "sender": "b@x.com",
     "body": "Please process this order.", "expect": "purchase_order"},
    {"subject": "Proof of Delivery G062812-01", "sender": "a@x.com",
     "body": "Attached.", "expect": "transactional"},
    {"subject": "RFQ - breakers", "sender": "c@x.com",
     "body": "Please quote QO2100 qty 3", "expect": "quote_ack"}
  ],
  "features": true,
  "with_draft": false
}
```

Response carries a summary plus every miss:

```json
{"summary": {"n": 3, "scored": 3, "correct": 2, "accuracy": 0.667,
             "misses": [{"subject": "...", "got": "quote_ack",
                         "expected": "transactional", "confidence": 0.55}]},
 "results": [...]}
```

`expect` is optional — omit it to just see what the classifier says.

## Suggested assessment loop

1. `GET /state` — where things stand
2. `GET /pending?max_conf=0.6&limit=100` — the low-confidence rows are where
   the classifier is guessing, and the best source of real misses
3. Build a `/classify` batch from those subjects with your own `expect`
   values, and read the `misses` list
4. `POST /inject` a candidate edge case, confirm it lands correctly,
   `POST /purge_synthetic` to clean up
5. `GET /stats` — whether any category is nearing graduation, and on what
   agreement rate
6. `GET /autosend` — confirm nothing is eligible that shouldn't be

## `/config?probe=1` — is the fallback live?

`/config` reports each endpoint's `reachable` (does `/api/tags` answer) and
`model_listed` (is the configured model in that list). Neither claim is the
one that matters, and on a real install they came apart: the host listed
`gemma3:27b` and passed every reachability check while each chat call died on
a CUDA fault, so every email fell back to a 3B local model — silently, with
nothing in the app saying so. Listing a model is not the same claim as being
able to run it.

`?probe=1` issues one real chat through the same host-then-local walk that
classification uses and reports who answered:

```json
"live_probe": {
  "answered_by": "local", "seconds": 21.4, "reply": "OK",
  "note": "FALLBACK ACTIVE — 'host' is configured first but 'local' served
           this call; labels are not coming from the model named in Settings"
}
```

Worth checking before trusting any accuracy measurement, because a label is
only as good as the model that produced it. Costs one round trip, and takes
the full timeout when the first endpoint is the broken one.

## Notes

- `/inject` ids carry a `<diag-synth-` marker, and `/purge_synthetic` only
  removes ids carrying it, so real mail cannot be caught by a cleanup
- `/reclassify` deliberately skips decided rows: rewriting a decided verdict
  would retroactively change what the user agreed or disagreed with and
  corrupt every agreement rate derived from it
- `/config` reports the signature as a length, not its contents
