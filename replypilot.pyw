# replypilot.pyw
# Replyit v1.14.0 — standalone email auto-reply trainer
#
# v1.0.0: Initial. v1.1.0: theme, multi-select, Deleted bucket. v1.2.0:
# acknowledgement, AI Review, checkboxes, auto-send. v1.3.0: manifest
# toolbar, select all/none, AI Review usable. v1.4.0: host-first Ollama
# with local fallback.
# v1.5.0 changelog:
#   - AI Settings (in Settings window, MaINbox pattern): host address/port/
#     model, local fallback address/port/model, request timeout, and host
#     probe — all editable and saved to settings.json. Blank fields fall
#     back to defaults. A "Test connection" button reports which endpoint
#     answers. Applied live on save; no restart or env vars needed.
#   - Window size + position remembered across launches for the main window
#     (saved to window_geometry in settings.json on close, restored on open,
#     clamped back on-screen if it would open off the visible desktop).
# v1.6.0 changelog:
#   - Learn from Sent: imports a MaINbox sent-mail JSON export, keeps only
#     genuine replies, recovers the quoted inbound email, infers which
#     response type was chosen, and stages it for review. STAGED ROWS ARE
#     INERT — they live in their own table (learn_staging) that the
#     graduation math never reads. Only Confirm/Correct promotes a row into
#     the corpus; ignored and untouched rows affect nothing, ever.
#   - New category quote_delivered ("$15.73, we have these in stock") —
#     added because the real export showed answering with the price now is
#     a distinct, common response the taxonomy had no home for.
#   - Imported samples carry origin='import' in the corpus so they stay
#     distinguishable from live decisions forever.
# v1.6.1 changelog:
#   - Learning inference retuned against a real production import (13 rows
#     read off the live window; 8 were mis-binned). Fixes: short "<Name>,"
#     sign-off and "Good afternoon," greeting are stripped before matching;
#     quote delivery recognized by attachment phrasing ("attached is your
#     quote") with no dollar figure present; "will advise" (quote_ack) split
#     from "please advise" (need_info); wider own-RFQ detection; internal
#     colleague threads flagged via own-domain, derived from quoted To:
#     headers rather than the Exchange-DN sender field.
# v1.7.0 changelog:
#   - Every window remembers its own size and position (keyed by name in
#     settings.json), not just the main one. Button-driven closes persist
#     geometry too, since those bypass WM_DELETE_WINDOW.
#   - Learning window actions moved to a top bar; they previously sat in a
#     right-hand column that fell outside the default width, so the window
#     had to be resized before anything could be clicked. Default is also
#     wider now.
#   - Settings -> Drafting can import the real Outlook signature from
#     %APPDATA%\Microsoft\Signatures (plain-text .txt form), so replies
#     match the rest of the user's mail.
#   - Second learning retune against a live import (16 rows). Biggest fix:
#     a delivered quote is now recognized BEFORE a decline, because real
#     deliveries carry a decline clause about one line item ("Attached is
#     your revised quote, left off the D rings which we don't stock").
# v1.8.0 changelog:
#   - Real signature on send. Item.Reply() already contains the user's
#     configured Outlook signature with logos and images; the old code
#     assigned .Body, which collapses that HTML document to plain text and
#     loses them. Sending now prepends the draft to .HTMLBody instead, so
#     the signature (and the quoted original's formatting) survive intact.
#     The app's plain-text signature is stripped first to avoid duplication,
#     and remains the fallback when Outlook isn't available.
#   - AI Settings: "Load installed models" queries each endpoint's /api/tags
#     and fills the Model fields as dropdowns, warning when a configured
#     model isn't actually installed. That is the earlier error:HTTPError
#     case, caught before it can happen.
# v1.9.0 changelog:
#   - Settings -> Mailboxes lists every mail folder across every open store
#     (own mailbox, shared mailboxes, archives) so the user picks exactly
#     what Scan Outlook Inbox reads. Saved by FolderPath, which is stable
#     across restarts unlike EntryID. Empty selection keeps the previous
#     behavior of scanning only the default Inbox.
#   - Per-folder scan cap so one busy mailbox cannot crowd out the others,
#     and the status line reports how many folders were read and whether
#     any saved folder has since disappeared.
# v1.9.1 changelog:
#   - Mailboxes list grouped under a heading per mailbox, so three folders
#     all called "Inbox" are finally distinguishable; selecting any row
#     shows its full Outlook path in the status line.
#   - Outlook plumbing (Sync Issues, Yammer Root, Conversation History, GUID
#     folders, ...) marked at enumeration and hidden by default, along with
#     empty folders. Both are toggleable, and a folder that is already
#     ticked is never hidden — otherwise it could not be found to un-tick.
# v1.10.0 changelog:
#   - Bulk action bar: Accept, Accept & Send, Decline, Move to No Reply, and
#     Back to Queue, acting on the current tab's selection (ticked rows win
#     over the highlight on the queue). Accept and Accept & Send open a
#     response-type chooser whose first and default option is "keep each
#     email's own AI choice" — a mixed selection forced to one category
#     would mislabel emails AND destroy the unchanged-sample signal that
#     graduation depends on. Overriding regenerates each draft for the new
#     category. Accept & Send confirms before sending real mail.
# v1.11.0 changelog:
#   - New category quote_in_process: the sender is chasing an RFQ already in
#     hand ("This coming over soon?"), so the reply confirms work is underway
#     instead of acknowledging a new request. Chase detection runs BEFORE the
#     RFQ check, because a chase quotes the original request underneath it.
#   - Review window: the quote family moved into a dropdown (the flat radio
#     row had already overflowed and was clipping its last option); non-quote
#     types stay as radios.
#   - Enter accepts in the review window and confirms in every picker dialog;
#     Escape cancels. Enter still inserts a newline while the caret is in a
#     text box. Picker dialogs remember their size and position.
#   - Button labels read "Accept & Send" — Tk shows plain text, so the
#     doubled ampersand was simply wrong.
# v1.12.0 changelog:
#   - "Needs Your Input" flag + tab. Some emails ask the user something only
#     the user knows ("Do you have a job name and the competition?"); no
#     classifier improvement can draft those. Implemented as a FLAG rather
#     than a category because it is orthogonal (a real quote_ack can still
#     need a fact), because a tenth category would split graduation counts
#     without describing a different kind of reply, and because the property
#     that matters — never auto-send — applies whatever the category is.
#     The auto engine treats it as a hard block, above graduation.
#   - "AI Review Queue" tab. AI Review now stages rows instead of firing
#     immediately, with per-row status, a working Cancel, and a choice of
#     endpoint (auto / local / host). At ~30s an email a 70-email pass was
#     half an hour with no way to see progress or stop.
# v1.12.1 changelog:
#   - One row, one tab. An email queued for AI Review now leaves its source
#     tab entirely instead of appearing in both places.
#   - Consequences handled: rows can be accepted/declined/moved directly
#     from the AI Review Queue (otherwise they would be stranded there);
#     successfully tailored rows return to their own tab automatically when
#     a run finishes, while failures stay listed with their reason; deciding
#     or deleting a row drops it from the queue; and queue entries whose
#     email is no longer pending are pruned on every refresh.
# v1.13.0 changelog (all four driven by a live 67-row production queue):
#   - Classification is now DETERMINISTIC. Ollama defaults to temperature
#     0.8, so the same email could return a different category on different
#     runs; an unstable label cannot be learned from, because the agreement
#     rate would partly measure sampling noise. Draft polish keeps its
#     variation, where it is harmless.
#   - New category purchase_order. Roughly a sixth of the live queue was
#     inbound ORDERS being answered "I'll get back to you with pricing".
#     Detection is anchored to the subject, since quote requests routinely
#     cite a PO number in passing.
#   - New category transactional for proof-of-delivery, invoices, ASNs and
#     packing slips, which were also being read as quote requests.
#   - Reclassify Pending re-runs the classifier over pending rows only.
#     Decided rows keep their verdict: rewriting it would retroactively
#     change what the user agreed or disagreed with and corrupt every
#     agreement rate derived from it.
# v1.14.0 changelog:
#   - Diagnostic bridge (replyit_diag_bridge.py) so an external tool can
#     inspect and exercise a running instance. Off unless REPLYIT_DIAG=1,
#     binds 127.0.0.1 only, fresh token per boot written to the data dir,
#     no endpoint touches Outlook COM. Reads go straight to SQLite; anything
#     that mutates app state is marshalled onto the Tk main thread.

APP_TITLE = "Replyit v1.14.0"

import os
import re
import threading
import queue as _queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

import replypilot_record_engine as rec
import replypilot_classify_engine as clf
import replypilot_draft_engine as drafts
import replypilot_mail_engine as mail
import replypilot_auto_engine as auto
import replypilot_learn_engine as learn

# ----------------------------------------------------------------- MaINbox palette
_BG      = "#15181e"
_BG2     = "#1c2027"
_BG3     = "#222831"
_FG      = "#e8eaed"
_FG_DIM  = "#9aa3ad"
_ACCENT  = "#1f9cff"
_SEL_BG  = "#1f3a5f"
_SEL_FG  = "#ffffff"
_ENTRY_BG = "#1e2229"
_FONT    = "Segoe UI"
_FONT_SZ = 9


def apply_theme(root):
    root.configure(bg=_BG)
    s = ttk.Style(root)
    try:
        s.theme_use("clam")
    except Exception:
        pass
    # global widget defaults
    s.configure(".",
                 background=_BG, foreground=_FG,
                 fieldbackground=_ENTRY_BG, troughcolor=_BG2,
                 bordercolor=_BG3, darkcolor=_BG2, lightcolor=_BG3,
                 font=(_FONT, _FONT_SZ))
    # frames / labels
    for w in ("TFrame", "TLabelframe", "TLabelframe.Label", "TLabel"):
        s.configure(w, background=_BG, foreground=_FG)
    # buttons
    s.configure("TButton", background=_BG3, foreground=_FG,
                padding=(6, 3), relief="flat")
    s.map("TButton",
          background=[("active", _ACCENT), ("pressed", "#1780d4")],
          foreground=[("active", _SEL_FG)])
    # notebook
    s.configure("TNotebook", background=_BG2, borderwidth=0)
    s.configure("TNotebook.Tab", background=_BG3, foreground=_FG_DIM,
                padding=(10, 4))
    s.map("TNotebook.Tab",
          background=[("selected", _BG)],
          foreground=[("selected", _ACCENT)])
    # treeview
    s.configure("Treeview", background=_BG2, foreground=_FG,
                fieldbackground=_BG2, rowheight=20,
                borderwidth=0, font=(_FONT, _FONT_SZ))
    s.configure("Treeview.Heading", background=_BG3, foreground=_FG_DIM,
                relief="flat", font=(_FONT, _FONT_SZ, "bold"))
    s.map("Treeview",
          background=[("selected", _SEL_BG)],
          foreground=[("selected", _SEL_FG)])
    s.map("Treeview.Heading",
          background=[("active", _BG3)])
    # scrollbar
    s.configure("Vertical.TScrollbar", background=_BG3,
                troughcolor=_BG2, arrowcolor=_FG_DIM, borderwidth=0)
    # radiobutton / checkbutton
    for w in ("TRadiobutton", "TCheckbutton"):
        s.configure(w, background=_BG, foreground=_FG,
                    focuscolor=_BG)
        s.map(w, background=[("active", _BG)],
              foreground=[("active", _ACCENT)])
    # combobox (used for model pickers)
    s.configure("TCombobox", fieldbackground=_ENTRY_BG, background=_BG3,
                foreground=_FG, arrowcolor=_FG_DIM, bordercolor=_BG3,
                selectbackground=_SEL_BG, selectforeground=_SEL_FG)
    s.map("TCombobox", fieldbackground=[("readonly", _ENTRY_BG)],
          foreground=[("readonly", _FG)])
    root.option_add("*TCombobox*Listbox.background", _ENTRY_BG)
    root.option_add("*TCombobox*Listbox.foreground", _FG)
    root.option_add("*TCombobox*Listbox.selectBackground", _SEL_BG)
    root.option_add("*TCombobox*Listbox.selectForeground", _SEL_FG)
    # PanedWindow sash
    s.configure("TPanedwindow", background=_BG3)
    # option db for non-ttk widgets
    db = root.option_add
    db("*Background",        _BG)
    db("*Foreground",        _FG)
    db("*selectBackground",  _SEL_BG)
    db("*selectForeground",  _SEL_FG)
    db("*insertBackground",  _ACCENT)
    db("*font",              (_FONT, _FONT_SZ))
    db("*Text.Background",   _ENTRY_BG)
    db("*Text.Foreground",   _FG)
    db("*Entry.Background",  _ENTRY_BG)


CATEGORY_LABELS = {
    clf.CAT_QUOTE_ACK: "Quote — will get back shortly",
    clf.CAT_QUOTE_IN_PROCESS: "Quote — in process, coming shortly",
    clf.CAT_PURCHASE_ORDER: "Purchase order received",
    clf.CAT_TRANSACTIONAL: "Paperwork / notification",
    clf.CAT_QUOTE_DELIVERED: "Quote — price given now",
    clf.CAT_NO_QUOTE:  "No quote",
    clf.CAT_NEED_INFO: "Need more information",
    clf.CAT_JOB_NAME:  "Ask for job name",
    clf.CAT_ACK:       "Acknowledgement (thank you)",
    clf.CAT_ESCALATE:  "Escalate (handle personally)",
    clf.CAT_NO_REPLY:  "No reply needed",
}

# Tab indices
_TAB_QUEUE    = 0
_TAB_INPUT    = 1
_TAB_AIREVIEW = 2
_TAB_NOREPLY  = 3
_TAB_DELETED  = 4
_TAB_DECIDED  = 5

# v1.3.0: toolbar manifest — single source of truth for every button.
# (id, label, app method name). The saved layout in settings.json is a list
# of {"id":..., "visible":...} in display order.
TOOLBAR_BUTTONS = (
    ("import_files",  "Import .eml Files",   "import_files"),
    ("import_folder", "Import Folder",       "import_folder"),
    ("scan_outlook",  "Scan Outlook Inbox",  "scan_outlook"),
    ("ai_review",     "AI Review",           "ai_review"),
    ("learn",         "Learn from Sent",     "open_learning"),
    ("reclassify",    "Reclassify Pending",  "reclassify_pending"),
    ("settings",      "Settings",            "open_settings"),
    ("stats",         "Stats / Graduation",  "show_stats"),
    ("export",        "Export Corpus",       "export_corpus"),
)
TOOLBAR_ALWAYS_VISIBLE = ("settings",)   # or you'd lock yourself out


def normalize_toolbar_layout(saved, manifest_ids=None):
    """v1.3.0: pure function — sanitize a saved layout against the manifest.
    Drops unknown ids, dedupes, forces always-visible ids on, appends any
    manifest ids missing from the saved layout (new buttons in future
    versions appear automatically). Preserves saved order."""
    manifest_ids = manifest_ids or tuple(b[0] for b in TOOLBAR_BUTTONS)
    out, seen = [], set()
    for entry in (saved or []):
        if not isinstance(entry, dict):
            continue
        bid = entry.get("id")
        if bid not in manifest_ids or bid in seen:
            continue
        vis = bool(entry.get("visible", True))
        if bid in TOOLBAR_ALWAYS_VISIBLE:
            vis = True
        out.append({"id": bid, "visible": vis})
        seen.add(bid)
    for bid in manifest_ids:
        if bid not in seen:
            out.append({"id": bid, "visible": True})
    return out


def _pick_from_list(app, parent, title, options):
    """Small modal list chooser. Returns the chosen string or None."""
    sel = {"v": None}
    d = tk.Toplevel(parent)
    d.title(title)
    d.configure(bg=_BG)
    apply_theme(d)
    app._bind_geometry(d, "pick_list", "380x320")
    lb = tk.Listbox(d, bg=_ENTRY_BG, fg=_FG, selectbackground=_SEL_BG,
                    selectforeground=_SEL_FG, relief="flat",
                    font=(_FONT, _FONT_SZ), activestyle="none")
    for o in options:
        lb.insert("end", o)
    lb.selection_set(0)
    lb.pack(fill="both", expand=True, padx=10, pady=10)

    def ok(_e=None):
        c = lb.curselection()
        sel["v"] = options[c[0]] if c else None
        app._save_geometry_for("pick_list", d)
        d.destroy()
    row = tk.Frame(d, bg=_BG, pady=8)
    row.pack(fill="x")
    ReviewWindow._btn(row, "OK", ok, accent=True).pack(side="left", padx=10)
    ReviewWindow._btn(row, "Cancel", d.destroy).pack(side="left")
    d.bind("<Return>", ok)          # v1.11.0
    d.bind("<Double-1>", ok)
    d.bind("<Escape>", lambda _e: d.destroy())
    d.transient(parent)
    d.grab_set()
    lb.focus_set()
    parent.wait_window(d)
    return sel["v"]


BULK_KEEP = "__keep__"


def _pick_bulk_category(app, count, allow_keep=True):
    """Category chooser for a bulk action.

    "Keep each email's own AI choice" is offered first and preselected. It
    matters: a mixed selection forced to one category would overwrite correct
    inferences with wrong ones, and — because accepting the AI's own answer
    is what counts as an *unchanged* sample — it is also the option that
    actually builds graduation data. Returns a category, BULK_KEEP, or None.
    """
    sel = {"cat": None}
    parent = app.root
    d = tk.Toplevel(parent)
    d.title("Response type for %d email(s)" % count)
    d.configure(bg=_BG)
    apply_theme(d)
    app._bind_geometry(d, "pick_bulk_category", "440x440")
    tk.Label(d, text="Applying to %d selected email(s)" % count,
             bg=_BG2, fg=_FG, font=(_FONT, _FONT_SZ), anchor="w",
             padx=10, pady=6).pack(fill="x")
    var = tk.StringVar(value=BULK_KEEP if allow_keep else "")
    if allow_keep:
        ttk.Radiobutton(
            d, text="Keep each email's own AI choice  (recommended)",
            value=BULK_KEEP, variable=var).pack(anchor="w", padx=14,
                                                pady=(10, 2))
        tk.Label(d, text="Records each as accepted-unchanged, which is what "
                 "moves a category toward graduation.",
                 bg=_BG, fg=_FG_DIM, font=(_FONT, _FONT_SZ),
                 wraplength=390, justify="left").pack(anchor="w", padx=34)
        ttk.Separator(d, orient="horizontal").pack(fill="x", padx=14,
                                                   pady=8)
        tk.Label(d, text="…or set them all to:", bg=_BG, fg=_FG_DIM,
                 font=(_FONT, _FONT_SZ)).pack(anchor="w", padx=14)
    for cat in clf.REPLY_CATEGORIES + (clf.CAT_NO_REPLY,):
        ttk.Radiobutton(d, text=CATEGORY_LABELS.get(cat, cat), value=cat,
                        variable=var).pack(anchor="w", padx=24, pady=1)

    def ok(_e=None):
        sel["cat"] = var.get() or None
        app._save_geometry_for("pick_bulk_category", d)
        d.destroy()
    row = tk.Frame(d, bg=_BG, pady=10)
    row.pack(fill="x")
    ReviewWindow._btn(row, "OK", ok, accent=True).pack(side="left", padx=12)
    ReviewWindow._btn(row, "Cancel", d.destroy).pack(side="left")
    d.bind("<Return>", ok)          # v1.11.0
    d.bind("<KP_Enter>", ok)
    d.bind("<Escape>", lambda _e: d.destroy())
    d.transient(parent)
    d.grab_set()
    d.focus_set()
    parent.wait_window(d)
    return sel["cat"]


def _pick_category(app, parent=None):
    """Modal category chooser used by the learning review's Correct action."""
    sel = {"cat": None}
    parent = parent or app.root
    d = tk.Toplevel(parent)
    d.title("Pick the correct response type")
    d.configure(bg=_BG)
    apply_theme(d)
    app._bind_geometry(d, "pick_category", "380x360")
    var = tk.StringVar(value="")
    for cat in clf.REPLY_CATEGORIES + (clf.CAT_NO_REPLY,):
        ttk.Radiobutton(d, text=CATEGORY_LABELS.get(cat, cat), value=cat,
                        variable=var).pack(anchor="w", padx=14, pady=2)

    def ok(_e=None):
        sel["cat"] = var.get() or None
        app._save_geometry_for("pick_category", d)
        d.destroy()
    row = tk.Frame(d, bg=_BG, pady=8)
    row.pack(fill="x")
    ReviewWindow._btn(row, "OK", ok, accent=True).pack(side="left", padx=10)
    ReviewWindow._btn(row, "Cancel", d.destroy).pack(side="left")
    d.bind("<Return>", ok)          # v1.11.0
    d.bind("<KP_Enter>", ok)
    d.bind("<Escape>", lambda _e: d.destroy())
    d.transient(parent)
    d.grab_set()
    d.focus_set()
    parent.wait_window(d)
    return sel["cat"]


def partition_pending(pending, ai_queue_ids=()):
    """v1.12.1: split pending rows across the tabs that show them.

    Returns (queue, needs_input, no_reply, ai_review). Every pending row
    lands in exactly one bucket — a row shown in two tabs at once made both
    lists harder to work, and acting on it in one place left a stale copy in
    the other. Precedence: AI Review Queue, then Needs Your Input, then
    reply-needed vs not.
    """
    in_ai = set(ai_queue_ids)
    queue, needs_input, no_reply, ai_review = [], [], [], []
    for r in pending:
        if r["message_id"] in in_ai:
            ai_review.append(r)
        elif r.get("needs_input"):
            needs_input.append(r)
        elif r.get("ai_needs_reply"):
            queue.append(r)
        else:
            no_reply.append(r)
    return queue, needs_input, no_reply, ai_review


def bulk_resolution(ai_category, chosen):
    """v1.10.0: decide what one row gets in a bulk action.

    Returns (action, final_category, regenerate_draft).

    The distinction that matters: accepting the AI's own category records an
    *unchanged* sample, which is what graduation counts. Overriding records a
    changed one. So BULK_KEEP is not merely a convenience — a mixed selection
    forced to a single category would both mislabel emails and destroy the
    unchanged-sample signal. When the override happens to match a row's own
    category, that row is still an accept, not a recategorization.
    """
    if chosen == BULK_KEEP:
        cat = ai_category
    else:
        cat = chosen
    if cat == clf.CAT_NO_REPLY:
        return rec.ACTION_MOVED_NO_REPLY, clf.CAT_NO_REPLY, False
    if cat == ai_category:
        return rec.ACTION_ACCEPTED, cat, False
    return rec.ACTION_RECATEGORIZED, cat, True


def folder_visible(f, selected=(), hide_empty=True, show_system=False):
    """v1.9.1: should this folder appear in the Mailboxes list?

    A selected folder is always shown even when it would otherwise be
    filtered out — hiding something already ticked makes it impossible to
    find and un-tick.
    """
    if f.get("system") and not show_system:
        return f.get("path") in selected
    cnt = f.get("count", -1)
    if hide_empty and isinstance(cnt, int) and cnt == 0:
        return f.get("path") in selected
    return True


def group_folders_by_store(folders, selected=(), hide_empty=True,
                           show_system=False):
    """v1.9.1: build display rows grouped under their mailbox.

    Returns [("store", store_name) | ("folder", index_into_folders)].
    Stores keep first-seen order; a store with nothing visible is omitted
    entirely rather than left as an empty heading.
    """
    by_store, order = {}, []
    for i, f in enumerate(folders):
        st = f.get("store") or "(unknown mailbox)"
        if st not in by_store:
            by_store[st] = []
            order.append(st)
        by_store[st].append(i)
    rows = []
    for st in order:
        idxs = [i for i in by_store[st]
                if folder_visible(folders[i], selected, hide_empty,
                                  show_system)]
        if not idxs:
            continue
        rows.append(("store", st))
        for i in idxs:
            rows.append(("folder", i))
    return rows


class ReplyPilotApp:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        apply_theme(root)

        self.store = rec.RecordStore()
        self.settings = drafts.load_settings(self.store.dir)
        # v1.5.0: push saved AI settings into the engine config before any
        # classification runs (env vars are the fallback defaults)
        clf.apply_ai_settings(self.settings)
        # v1.5.0: restore last window size/position, else sane default
        root.geometry(self._restore_geometry())
        self.ui_queue = _queue.Queue()
        self.busy = False
        self.checked = set()   # v1.2.0: message_ids checked in queue tab
        self.auto = auto.AutoSendEngine(self.store, self.settings)
        self.learn = learn.LearningStore(self.store)   # v1.6.0
        self._last_scan_report = []                   # v1.9.0
        # v1.12.0: AI Review staging queue. message_id -> status string.
        self.ai_queue = {}
        self.ai_cancel = threading.Event()

        self._build_ui()
        self._refresh_lists()
        # v1.5.0: save geometry on close
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        # v1.14.0: diagnostic bridge for external inspection. Off unless
        # REPLYIT_DIAG=1. Wrapped because a broken bridge must never stop
        # the app from starting — it is a diagnostic, not a dependency.
        self.diag = None
        try:
            import replyit_diag_bridge as _diag
            self.diag = _diag.start(self)
            if self.diag is not None:
                self._set_status(
                    "Diag bridge on %s — token in %s"
                    % (self.diag.base_url(), self.diag.token_path()))
        except Exception as _e:
            print("diag bridge unavailable: %s" % _e)
        self.root.after(150, self._drain_ui_queue)

    # ------------------------------------------------------------------- UI
    def _build_ui(self):
        self.toolbar = tk.Frame(self.root, bg=_BG2, pady=4)
        self.toolbar.pack(fill="x")
        self._build_toolbar()

        # v1.10.0: bulk actions on the current tab's selection, so a queue of
        # dozens can be worked without opening each email.
        bulk = tk.Frame(self.root, bg=_BG3, pady=4)
        bulk.pack(fill="x")
        bopts = dict(bg=_BG2, fg=_FG, activebackground=_ACCENT,
                     activeforeground=_SEL_FG, relief="flat",
                     font=(_FONT, _FONT_SZ), padx=8, pady=3, bd=0)
        tk.Label(bulk, text="Selected:", bg=_BG3, fg=_FG_DIM,
                 font=(_FONT, _FONT_SZ)).pack(side="left", padx=(8, 4))
        tk.Button(bulk, text="✓ Accept", command=self.bulk_accept,
                  **dict(bopts, bg=_ACCENT, fg=_SEL_FG)).pack(side="left",
                                                              padx=3)
        tk.Button(bulk, text="✓ Accept & Send",
                  command=self.bulk_accept_send, **bopts).pack(side="left",
                                                               padx=3)
        tk.Button(bulk, text="Decline", command=self.bulk_decline,
                  **bopts).pack(side="left", padx=3)
        tk.Button(bulk, text="Move to No Reply", command=self.bulk_no_reply,
                  **bopts).pack(side="left", padx=3)
        tk.Button(bulk, text="↩ Back to Queue", command=self.bulk_requeue,
                  **bopts).pack(side="left", padx=(14, 3))
        tk.Button(bulk, text="⚑ Needs my input",
                  command=self.bulk_needs_input, **bopts).pack(side="left",
                                                               padx=(14, 3))
        tk.Label(bulk, text="tick rows with Space, or select them",
                 bg=_BG3, fg=_FG_DIM,
                 font=(_FONT, _FONT_SZ)).pack(side="left", padx=12)

        # v1.12.0: AI Review Queue controls, on their own row
        airow = tk.Frame(self.root, bg=_BG2, pady=4)
        airow.pack(fill="x")
        tk.Label(airow, text="AI Review Queue:", bg=_BG2, fg=_FG_DIM,
                 font=(_FONT, _FONT_SZ)).pack(side="left", padx=(8, 4))
        tk.Button(airow, text="▶ Run (auto)", command=self.ai_run_auto,
                  **dict(bopts, bg=_ACCENT, fg=_SEL_FG)).pack(side="left",
                                                              padx=3)
        tk.Button(airow, text="▶ Run on local", command=self.ai_run_local,
                  **bopts).pack(side="left", padx=3)
        tk.Button(airow, text="▶ Run on host", command=self.ai_run_host,
                  **bopts).pack(side="left", padx=3)
        tk.Button(airow, text="■ Cancel", command=self.ai_cancel_run,
                  **bopts).pack(side="left", padx=(12, 3))
        tk.Button(airow, text="Remove selected",
                  command=self.ai_remove_selected, **bopts).pack(side="left",
                                                                 padx=3)
        tk.Button(airow, text="Clear queue", command=self.ai_clear_queue,
                  **bopts).pack(side="left", padx=3)

        self.status_var = tk.StringVar(value="Ready. Data dir: %s"
                                       % self.store.dir)
        tk.Label(self.root, textvariable=self.status_var, anchor="w",
                 bg=_BG2, fg=_FG_DIM, font=(_FONT, _FONT_SZ),
                 pady=3, padx=8).pack(fill="x", side="bottom")

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=6, pady=(4, 0))

        self.tree_queue    = self._make_tree(checks=True)
        self.tree_input    = self._make_tree(checks=True)
        self.tree_aireview = self._make_tree(ai_status=True)
        self.tree_noreply  = self._make_tree()
        self.tree_deleted  = self._make_tree(deleted=True)
        self.tree_done     = self._make_tree(done=True)

        self.nb.add(self.tree_queue.master,    text="Auto-Reply Queue (0)")
        self.nb.add(self.tree_input.master,    text="Needs Your Input (0)")
        self.nb.add(self.tree_aireview.master, text="AI Review Queue (0)")
        self.nb.add(self.tree_noreply.master,  text="No Reply (0)")
        self.nb.add(self.tree_deleted.master,  text="Deleted (0)")
        self.nb.add(self.tree_done.master,     text="Decided (0)")

        # double-click → review
        self.tree_queue.bind("<Double-1>",
            lambda e: self._open_review(self.tree_queue))
        self.tree_input.bind("<Double-1>",
            lambda e: self._open_review(self.tree_input))
        self.tree_aireview.bind("<Double-1>",
            lambda e: self._open_review(self.tree_aireview))
        self.tree_noreply.bind("<Double-1>",
            lambda e: self._open_review(self.tree_noreply, from_no_reply=True))
        self.tree_deleted.bind("<Double-1>",
            lambda e: self._open_review(self.tree_deleted, from_deleted=True))
        self.tree_done.bind("<Double-1>",
            lambda e: self._open_review(self.tree_done, read_only=True))

        # Delete key on queue / no-reply / decided → soft-delete selection
        for tree in (self.tree_queue, self.tree_input, self.tree_noreply,
                     self.tree_done):
            tree.bind("<Delete>", lambda e, t=tree: self._delete_selection(t))
        # Delete on deleted tab → permanent hard-delete (with confirm)
        self.tree_deleted.bind("<Delete>",
            lambda e: self._purge_selection(self.tree_deleted))
        # v1.2.0: Space toggles the checkbox on the selected queue row(s);
        # arrow keys already move the selection natively. "break" stops the
        # default Space behavior (which would re-toggle selection).
        for _tv in (self.tree_queue, self.tree_input):
            _tv.bind("<space>",
                     lambda e, t_=_tv: (self._toggle_checks(t_), "break")[1])
            _tv.bind("<Button-1>",
                     lambda e, t_=_tv: self._on_queue_click(e, t_))

    # ------------------------------------------------------- window geometry
    _DEFAULT_GEOMETRY = "1060x660"

    def _geometry_for(self, key, default):
        """v1.7.0: saved geometry for any window, clamped on-screen.
        Keyed by window name so each remembers its own size and position."""
        store = self.settings.get("window_geometry")
        if isinstance(store, str):
            # v1.5.0 stored only the main window as a bare string — migrate
            store = {"main": store}
            self.settings["window_geometry"] = store
        if not isinstance(store, dict):
            store = {}
            self.settings["window_geometry"] = store
        g = store.get(key)
        if not g or not isinstance(g, str):
            return default
        m = re.match(r"^(\d+)x(\d+)(?:\+(-?\d+)\+(-?\d+))?$", g.strip())
        if not m:
            return default
        w, h = int(m.group(1)), int(m.group(2))
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = max(480, min(w, sw))
        h = max(320, min(h, sh))
        if m.group(3) is None:
            return "%dx%d" % (w, h)
        x, y = int(m.group(3)), int(m.group(4))
        x = max(0, min(x, sw - 100))
        y = max(0, min(y, sh - 100))
        return "%dx%d+%d+%d" % (w, h, x, y)

    def _save_geometry_for(self, key, win):
        try:
            store = self.settings.get("window_geometry")
            if not isinstance(store, dict):
                store = {}
            store[key] = win.winfo_geometry()
            self.settings["window_geometry"] = store
            drafts.save_settings(self.store.dir, self.settings)
        except Exception:
            pass

    def _bind_geometry(self, win, key, default):
        """Restore this window's saved geometry and save it again on close."""
        win.geometry(self._geometry_for(key, default))

        def on_close():
            self._save_geometry_for(key, win)
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", on_close)
        return on_close

    def _restore_geometry(self):
        return self._geometry_for("main", self._DEFAULT_GEOMETRY)

    def _save_geometry(self):
        self._save_geometry_for("main", self.root)

    def _on_close(self):
        try:
            if getattr(self, "diag", None) is not None:
                self.diag.stop()
        except Exception:
            pass
        self._save_geometry()
        try:
            self.store.close()
        except Exception:
            pass
        self.root.destroy()

    def _build_toolbar(self):
        """v1.3.0: (re)build the toolbar from the saved layout. Called at
        startup and again after Visual settings are saved."""
        for w in self.toolbar.winfo_children():
            w.destroy()
        btn_opts = dict(bg=_BG3, fg=_FG, activebackground=_ACCENT,
                        activeforeground=_SEL_FG, relief="flat",
                        font=(_FONT, _FONT_SZ), padx=8, pady=3, bd=0)
        layout = normalize_toolbar_layout(
            self.settings.get("toolbar_layout"))
        self.settings["toolbar_layout"] = layout
        by_id = {b[0]: b for b in TOOLBAR_BUTTONS}
        first = True
        self.btn_outlook = None
        for entry in layout:
            if not entry["visible"]:
                continue
            bid, label, method = by_id[entry["id"]]
            btn = tk.Button(self.toolbar, text=label,
                            command=getattr(self, method), **btn_opts)
            btn.pack(side="left", padx=((8, 2) if first else 2))
            first = False
            if bid == "scan_outlook":
                self.btn_outlook = btn
                if not mail.COM_AVAILABLE:
                    btn.config(state="disabled", fg=_FG_DIM)

    def _make_tree(self, done=False, deleted=False, checks=False,
                   ai_status=False):
        frame = tk.Frame(self.nb, bg=_BG)
        cols = ("received", "sender", "subject", "category", "conf")
        heads = ("Received", "From", "Subject", "AI Category", "Conf")
        if checks:
            cols  = ("chk",) + cols
            heads = ("✓",) + heads
        if done:
            cols  = cols  + ("action",)
            heads = heads + ("Decision",)
        if deleted:
            cols  = cols  + ("deleted_at",)
            heads = heads + ("Deleted",)
        if ai_status:
            cols  = cols  + ("aistate",)
            heads = heads + ("AI Review",)
        tree = ttk.Treeview(frame, columns=cols, show="headings",
                            selectmode="extended")   # ← multi-select
        widths = {"chk": 34, "received": 140, "sender": 185, "subject": 300,
                  "category": 155, "conf": 50, "action": 110,
                  "deleted_at": 130, "aistate": 110}
        for c, h in zip(cols, heads):
            tree.heading(c, text=h)
            tree.column(c, width=widths.get(c, 100),
                        anchor=("center" if c == "chk" else "w"),
                        stretch=(c == "subject"))
        if checks:
            # v1.3.0: clicking the ✓ heading toggles select all / none
            tree.heading("chk", text="✓",
                         command=lambda t_=tree: self._toggle_all_checks(t_))
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        return tree

    # ------------------------------------------------------------- refreshing
    def _refresh_lists(self):
        pending = self.store.pending()
        pending_ids = {r["message_id"] for r in pending}
        # v1.12.1: drop queue entries whose email has since been decided or
        # deleted, so the dict can't grow stale keys forever
        for gone in [m for m in self.ai_queue if m not in pending_ids]:
            self.ai_queue.pop(gone, None)
        q_rows, input_rows, nr_rows, ai_rows = partition_pending(
            pending, self.ai_queue)
        nr_rows = list(nr_rows) + self.store.by_action(
            rec.ACTION_MOVED_NO_REPLY)
        del_rows = self.store.by_action(rec.ACTION_DELETED)
        done = []
        for a in (rec.ACTION_ACCEPTED, rec.ACTION_RECATEGORIZED,
                  rec.ACTION_EDITED, rec.ACTION_DECLINED,
                  rec.ACTION_AUTO_SENT):
            done.extend(self.store.by_action(a))
        done.sort(key=lambda r: r.get("decided_at") or "", reverse=True)

        self._fill(self.tree_queue,    q_rows, checks=True)
        self._fill(self.tree_input,    input_rows, checks=True)
        self._fill(self.tree_aireview, ai_rows, ai_status=True)
        self._fill(self.tree_noreply,  nr_rows)
        self._fill(self.tree_deleted,  del_rows, deleted=True)
        self._fill(self.tree_done,     done,     done=True)
        # prune ticks once, against BOTH checkable tabs — pruning inside
        # _fill would let one tab wipe the other's ticks
        live = set(self.tree_queue.get_children()) \
            | set(self.tree_input.get_children())
        self.checked &= live
        self.nb.tab(_TAB_QUEUE,   text="Auto-Reply Queue (%d)" % len(q_rows))
        self.nb.tab(_TAB_INPUT,   text="Needs Your Input (%d)"
                    % len(input_rows))
        self.nb.tab(_TAB_AIREVIEW, text="AI Review Queue (%d)" % len(ai_rows))
        self.nb.tab(_TAB_NOREPLY, text="No Reply (%d)" % len(nr_rows))
        self.nb.tab(_TAB_DELETED, text="Deleted (%d)" % len(del_rows))
        self.nb.tab(_TAB_DECIDED, text="Decided (%d)" % len(done))

    def _fill(self, tree, rows, done=False, deleted=False, checks=False,
              ai_status=False):
        tree.delete(*tree.get_children())
        for r in rows:
            vals = (r.get("received_at", "")[:16].replace("T", " "),
                    r.get("sender", ""),
                    r.get("subject", ""),
                    CATEGORY_LABELS.get(r.get("ai_category"),
                                        r.get("ai_category") or ""),
                    "%.0f%%" % (100 * (r.get("ai_confidence") or 0)))
            if checks:
                mark = "☑" if r["message_id"] in self.checked else "☐"
                vals = (mark,) + vals
            if done:
                vals = vals + (r.get("user_action", ""),)
            if deleted:
                vals = vals + (
                    (r.get("decided_at") or "")[:16].replace("T", " "),)
            if ai_status:
                vals = vals + (self.ai_queue.get(r["message_id"], "queued"),)
            tree.insert("", "end", iid=r["message_id"], values=vals)

    # -------------------------------------------------------- checkbox logic
    def _toggle_all_checks(self, tree=None):
        """v1.3.0: ✓ heading click — if every visible row is checked,
        uncheck all; otherwise check all."""
        tree = tree or self.tree_queue
        rows = list(tree.get_children())
        if not rows:
            return
        if all(mid in self.checked for mid in rows):
            for mid in rows:
                self.checked.discard(mid)
                tree.set(mid, "chk", "☐")
            self._set_status("All unchecked.")
        else:
            for mid in rows:
                self.checked.add(mid)
                tree.set(mid, "chk", "☑")
            self._set_status("All %d checked." % len(rows))

    def _toggle_checks(self, tree=None):
        """Space: flip the checkbox on every selected row."""
        tree = tree or self.tree_queue
        for mid in tree.selection():
            if mid in self.checked:
                self.checked.discard(mid)
                tree.set(mid, "chk", "☐")
            else:
                self.checked.add(mid)
                tree.set(mid, "chk", "☑")

    def _on_queue_click(self, event, tree=None):
        """Click directly on the ✓ column toggles that row's checkbox."""
        tree = tree or self.tree_queue
        if tree.identify_column(event.x) != "#1":
            return
        mid = tree.identify_row(event.y)
        if not mid:
            return
        if mid in self.checked:
            self.checked.discard(mid)
            tree.set(mid, "chk", "☐")
        else:
            self.checked.add(mid)
            tree.set(mid, "chk", "☑")
        return "break"

    # ------------------------------------------------------------ multi-delete
    def _delete_selection(self, tree):
        """Move selected items to Deleted. Records as ACTION_DELETED (which
        counts as no_reply + accepted) so the AI learns the pattern."""
        sel = tree.selection()
        if not sel:
            return
        n = len(sel)
        label = ("this email" if n == 1
                 else "%d emails" % n)
        if not messagebox.askyesno(
                APP_TITLE,
                "Move %s to Deleted?\n\n"
                "The AI will learn these don't need replies. "
                "You can always undo from the Deleted tab." % label):
            return
        for mid in sel:
            self.auto.cancel(mid)   # v1.2.0: deleting cancels scheduled send
            self.ai_queue.pop(mid, None)   # v1.12.1
            self.store.record_decision(mid, rec.ACTION_DELETED,
                                       final_category=clf.CAT_NO_REPLY,
                                       final_draft="")
        self._refresh_lists()
        self._set_status("Moved %d item(s) to Deleted." % n)

    def _purge_selection(self, tree):
        """Permanently remove records (hard delete — no undo). Separate from
        the soft-delete path so accidental Delete on the Deleted tab always
        asks first."""
        sel = tree.selection()
        if not sel:
            return
        if not messagebox.askyesno(
                APP_TITLE,
                "Permanently delete %d record(s)? This cannot be undone." %
                len(sel), icon="warning"):
            return
        self.store.purge(list(sel))
        self._refresh_lists()
        self._set_status("Permanently removed %d record(s)." % len(sel))

    def _undo_delete(self, message_ids):
        """Restore soft-deleted items back to pending with their original
        AI classification intact."""
        for mid in message_ids:
            self.store.reopen(mid)
        self._refresh_lists()
        self._set_status("Restored %d item(s) to queue." % len(message_ids))

    # ------------------------------------------------------- bulk operations
    def _bulk_targets(self):
        """(tab_index, message_ids) for the active tab. On the queue, ticked
        rows win over the highlight — that matches AI Review and lets a
        selection survive scrolling."""
        try:
            idx = self.nb.index(self.nb.select())
        except Exception:
            return None, []
        if idx in (_TAB_QUEUE, _TAB_INPUT):
            tree = self.tree_queue if idx == _TAB_QUEUE else self.tree_input
            ids = [m for m in tree.get_children() if m in self.checked] \
                or list(tree.selection())
        elif idx == _TAB_AIREVIEW:
            tree, ids = self.tree_aireview, list(
                self.tree_aireview.selection())
        elif idx == _TAB_NOREPLY:
            tree, ids = self.tree_noreply, list(self.tree_noreply.selection())
        elif idx == _TAB_DELETED:
            tree, ids = self.tree_deleted, list(self.tree_deleted.selection())
        else:
            return idx, []
        return idx, ids

    def _bulk_guard(self, action_name, allowed_tabs):
        idx, ids = self._bulk_targets()
        if idx is None:
            return None
        if idx not in allowed_tabs:
            self._set_status(
                "%s applies to the %s tab." % (
                    action_name,
                    " or ".join("Auto-Reply Queue" if t == _TAB_QUEUE
                                else "No Reply" if t == _TAB_NOREPLY
                                else "Deleted" for t in allowed_tabs)))
            return None
        if not ids:
            self._set_status("Nothing selected — tick rows with Space or "
                             "click to select.")
            return None
        return ids

    def _apply_bulk(self, ids, chosen, send=False):
        """Record a decision for each id. `chosen` is BULK_KEEP or a
        category. Returns (recorded, sent, skipped)."""
        recorded, sent, skipped = 0, 0, 0
        for mid in ids:
            row = self.store.get(mid)
            if row is None:
                skipped += 1
                continue
            ai_cat = row.get("ai_category")
            action, cat, regen = bulk_resolution(ai_cat, chosen)
            if action == rec.ACTION_MOVED_NO_REPLY:
                self.auto.cancel(mid)
                self.store.record_decision(mid, action, clf.CAT_NO_REPLY, "")
                recorded += 1
                continue
            if regen:
                # recategorized — regenerate the draft for the new category
                # rather than sending text written for the old one
                draft, _src = drafts.make_draft(
                    cat, row.get("sender", "").split("@")[0],
                    row.get("sender", ""), row.get("subject", ""),
                    row.get("body_full") or "", self.settings,
                    voice_examples=self._voice_for(cat))
            else:
                draft = row.get("ai_draft") or ""
            self.auto.cancel(mid)
            self.store.record_decision(mid, action, cat, draft)
            recorded += 1
            if send:
                if draft.strip():
                    self.send_reply_async(mid, row.get("sender", ""),
                                          row.get("subject", ""), draft)
                    sent += 1
                else:
                    skipped += 1
        self.checked -= set(ids)
        for mid in ids:
            self.ai_queue.pop(mid, None)   # v1.12.1
        self._refresh_lists()
        return recorded, sent, skipped

    def bulk_accept(self):
        ids = self._bulk_guard("Accept", (_TAB_QUEUE, _TAB_INPUT, _TAB_AIREVIEW))
        if not ids:
            return
        chosen = _pick_bulk_category(self, len(ids))
        if not chosen:
            return
        recorded, _s, skipped = self._apply_bulk(ids, chosen, send=False)
        self._set_status(
            "Recorded %d decision(s)%s. Nothing was sent."
            % (recorded, " (%d skipped)" % skipped if skipped else ""))

    def bulk_accept_send(self):
        ids = self._bulk_guard("Accept & Send",
                               (_TAB_QUEUE, _TAB_INPUT, _TAB_AIREVIEW))
        if not ids:
            return
        chosen = _pick_bulk_category(self, len(ids))
        if not chosen:
            return
        if not messagebox.askyesno(
                APP_TITLE,
                "Send %d repl%s now?\n\nThis sends real email through "
                "Outlook and cannot be undone."
                % (len(ids), "y" if len(ids) == 1 else "ies"),
                icon="warning"):
            return
        recorded, sent, skipped = self._apply_bulk(ids, chosen, send=True)
        self._set_status(
            "Recorded %d, sending %d%s."
            % (recorded, sent,
               " (%d had no draft)" % skipped if skipped else ""))

    def bulk_decline(self):
        ids = self._bulk_guard("Decline", (_TAB_QUEUE, _TAB_INPUT, _TAB_AIREVIEW))
        if not ids:
            return
        for mid in ids:
            self.auto.cancel(mid)
            self.ai_queue.pop(mid, None)
            self.store.record_decision(mid, rec.ACTION_DECLINED)
        self.checked -= set(ids)
        self._refresh_lists()
        self._set_status("Declined %d — no replies will be sent." % len(ids))

    def bulk_no_reply(self):
        ids = self._bulk_guard("Move to No Reply",
                               (_TAB_QUEUE, _TAB_INPUT, _TAB_AIREVIEW))
        if not ids:
            return
        for mid in ids:
            self.auto.cancel(mid)
            self.ai_queue.pop(mid, None)
            self.store.record_decision(mid, rec.ACTION_MOVED_NO_REPLY,
                                       clf.CAT_NO_REPLY, "")
        self.checked -= set(ids)
        self._refresh_lists()
        self._set_status("Moved %d to No Reply." % len(ids))

    def bulk_needs_input(self):
        """Flag/unflag rows as needing the user's own knowledge. Flagged
        rows move to their own tab and can never auto-send."""
        idx, ids = self._bulk_targets()
        if not ids:
            self._set_status("Nothing selected.")
            return
        if idx == _TAB_INPUT:
            for m in ids:
                self.store.set_needs_input(m, False)
            msg = "Returned %d to the Auto-Reply Queue." % len(ids)
        else:
            for m in ids:
                self.auto.cancel(m)
                self.store.set_needs_input(m, True)
            msg = ("Flagged %d as needing your input — they will not "
                   "auto-send." % len(ids))
        self.checked -= set(ids)
        self._refresh_lists()
        self._set_status(msg)

    def bulk_requeue(self):
        """Undo from No Reply or Deleted — back to pending."""
        ids = self._bulk_guard("Back to Queue", (_TAB_NOREPLY, _TAB_DELETED))
        if not ids:
            return
        for mid in ids:
            self.store.reopen(mid)
        self._refresh_lists()
        self._set_status("Returned %d to the Auto-Reply Queue." % len(ids))

    # -------------------------------------------------------------- importing
    def import_files(self):
        paths = filedialog.askopenfilenames(
            title="Select .eml files",
            filetypes=[("Email files", "*.eml")])
        if paths:
            self._ingest_async(
                lambda: [mail.parse_eml_file(p) for p in paths])

    def import_folder(self):
        folder = filedialog.askdirectory(
            title="Folder containing .eml files")
        if folder:
            self._ingest_async(lambda: mail.scan_eml_folder(folder))

    def scan_outlook(self):
        if not mail.COM_AVAILABLE:
            messagebox.showinfo(APP_TITLE, "Outlook COM not available.")
            return
        # v1.9.0: scan the folders chosen in Settings -> Mailboxes.
        # Empty selection keeps the original behavior (default Inbox).
        paths = [p for p in (self.settings.get("scan_folders") or []) if p]
        per_folder = int(self.settings.get("scan_max_per_folder", 100) or 100)
        self._last_scan_report = []

        def fetch():
            items, report = mail.scan_outlook_folders(
                paths or None, max_items=per_folder)
            self._last_scan_report = report
            return items

        self._ingest_async(fetch)

    def _ingest_async(self, fetch_fn):
        if self.busy:
            messagebox.showinfo(APP_TITLE, "A scan is already running.")
            return
        self.busy = True
        self._set_status("Scanning / classifying…")
        known = self.store.known_ids()

        def worker():
            new_count, seen_count, err = 0, 0, None
            try:
                items = fetch_fn()
                for it in items:
                    mid = it.get("message_id") or \
                        rec.RecordStore.fallback_message_id(
                            it.get("sender", ""), it.get("subject", ""),
                            it.get("received_at", ""), it.get("body", ""))
                    if mid in known:
                        seen_count += 1
                        continue
                    res = clf.classify(it.get("subject", ""),
                                       it.get("sender", ""),
                                       it.get("body", ""))
                    draft_text, dsrc = "", "template"
                    if res["needs_reply"] and \
                       res["category"] != clf.CAT_NO_REPLY:
                        draft_text, dsrc = drafts.make_draft(
                            res["category"],
                            it.get("sender_name", ""),
                            it.get("sender", ""),
                            it.get("subject", ""),
                            it.get("body", ""),
                            self.settings,
                            voice_examples=self._voice_for(res["category"]))
                    inserted = self.store.upsert_intake(
                        mid, it.get("received_at", ""),
                        it.get("subject", ""), it.get("sender", ""),
                        res["features"], res["needs_reply"],
                        res["category"], res["confidence"],
                        draft_text,
                        "%s/%s" % (res["source"], dsrc),
                        it.get("body", ""),
                        needs_input=res.get("needs_input", False))
                    if inserted:
                        new_count += 1
                        known.add(mid)
            except Exception as e:
                err = str(e)
            self.ui_queue.put(("ingest_done", new_count, seen_count, err))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_ui_queue(self):
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                if msg[0] == "ingest_done":
                    _, new_count, seen_count, err = msg
                    self.busy = False
                    if err:
                        self._set_status("Scan error: %s" % err)
                    else:
                        rep = getattr(self, "_last_scan_report", []) or []
                        extra = ""
                        if rep:
                            missing = [r[0] for r in rep if r[2] != "ok"]
                            extra = "  [%d folder(s)" % len(rep)
                            if missing:
                                extra += ", %d not found" % len(missing)
                            extra += "]"
                        self._set_status(
                            "Scan complete: %d new, %d already known.%s"
                            % (new_count, seen_count, extra))
                    self._refresh_lists()
                    # v1.2.0: evaluate auto-send eligibility after each scan
                    newly = self.auto.evaluate_and_schedule()
                    if newly:
                        self._set_status(
                            "Scan complete: %d new. %d auto-send(s) "
                            "scheduled — %ds undo window."
                            % (new_count, len(newly), self.auto.delay_sec()))
                elif msg[0] == "send_done":
                    _, ok, detail = msg
                    self._set_status("Send: %s" % detail)
                    self._refresh_lists()
                elif msg[0] == "reclassify_done":
                    _, changed, same, err = msg
                    self.busy = False
                    if err:
                        self._set_status("Reclassify error: %s" % err)
                    else:
                        self._set_status(
                            "Reclassified: %d changed category, %d unchanged."
                            % (changed, same))
                    self._refresh_lists()
                elif msg[0] == "ai_state":
                    _, mid, state = msg
                    if mid in self.ai_queue:
                        self.ai_queue[mid] = state
                        try:
                            self.tree_aireview.set(mid, "aistate", state)
                        except Exception:
                            pass
                elif msg[0] == "ai_review_progress":
                    _, i, n, subject = msg
                    self._set_status(
                        "AI Review %d/%d: %s" % (i, n, subject[:60]))
                elif msg[0] == "ai_review_done":
                    _, improved, total, err = msg
                    self.busy = False
                    # v1.12.1: tailored rows return to their own tab on
                    # their own. Leaving them here would strand them away
                    # from the queue where decisions actually get made;
                    # anything that failed stays listed with its reason.
                    returned = [m for m, s in list(self.ai_queue.items())
                                if s == "done"]
                    for m in returned:
                        self.ai_queue.pop(m, None)
                    left = len(self.ai_queue)
                    if err:
                        self._set_status("AI Review error: %s" % err)
                    else:
                        self._set_status(
                            "AI Review: %d of %d tailored and returned to "
                            "their tab%s."
                            % (improved, total,
                               "; %d still queued" % left if left else ""))
                    self._refresh_lists()
        except _queue.Empty:
            pass
        self._auto_tick()
        self.root.after(150, self._drain_ui_queue)

    def _auto_tick(self):
        """v1.2.0: fire due auto-sends (re-verified eligible inside due())
        and keep a countdown in the status bar while any are scheduled."""
        for mid in self.auto.due():
            row = self.store.get(mid)
            if row is None:
                continue
            self.store.record_decision(mid, rec.ACTION_AUTO_SENT)
            self.send_reply_async(mid, row.get("sender", ""),
                                  row.get("subject", ""),
                                  row.get("ai_draft") or "")
            self._set_status("Auto-sent: %s" % (row.get("subject") or mid))
            self._refresh_lists()
        n = self.auto.pending_count()
        if n:
            secs = self.auto.next_fire_in()
            self._set_status(
                "%d auto-send(s) scheduled — next in %ds. "
                "Open or Delete an email to cancel its send." % (n, secs))

    def _set_status(self, msg):
        self.status_var.set(msg)

    def _voice_for(self, category):
        """The user's own confirmed replies for a category, as style examples.

        Only promoted rows qualify (phrasing_examples enforces that), so an
        unconfirmed import guess can never shape a draft. Failures are
        swallowed deliberately: drafting must keep working on the template
        alone if the learning store is unavailable, which is exactly what
        happened for every draft before this was wired up.
        """
        try:
            return self.learn.phrasing_examples(category, limit=5)
        except Exception:
            return []

    # ----------------------------------------------------------------- review
    def _open_review(self, tree, from_no_reply=False,
                     from_deleted=False, read_only=False):
        sel = tree.selection()
        if not sel:
            return
        # Only open a review window for the first selected item when
        # multi-select; bulk actions use the Delete key path.
        if self.auto.cancel(sel[0]):   # v1.2.0: opening cancels its auto-send
            self._set_status("Auto-send cancelled — you're reviewing it.")
        row = self.store.get(sel[0])
        if row is None:
            return
        ReviewWindow(self, row,
                     from_no_reply=from_no_reply,
                     from_deleted=from_deleted,
                     read_only=read_only)

    # ------------------------------------------------------------ reclassify
    def reclassify_pending(self):
        """v1.13.0: re-run classification over every pending row.

        The classifier has changed repeatedly; rows scanned under older logic
        carry stale verdicts, so a review pass spends its time correcting
        bugs that are already fixed. Only PENDING rows are rewritten —
        decided rows are corpus and must keep the verdict the user actually
        agreed or disagreed with.
        """
        pending = self.store.pending()
        if not pending:
            self._set_status("Nothing pending to reclassify.")
            return
        if self.busy:
            messagebox.showinfo(APP_TITLE, "A task is already running.")
            return
        if not messagebox.askyesno(
                APP_TITLE,
                "Re-run classification on %d pending email(s)?\n\n"
                "Drafts you have not yet accepted will be regenerated.\n"
                "Decided emails are left untouched." % len(pending)):
            return
        self.busy = True
        self._set_status("Reclassifying %d…" % len(pending))
        rows = [dict(r) for r in pending]
        settings = dict(self.settings)

        def worker():
            changed, same, err = 0, 0, None
            try:
                for i, row in enumerate(rows, 1):
                    if i % 10 == 0 or i == len(rows):
                        self.ui_queue.put(
                            ("ai_review_progress", i, len(rows),
                             "reclassifying"))
                    res = clf.classify(row.get("subject", ""),
                                       row.get("sender", ""),
                                       row.get("body_full") or "")
                    draft = ""
                    if res["needs_reply"] and \
                            res["category"] != clf.CAT_NO_REPLY:
                        draft, dsrc = drafts.make_draft(
                            res["category"],
                            row.get("sender", "").split("@")[0],
                            row.get("sender", ""), row.get("subject", ""),
                            row.get("body_full") or "", settings,
                            voice_examples=self._voice_for(res["category"]))
                    else:
                        dsrc = "template"
                    ok = self.store.reclassify_pending(
                        row["message_id"], res["category"],
                        res["confidence"], draft,
                        "%s/%s" % (res["source"], dsrc),
                        needs_reply=res["needs_reply"],
                        needs_input=res.get("needs_input", False))
                    if ok:
                        if res["category"] != row.get("ai_category"):
                            changed += 1
                        else:
                            same += 1
            except Exception as e:
                err = str(e)
            self.ui_queue.put(("reclassify_done", changed, same, err))

        threading.Thread(target=worker, daemon=True).start()

    # -------------------------------------------------------------- AI review
    # ------------------------------------------------------------ learning UI
    def open_learning(self):
        """v1.6.0: import MaINbox sent replies, review AI-inferred
        categories, confirm/correct. Staged rows are INERT — they live in
        their own table and cannot affect graduation until promoted here."""
        win = tk.Toplevel(self.root)
        win.title("Learn from Sent Mail — %s" % APP_TITLE)
        win.configure(bg=_BG)
        apply_theme(win)
        self._bind_geometry(win, "learning", "1180x700")

        # v1.7.0: every action lives in a top bar. They used to sit in a
        # right-hand column that fell outside the default window width, so
        # the window had to be resized before anything could be clicked.
        bar = tk.Frame(win, bg=_BG2, pady=5)
        bar.pack(fill="x")
        bar2 = tk.Frame(win, bg=_BG2, pady=4)
        bar2.pack(fill="x")
        status_var = tk.StringVar(value="")

        cols = ("conf", "cat", "when", "who", "reply")
        tree = ttk.Treeview(win, columns=cols, show="headings",
                            selectmode="extended")
        for c, h, w in (("conf", "Conf", 55), ("cat", "AI thinks", 150),
                        ("when", "Sent", 120), ("who", "To / From", 200),
                        ("reply", "What you wrote", 520)):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor="w", stretch=(c == "reply"))
        vsb = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True, padx=(8, 0),
                  pady=(6, 8))
        vsb.pack(side="left", fill="y", pady=(6, 8), padx=(0, 8))

        def refresh():
            tree.delete(*tree.get_children())
            rows = self.learn.by_status(learn.STATUS_STAGED)
            for r in rows:
                who = r["orig_from_email"] or r["to_addr"] or ""
                tree.insert("", "end", iid=r["stage_id"], values=(
                    "%.0f%%" % (100 * (r["ai_confidence"] or 0)),
                    CATEGORY_LABELS.get(r["ai_category"], r["ai_category"]),
                    (r["sent_on"] or "")[:16].replace("T", " "),
                    who[:34],
                    " ".join((r["reply_text"] or "").split())[:120]))
            c = self.learn.counts()
            oc = self.store.origin_counts()
            status_var.set(
                "Staged (inert): %d   |   confirmed: %d   corrected: %d   "
                "ignored: %d   |   corpus: %d live, %d imported"
                % (c.get(learn.STATUS_STAGED, 0),
                   c.get(learn.STATUS_CONFIRMED, 0),
                   c.get(learn.STATUS_CORRECTED, 0),
                   c.get(learn.STATUS_IGNORED, 0),
                   oc.get("live", 0), oc.get("import", 0)))

        def do_import():
            path = filedialog.askopenfilename(
                title="Select MaINbox sent-mail JSON export",
                filetypes=[("JSON", "*.json")], parent=win)
            if not path:
                return
            how_many = simpledialog.askstring(
                APP_TITLE,
                "How many of the most recent replies should I pull?\n"
                "(number, or 'all')", initialvalue="100", parent=win)
            if how_many is None:
                return
            how_many = how_many.strip().lower()
            limit = None if how_many in ("all", "") else None
            if how_many not in ("all", ""):
                try:
                    limit = max(1, int(how_many))
                except ValueError:
                    messagebox.showerror(APP_TITLE, "Enter a number or 'all'.",
                                         parent=win)
                    return
            try:
                records = learn.load_sent_json(path)
                cands, st = learn.select_candidates(records, limit=limit)
                new, skipped = self.learn.stage(cands)
            except Exception as e:
                messagebox.showerror(APP_TITLE, "Import failed:\n%s" % e,
                                     parent=win)
                return
            refresh()
            messagebox.showinfo(
                APP_TITLE,
                "Read %d sent items.\n\n"
                "  %d skipped (not a reply)\n"
                "  %d skipped (no text you wrote)\n"
                "  %d flagged as follow-ups to your own RFQs\n"
                "  %d flagged as internal (%s)\n\n"
                "Staged %d for review (%d already known).\n\n"
                "Nothing here affects learning until you confirm it."
                % (st["total"], st["not_reply"], st["no_reply_text"],
                   st["outbound_followup"], st.get("internal", 0),
                   st.get("own_domain") or "own domain unknown",
                   new, skipped), parent=win)

        def selected_ids():
            return list(tree.selection())

        def confirm_sel():
            ids = selected_ids()
            if not ids:
                return
            n = 0
            undet = 0
            for sid in ids:
                ok, why = self.learn.confirm(sid)
                if ok:
                    n += 1
                elif why == "undetermined":
                    undet += 1
            refresh()
            msg = "Confirmed %d imported sample(s) into the corpus." % n
            if undet:
                # These were never classified — the importer returned escalate
                # because it could not tell. Confirming would record agreement
                # with a verdict that was never reached, so Correct is the only
                # way in.
                msg += ("  %d skipped: no category was inferred — use Correct "
                        "to assign one." % undet)
            self._set_status(msg)

        def correct_sel():
            ids = selected_ids()
            if not ids:
                return
            cat = _pick_category(self, win)
            if not cat:
                return
            n = 0
            for sid in ids:
                ok, _ = self.learn.confirm(sid, final_category=cat)
                if ok:
                    n += 1
            refresh()
            self._set_status("Corrected %d sample(s) to %s."
                             % (n, CATEGORY_LABELS.get(cat, cat)))

        def ignore_sel():
            for sid in selected_ids():
                self.learn.ignore(sid)
            refresh()

        def clear_staged():
            if not messagebox.askyesno(
                    APP_TITLE,
                    "Discard all un-confirmed staged rows?\n"
                    "Confirmed samples already in the corpus are kept.",
                    parent=win):
                return
            n = self.learn.unstage_all()
            refresh()
            self._set_status("Cleared %d staged row(s)." % n)

        def show_detail(_e=None):
            sel = selected_ids()
            if not sel:
                return
            r = self.learn.get(sel[0])
            if not r:
                return
            d = tk.Toplevel(win)
            d.title(r["subject"] or "(no subject)")
            d.configure(bg=_BG)
            apply_theme(d)
            self._bind_geometry(d, "learning_detail", "860x620")
            tk.Label(d, text="AI thinks: %s  (%.0f%% — %s)  •  INERT until "
                     "you confirm" % (
                         CATEGORY_LABELS.get(r["ai_category"],
                                             r["ai_category"]),
                         100 * (r["ai_confidence"] or 0), r["ai_source"]),
                     bg=_BG2, fg=_ACCENT, font=(_FONT, _FONT_SZ),
                     anchor="w", padx=8, pady=5).pack(fill="x")
            p = ttk.PanedWindow(d, orient="vertical")
            p.pack(fill="both", expand=True, padx=8, pady=6)
            f1 = ttk.LabelFrame(p, text="Incoming email (recovered)")
            t1 = tk.Text(f1, wrap="word", height=12, bg=_ENTRY_BG, fg=_FG,
                         relief="flat", font=(_FONT, _FONT_SZ))
            t1.insert("1.0", "From: %s\nSubject: %s\n\n%s" % (
                r["orig_from_email"] or r["orig_from_name"] or "(unknown)",
                r["orig_subject"] or "", r["orig_body"] or "(not recovered)"))
            t1.configure(state="disabled")
            t1.pack(fill="both", expand=True, padx=4, pady=4)
            p.add(f1, weight=3)
            f2 = ttk.LabelFrame(p, text="What you actually wrote")
            t2 = tk.Text(f2, wrap="word", height=8, bg=_ENTRY_BG, fg=_FG,
                         relief="flat", font=(_FONT, _FONT_SZ))
            t2.insert("1.0", r["reply_text"] or "")
            t2.configure(state="disabled")
            t2.pack(fill="both", expand=True, padx=4, pady=4)
            p.add(f2, weight=2)

        btn_opts = dict(bg=_BG3, fg=_FG, activebackground=_ACCENT,
                        activeforeground=_SEL_FG, relief="flat",
                        font=(_FONT, _FONT_SZ), padx=8, pady=3, bd=0)
        tk.Button(bar, text="Import sent JSON…", command=do_import,
                  **btn_opts).pack(side="left", padx=(8, 4))
        tk.Label(bar, textvariable=status_var, bg=_BG2, fg=_FG_DIM,
                 font=(_FONT, _FONT_SZ)).pack(side="left", padx=12)

        ReviewWindow._btn(bar2, "✓ Confirm", confirm_sel,
                          accent=True).pack(side="left", padx=(8, 3))
        ReviewWindow._btn(bar2, "✎ Correct…", correct_sel).pack(
            side="left", padx=3)
        ReviewWindow._btn(bar2, "Ignore", ignore_sel).pack(
            side="left", padx=3)
        ReviewWindow._btn(bar2, "View detail", show_detail).pack(
            side="left", padx=(14, 3))
        ReviewWindow._btn(bar2, "Clear staged", clear_staged).pack(
            side="left", padx=(14, 3))
        tk.Label(bar2, text="Confirm = AI was right   •   Correct = pick the "
                 "right one   •   Ignore or untouched = no effect on learning",
                 bg=_BG2, fg=_FG_DIM,
                 font=(_FONT, _FONT_SZ)).pack(side="left", padx=14)

        tree.bind("<Double-1>", show_detail)
        refresh()

    def ai_review(self):
        """v1.12.0: stage the checked emails into the AI Review Queue tab
        rather than firing immediately. On a long run the old behavior gave
        no way to see what was pending or to stop it — at ~30s an email, a
        70-email pass is half an hour with no exit."""
        mids = [m for m in (list(self.tree_queue.get_children())
                            + list(self.tree_input.get_children()))
                if m in self.checked]
        if not mids:
            idx, sel = self._bulk_targets()
            mids = sel
        if not mids:
            messagebox.showinfo(
                APP_TITLE,
                "Tick some emails first (Space, or the ✓ heading for all).")
            return
        added = sum(1 for m in mids if m not in self.ai_queue)
        for m in mids:
            self.ai_queue.setdefault(m, "queued")
        self.checked -= set(mids)
        self._refresh_lists()
        self.nb.select(_TAB_AIREVIEW)
        self._set_status(
            "Moved %d into the AI Review Queue%s. Pick an endpoint to run "
            "them, or Remove to send them back."
            % (added, " (%d already there)" % (len(mids) - added)
               if len(mids) > added else ""))

    def _ai_run(self, endpoint="auto"):
        """Process the queue on a worker thread. `endpoint` is auto/host/
        local. Cancellable between emails."""
        pending = [m for m, s in self.ai_queue.items()
                   if s in ("queued", "failed")]
        if not pending:
            self._set_status("Nothing queued for AI Review.")
            return
        if self.busy:
            messagebox.showinfo(APP_TITLE, "A task is already running.")
            return
        if clf.NO_LLM:
            messagebox.showinfo(APP_TITLE, "AI Review needs the LLM "
                                "(REPLYPILOT_NO_LLM=1 is set).")
            return
        self.busy = True
        self.ai_cancel.clear()
        rows = [self.store.get(m) for m in pending]
        settings = dict(self.settings)
        # snapshot the endpoint config, then force it if the user picked one
        base = clf.ai_settings_defaults()
        forced = dict(base)
        if endpoint == "local":
            forced["ai_host"] = base["ai_local_host"]
            forced["ai_port"] = base["ai_local_port"]
            forced["ai_host_model"] = base["ai_local_model"]
        elif endpoint == "host":
            forced["ai_local_host"] = base["ai_host"]
            forced["ai_local_port"] = base["ai_port"]
            forced["ai_local_model"] = base["ai_host_model"]
        self._set_status("AI Review starting on %s…" % endpoint)

        def worker():
            if endpoint in ("local", "host"):
                clf.apply_ai_settings(forced)
            try:
                label = clf.active_endpoint_label()
                if label is None:
                    self.ui_queue.put((
                        "ai_review_done", 0, len(rows),
                        "No Ollama reachable — host %s:%d and local %s:%d "
                        "both down." % (clf.OLLAMA_HOST, clf.OLLAMA_PORT,
                                        clf.LOCAL_OLLAMA_HOST,
                                        clf.LOCAL_OLLAMA_PORT)))
                    return
                where = "tillium (host)" if label == "host" else "local Ollama"
                improved, reasons, err, cancelled = 0, {}, None, 0
                try:
                    for i, row in enumerate(rows, 1):
                        if self.ai_cancel.is_set():
                            cancelled = len(rows) - i + 1
                            for r2 in rows[i - 1:]:
                                if r2:
                                    self.ui_queue.put(
                                        ("ai_state", r2["message_id"],
                                         "queued"))
                            break
                        if row is None:
                            reasons["gone"] = reasons.get("gone", 0) + 1
                            continue
                        mid = row["message_id"]
                        self.ui_queue.put(("ai_state", mid, "working…"))
                        self.ui_queue.put((
                            "ai_review_progress", i, len(rows),
                            "%s — via %s" % (
                                (row.get("subject") or "(no subject)")[:44],
                                where)))
                        if not (row.get("ai_draft") or "").strip():
                            reasons["no_draft"] = reasons.get("no_draft", 0) + 1
                            self.ui_queue.put(("ai_state", mid, "no draft"))
                            continue
                        polished, reason = drafts.polish_draft(
                            row["ai_draft"], row.get("subject", ""),
                            row.get("body_full") or "", settings=settings)
                        if polished and self.store.update_ai_draft(
                                mid, polished):
                            improved += 1
                            self.ui_queue.put(("ai_state", mid, "done"))
                        else:
                            reasons[reason] = reasons.get(reason, 0) + 1
                            self.ui_queue.put(("ai_state", mid, "failed"))
                except Exception as e:
                    err = str(e)
                if err is None and cancelled:
                    err = "cancelled — %d left queued" % cancelled
                elif err is None and improved == 0 and reasons:
                    err = "no drafts changed: " + ", ".join(
                        "%s x%d" % (k, v) for k, v in sorted(reasons.items()))
                self.ui_queue.put(("ai_review_done", improved, len(rows), err))
            finally:
                if endpoint in ("local", "host"):
                    clf.apply_ai_settings(base)

        threading.Thread(target=worker, daemon=True).start()

    def ai_run_auto(self):
        self._ai_run("auto")

    def ai_run_local(self):
        self._ai_run("local")

    def ai_run_host(self):
        self._ai_run("host")

    def ai_cancel_run(self):
        if not self.busy:
            self._set_status("Nothing running.")
            return
        self.ai_cancel.set()
        self._set_status("Cancelling after the current email…")

    def ai_remove_selected(self):
        sel = list(self.tree_aireview.selection())
        if not sel:
            self._set_status("Select rows in the AI Review Queue to remove.")
            return
        for m in sel:
            self.ai_queue.pop(m, None)
        self._refresh_lists()
        self._set_status("Returned %d to their original tab." % len(sel))

    def ai_clear_queue(self):
        n = len(self.ai_queue)
        self.ai_queue.clear()
        self._refresh_lists()
        self._set_status("Emptied the AI Review Queue — %d returned to "
                         "their tabs." % n)

    # ------------------------------------------------------------ settings UI
    def open_settings(self):
        """v1.5.0: tabbed settings — Auto-send, Drafting, AI Settings,
        Visual. AI Settings mirrors MaINbox (host + local models, blank
        falls back to default) and applies live on save; no restart."""
        win = tk.Toplevel(self.root)
        win.title("Settings — %s" % APP_TITLE)
        win.configure(bg=_BG)
        apply_theme(win)
        self._bind_geometry(win, "settings", "640x640")

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        def _entry(parent, var, width=8):
            return tk.Entry(parent, textvariable=var, width=width,
                            bg=_ENTRY_BG, fg=_FG, insertbackground=_ACCENT,
                            relief="flat")

        # ---------------- Tab 1: Auto-send --------------------------------
        t1 = tk.Frame(nb, bg=_BG); nb.add(t1, text="Auto-send")
        f = ttk.LabelFrame(t1, text="Auto-send engine", padding=8)
        f.pack(fill="x", padx=10, pady=10)
        master_var = tk.BooleanVar(
            value=bool(self.settings.get("auto_send_master", False)))
        ttk.Checkbutton(
            f, text="Master auto-send ON (categories still need to be "
            "graduated or overridden)", variable=master_var).pack(anchor="w")
        row1 = tk.Frame(f, bg=_BG); row1.pack(fill="x", pady=(6, 0))
        tk.Label(row1, text="Delay / undo window (seconds):", bg=_BG,
                 fg=_FG, font=(_FONT, _FONT_SZ)).pack(side="left")
        delay_var = tk.StringVar(
            value=str(self.settings.get("auto_send_delay_sec", 60)))
        _entry(row1, delay_var, 6).pack(side="left", padx=6)
        row2 = tk.Frame(f, bg=_BG); row2.pack(fill="x", pady=(6, 0))
        tk.Label(row2, text="Minimum AI confidence (0.0–1.0):", bg=_BG,
                 fg=_FG, font=(_FONT, _FONT_SZ)).pack(side="left")
        conf_var = tk.StringVar(
            value=str(self.settings.get("auto_send_min_conf", 0.85)))
        _entry(row2, conf_var, 6).pack(side="left", padx=6)
        tk.Label(f, text="Escalate and No-Reply are never auto-sent, "
                 "regardless of settings.", bg=_BG, fg=_FG_DIM,
                 font=(_FONT, _FONT_SZ)).pack(anchor="w", pady=(6, 0))

        # ---------------- Tab 2: Drafting ---------------------------------
        t2 = tk.Frame(nb, bg=_BG); nb.add(t2, text="Drafting")
        f2 = ttk.LabelFrame(t2, text="Drafting", padding=8)
        f2.pack(fill="both", expand=True, padx=10, pady=10)
        polish_var = tk.BooleanVar(
            value=bool(self.settings.get("use_llm_polish", False)))
        ttk.Checkbutton(
            f2, text="LLM-polish drafts automatically at scan time "
            "(AI Review button works either way)",
            variable=polish_var).pack(anchor="w")
        tk.Label(f2, text="Signature:", bg=_BG, fg=_FG,
                 font=(_FONT, _FONT_SZ)).pack(anchor="w", pady=(6, 0))
        sig_txt = tk.Text(f2, height=5, bg=_ENTRY_BG, fg=_FG,
                          insertbackground=_ACCENT, relief="flat",
                          font=(_FONT, _FONT_SZ))
        sig_txt.insert("1.0", self.settings.get("signature", ""))
        sig_txt.pack(fill="both", expand=True, pady=2)

        # v1.7.0: pull the real Outlook signature so replies look like the
        # rest of the user's mail. Signatures are plain files under
        # %APPDATA%\Microsoft\Signatures — no COM needed. The .txt form is
        # used because Replyit sends plain text.
        sigrow = tk.Frame(f2, bg=_BG)
        sigrow.pack(fill="x", pady=(6, 0))

        def import_outlook_sig():
            sigs = drafts.list_outlook_signatures()
            if not sigs:
                d = drafts.outlook_signature_dir()
                messagebox.showinfo(
                    APP_TITLE,
                    "No Outlook signatures found.\n\n%s"
                    % ("Looked in:\n%s" % d if d else
                       "The Signatures folder wasn't found — this is a "
                       "Windows/Outlook feature."), parent=win)
                return
            if len(sigs) == 1:
                name, path = sigs[0]
            else:
                name = _pick_from_list(
                    self, win, "Outlook signatures", [s[0] for s in sigs])
                if not name:
                    return
                path = dict(sigs)[name]
            text = drafts.read_signature_file(path)
            if not text:
                messagebox.showinfo(APP_TITLE,
                                    "That signature file was empty or "
                                    "unreadable.", parent=win)
                return
            sig_txt.delete("1.0", "end")
            sig_txt.insert("1.0", text)
            self.settings["signature_source"] = "outlook:%s" % name

        ReviewWindow._btn(sigrow, "Import text from Outlook…",
                          import_outlook_sig).pack(side="left")
        tk.Label(sigrow, text="reads %APPDATA%\\Microsoft\\Signatures",
                 bg=_BG, fg=_FG_DIM,
                 font=(_FONT, _FONT_SZ)).pack(side="left", padx=8)

        olsig_var = tk.BooleanVar(
            value=bool(self.settings.get("use_outlook_signature", True)))
        ttk.Checkbutton(
            f2, text="On send, let Outlook add your real signature "
            "(keeps logos and images)", variable=olsig_var).pack(
            anchor="w", pady=(8, 0))
        tk.Label(f2, text="Recommended. Outlook builds the reply with your "
                 "configured signature already in it, images and all — "
                 "Replyit writes the draft above it and the plain-text "
                 "signature below is stripped so it isn't duplicated. "
                 "That text version is what you see in the preview and what "
                 "gets used if Outlook isn't available.",
                 bg=_BG, fg=_FG_DIM, font=(_FONT, _FONT_SZ),
                 wraplength=560, justify="left").pack(anchor="w",
                                                      pady=(2, 0))

        # ---------------- Tab 3: AI Settings (MaINbox pattern) ------------
        t3 = tk.Frame(nb, bg=_BG); nb.add(t3, text="AI Settings")
        cur_ai = clf.ai_settings_defaults()
        cur_ai.update({k: self.settings[k] for k in clf.AI_SETTINGS_KEYS
                       if k in self.settings})

        fh = ttk.LabelFrame(t3, text="Host (tillium-bridge — tried first)",
                            padding=8)
        fh.pack(fill="x", padx=10, pady=(10, 4))
        ai_host_var = tk.StringVar(value=str(cur_ai["ai_host"]))
        ai_port_var = tk.StringVar(value=str(cur_ai["ai_port"]))
        ai_hmodel_var = tk.StringVar(value=str(cur_ai["ai_host_model"]))
        hr = tk.Frame(fh, bg=_BG); hr.pack(fill="x")
        tk.Label(hr, text="Address:", bg=_BG, fg=_FG, width=9, anchor="w",
                 font=(_FONT, _FONT_SZ)).pack(side="left")
        _entry(hr, ai_host_var, 18).pack(side="left")
        tk.Label(hr, text="Port:", bg=_BG, fg=_FG,
                 font=(_FONT, _FONT_SZ)).pack(side="left", padx=(10, 2))
        _entry(hr, ai_port_var, 7).pack(side="left")
        hr2 = tk.Frame(fh, bg=_BG); hr2.pack(fill="x", pady=(6, 0))
        tk.Label(hr2, text="Model:", bg=_BG, fg=_FG, width=9, anchor="w",
                 font=(_FONT, _FONT_SZ)).pack(side="left")
        host_model_cb = ttk.Combobox(hr2, textvariable=ai_hmodel_var,
                                     width=26, values=())
        host_model_cb.pack(side="left")

        fl = ttk.LabelFrame(
            t3, text="Local fallback (this PC — used if host down/busy)",
            padding=8)
        fl.pack(fill="x", padx=10, pady=4)
        ai_lhost_var = tk.StringVar(value=str(cur_ai["ai_local_host"]))
        ai_lport_var = tk.StringVar(value=str(cur_ai["ai_local_port"]))
        ai_lmodel_var = tk.StringVar(value=str(cur_ai["ai_local_model"]))
        lr = tk.Frame(fl, bg=_BG); lr.pack(fill="x")
        tk.Label(lr, text="Address:", bg=_BG, fg=_FG, width=9, anchor="w",
                 font=(_FONT, _FONT_SZ)).pack(side="left")
        _entry(lr, ai_lhost_var, 18).pack(side="left")
        tk.Label(lr, text="Port:", bg=_BG, fg=_FG,
                 font=(_FONT, _FONT_SZ)).pack(side="left", padx=(10, 2))
        _entry(lr, ai_lport_var, 7).pack(side="left")
        lr2 = tk.Frame(fl, bg=_BG); lr2.pack(fill="x", pady=(6, 0))
        tk.Label(lr2, text="Model:", bg=_BG, fg=_FG, width=9, anchor="w",
                 font=(_FONT, _FONT_SZ)).pack(side="left")
        local_model_cb = ttk.Combobox(lr2, textvariable=ai_lmodel_var,
                                      width=26, values=())
        local_model_cb.pack(side="left")

        fa = ttk.LabelFrame(t3, text="Advanced", padding=8)
        fa.pack(fill="x", padx=10, pady=4)
        ai_timeout_var = tk.StringVar(value=str(cur_ai["ai_timeout"]))
        ai_probe_var = tk.StringVar(value=str(cur_ai["ai_host_probe"]))
        ar = tk.Frame(fa, bg=_BG); ar.pack(fill="x")
        tk.Label(ar, text="Request timeout (s):", bg=_BG, fg=_FG,
                 font=(_FONT, _FONT_SZ)).pack(side="left")
        _entry(ar, ai_timeout_var, 6).pack(side="left", padx=(2, 12))
        tk.Label(ar, text="Host probe (s):", bg=_BG, fg=_FG,
                 font=(_FONT, _FONT_SZ)).pack(side="left")
        _entry(ar, ai_probe_var, 6).pack(side="left", padx=2)

        tk.Label(t3, text="Blank fields fall back to defaults. Blank local "
                 "model mirrors the host model.", bg=_BG, fg=_FG_DIM,
                 font=(_FONT, _FONT_SZ), wraplength=560,
                 justify="left").pack(anchor="w", padx=12, pady=(2, 0))

        ai_test_var = tk.StringVar(value="")
        tk.Label(t3, textvariable=ai_test_var, bg=_BG, fg=_ACCENT,
                 font=(_FONT, _FONT_SZ), wraplength=560,
                 justify="left").pack(anchor="w", padx=12, pady=(4, 0))

        def collect_ai():
            return {
                "ai_host": ai_host_var.get(), "ai_port": ai_port_var.get(),
                "ai_host_model": ai_hmodel_var.get(),
                "ai_local_host": ai_lhost_var.get(),
                "ai_local_port": ai_lport_var.get(),
                "ai_local_model": ai_lmodel_var.get(),
                "ai_timeout": ai_timeout_var.get(),
                "ai_host_probe": ai_probe_var.get(),
            }

        def _with_pending(fn):
            """Run fn with the on-screen values applied, then restore, so
            probing never commits settings the user hasn't saved."""
            saved = clf.ai_settings_defaults()
            clf.apply_ai_settings(collect_ai())
            try:
                return fn()
            finally:
                clf.apply_ai_settings(saved)

        def refresh_models():
            ai_test_var.set("Asking each endpoint what it has installed…")
            win.update_idletasks()
            host_models, local_models = _with_pending(
                lambda: clf.endpoint_models(timeout=5))
            host_model_cb.configure(values=tuple(host_models))
            local_model_cb.configure(values=tuple(local_models))
            bits = []
            bits.append("host: %d model(s)" % len(host_models)
                        if host_models else "host: unreachable")
            bits.append("local: %d model(s)" % len(local_models)
                        if local_models else "local: unreachable")
            # warn when the configured model isn't actually installed — this
            # is the "error:HTTPError" case, caught before it happens
            warn = []
            hm, lm = ai_hmodel_var.get().strip(), ai_lmodel_var.get().strip()
            if host_models and hm and not any(
                    m == hm or m.split(":")[0] == hm.split(":")[0]
                    for m in host_models):
                warn.append("host has no '%s'" % hm)
            if local_models and lm and not any(
                    m == lm or m.split(":")[0] == lm.split(":")[0]
                    for m in local_models):
                warn.append("local has no '%s'" % lm)
            msg = "   ".join(bits)
            if warn:
                msg += "   ⚠ " + "; ".join(warn) + " — pick from the dropdown."
            ai_test_var.set(msg)

        def test_conn():
            ai_test_var.set("Testing…")
            win.update_idletasks()
            label, eps = _with_pending(
                lambda: (clf.active_endpoint_label(timeout=4),
                         clf._endpoints()))
            if label == "host":
                ai_test_var.set("✓ Host reachable (%s:%s) — will serve first."
                                % (eps[0][1], eps[0][2]))
            elif label == "local":
                ai_test_var.set("Host down; ✓ local reachable — fallback "
                                "will serve.")
            else:
                ai_test_var.set("✗ Neither host nor local reachable.")

        aibtns = tk.Frame(t3, bg=_BG)
        aibtns.pack(anchor="w", padx=12, pady=(6, 0))
        ReviewWindow._btn(aibtns, "Test connection", test_conn).pack(
            side="left")
        ReviewWindow._btn(aibtns, "↻ Load installed models", refresh_models,
                          accent=True).pack(side="left", padx=6)

        # ---------------- Tab 4: Mailboxes --------------------------------
        t_mb = tk.Frame(nb, bg=_BG); nb.add(t_mb, text="Mailboxes")
        fmb = ttk.LabelFrame(
            t_mb, text="Folders to scan when you click Scan Outlook Inbox",
            padding=8)

        mb_status = tk.StringVar(value="")
        # working copies; committed on Save
        folder_cache = [dict(f) for f in
                        (self.settings.get("outlook_folders") or [])]
        selected = set(self.settings.get("scan_folders") or [])
        hide_empty_var = tk.BooleanVar(
            value=bool(self.settings.get("mb_hide_empty", True)))
        show_system_var = tk.BooleanVar(
            value=bool(self.settings.get("mb_show_system", False)))

        filt = tk.Frame(t_mb, bg=_BG)
        filt.pack(fill="x", padx=12, pady=(8, 0))
        ttk.Checkbutton(filt, text="Hide empty folders",
                        variable=hide_empty_var,
                        command=lambda: mb_render()).pack(side="left")
        ttk.Checkbutton(filt, text="Show Outlook system folders",
                        variable=show_system_var,
                        command=lambda: mb_render()).pack(side="left",
                                                          padx=14)
        fmb.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        mb_list = tk.Listbox(fmb, height=11, bg=_ENTRY_BG, fg=_FG,
                             selectbackground=_SEL_BG,
                             selectforeground=_SEL_FG, relief="flat",
                             font=(_FONT, _FONT_SZ), activestyle="none",
                             selectmode="extended", exportselection=False)
        mb_vsb = ttk.Scrollbar(fmb, orient="vertical", command=mb_list.yview)
        mb_list.configure(yscrollcommand=mb_vsb.set)
        mb_list.pack(side="left", fill="both", expand=True, pady=2)
        mb_vsb.pack(side="left", fill="y", pady=2)

        # display rows: ("store", name) headers or ("folder", cache_index).
        # Headers aren't selectable, so a row->folder map is required rather
        # than indexing folder_cache by listbox position.
        mb_rows = []

        def mb_visible(f):
            return folder_visible(f, selected, hide_empty_var.get(),
                                  show_system_var.get())

        def mb_render(keep=None):
            mb_list.delete(0, "end")
            mb_rows[:] = group_folders_by_store(
                folder_cache, selected, hide_empty_var.get(),
                show_system_var.get())
            shown = 0
            for kind, val in mb_rows:
                if kind == "store":
                    mb_list.insert("end", "\u2500\u2500  %s" % val)
                    mb_list.itemconfig("end", fg=_ACCENT)
                    continue
                f = folder_cache[val]
                mark = "\u2611" if f["path"] in selected else "\u2610"
                indent = "   " * int(f.get("depth", 0))
                cnt = f.get("count", -1)
                suffix = ("  (%s)" % cnt) \
                    if isinstance(cnt, int) and cnt >= 0 else ""
                mb_list.insert("end", "    %s %s%s%s"
                               % (mark, indent, f.get("name", ""), suffix))
                if f["path"] not in selected:
                    mb_list.itemconfig("end", fg=_FG_DIM)
                shown += 1
            if keep is not None and 0 <= keep < mb_list.size():
                mb_list.selection_set(keep)
                mb_list.see(keep)
            n = len(selected)
            hidden = len(folder_cache) - shown
            bits = ["%d folder(s) selected%s"
                    % (n, "" if n else " — the default Inbox will be used")]
            if folder_cache:
                bits.append("showing %d of %d" % (shown, len(folder_cache)))
                if hidden:
                    bits.append("%d hidden" % hidden)
            else:
                bits.append("press Refresh to read your Outlook folders")
            mb_status.set("   |   ".join(bits))

        def mb_show_path(_e=None):
            """Selecting a row shows its full path — the definitive answer to
            'which Inbox is this one?'"""
            sel = mb_list.curselection()
            if not sel:
                return
            kind, val = mb_rows[sel[0]] if sel[0] < len(mb_rows) else (None, None)
            if kind == "folder":
                mb_status.set(folder_cache[val]["path"])
            elif kind == "store":
                mb_status.set("Mailbox: %s" % val)

        def mb_toggle(_e=None):
            rows = list(mb_list.curselection())
            if not rows:
                return
            changed = False
            for r in rows:
                if r >= len(mb_rows):
                    continue
                kind, val = mb_rows[r]
                if kind != "folder":
                    continue
                p_ = folder_cache[val]["path"]
                if p_ in selected:
                    selected.discard(p_)
                else:
                    selected.add(p_)
                changed = True
            if changed:
                mb_render(rows[0])
            return "break"

        def mb_all():
            # only what's on screen — never silently select hidden plumbing
            for f in folder_cache:
                if mb_visible(f) or f["path"] in selected:
                    selected.add(f["path"])
            mb_render()

        def mb_none():
            selected.clear()
            mb_render()

        def mb_inboxes_only():
            selected.clear()
            for f in folder_cache:
                if (f.get("name", "").strip().lower() == "inbox"
                        and not f.get("system")):
                    selected.add(f["path"])
            mb_render()

        mb_holder = {}

        def mb_refresh():
            if not mail.COM_AVAILABLE:
                messagebox.showinfo(APP_TITLE, "Outlook COM not available "
                                    "on this machine.", parent=win)
                return
            mb_status.set("Reading Outlook folders…")
            win.update_idletasks()

            def worker():
                # COM on a worker thread, never the Tk thread
                try:
                    mb_holder["r"] = mail.list_mail_folders()
                except Exception as e:
                    mb_holder["e"] = str(e)
            threading.Thread(target=worker, daemon=True).start()

            def poll():
                if "e" in mb_holder:
                    mb_status.set("Folder read failed: %s"
                                  % mb_holder.pop("e"))
                    return
                if "r" not in mb_holder:
                    win.after(200, poll)
                    return
                found = mb_holder.pop("r")
                folder_cache[:] = found
                known = {f["path"] for f in folder_cache}
                # drop selections whose folder no longer exists
                for gone in list(selected - known):
                    selected.discard(gone)
                if not selected:
                    mb_inboxes_only()
                mb_render(0)
            win.after(200, poll)

        mbside = tk.Frame(fmb, bg=_BG)
        mbside.pack(side="left", fill="y", padx=(8, 0))
        ReviewWindow._btn(mbside, "\u21bb Refresh list", mb_refresh,
                          accent=True).pack(fill="x", pady=2)
        ReviewWindow._btn(mbside, "Toggle (Space)", mb_toggle).pack(
            fill="x", pady=(12, 2))
        ReviewWindow._btn(mbside, "Inboxes only", mb_inboxes_only).pack(
            fill="x", pady=2)
        ReviewWindow._btn(mbside, "Select all", mb_all).pack(fill="x", pady=2)
        ReviewWindow._btn(mbside, "Select none", mb_none).pack(
            fill="x", pady=2)

        mb_list.bind("<space>", mb_toggle)
        mb_list.bind("<Double-1>", mb_toggle)
        mb_list.bind("<<ListboxSelect>>", mb_show_path)

        mbrow = tk.Frame(t_mb, bg=_BG)
        mbrow.pack(fill="x", padx=12, pady=(0, 6))
        tk.Label(mbrow, text="Max emails per folder:", bg=_BG, fg=_FG,
                 font=(_FONT, _FONT_SZ)).pack(side="left")
        perfolder_var = tk.StringVar(
            value=str(self.settings.get("scan_max_per_folder", 100)))
        _entry(mbrow, perfolder_var, 6).pack(side="left", padx=6)
        tk.Label(mbrow, text="applies to each folder separately, so a busy "
                 "mailbox can't crowd out the others", bg=_BG, fg=_FG_DIM,
                 font=(_FONT, _FONT_SZ)).pack(side="left", padx=6)

        tk.Label(t_mb, textvariable=mb_status, bg=_BG, fg=_FG_DIM,
                 font=(_FONT, _FONT_SZ), wraplength=580,
                 justify="left").pack(anchor="w", padx=12, pady=(0, 8))
        mb_render()

        # ---------------- Tab 5: Visual (toolbar) -------------------------
        t4 = tk.Frame(nb, bg=_BG); nb.add(t4, text="Visual")
        f3 = ttk.LabelFrame(t4, text="Toolbar buttons", padding=8)
        f3.pack(fill="both", expand=True, padx=10, pady=10)
        layout = [dict(e) for e in normalize_toolbar_layout(
            self.settings.get("toolbar_layout"))]
        labels = {b[0]: b[1] for b in TOOLBAR_BUTTONS}

        lb = tk.Listbox(f3, height=7, bg=_ENTRY_BG, fg=_FG,
                        selectbackground=_SEL_BG, selectforeground=_SEL_FG,
                        relief="flat", font=(_FONT, _FONT_SZ),
                        exportselection=False, activestyle="none")
        lb.pack(side="left", fill="both", expand=True, pady=2)

        def render_layout(keep_index=None):
            lb.delete(0, "end")
            for e in layout:
                name = labels[e["id"]]
                lb.insert("end", name if e["visible"]
                          else "%s   (hidden)" % name)
                if not e["visible"]:
                    lb.itemconfig("end", fg=_FG_DIM)
            if keep_index is not None and 0 <= keep_index < len(layout):
                lb.selection_set(keep_index)
                lb.see(keep_index)

        def _sel():
            s = lb.curselection()
            return s[0] if s else None

        def move(delta):
            i = _sel()
            if i is None:
                return
            j = i + delta
            if 0 <= j < len(layout):
                layout[i], layout[j] = layout[j], layout[i]
                render_layout(j)

        def toggle_visible():
            i = _sel()
            if i is None:
                return
            e = layout[i]
            if e["id"] in TOOLBAR_ALWAYS_VISIBLE and e["visible"]:
                messagebox.showinfo(
                    APP_TITLE, "The Settings button can't be hidden — "
                    "you'd have no way back in.", parent=win)
                return
            e["visible"] = not e["visible"]
            render_layout(i)

        side = tk.Frame(f3, bg=_BG)
        side.pack(side="left", fill="y", padx=(8, 0))
        ReviewWindow._btn(side, "Move Up",
                          lambda: move(-1)).pack(fill="x", pady=2)
        ReviewWindow._btn(side, "Move Down",
                          lambda: move(+1)).pack(fill="x", pady=2)
        ReviewWindow._btn(side, "Hide / Show",
                          toggle_visible).pack(fill="x", pady=(10, 2))
        render_layout(0)

        # ---------------- shared Save / Cancel ----------------------------
        def save():
            try:
                delay = max(5, int(delay_var.get().strip()))
                conf = min(1.0, max(0.0, float(conf_var.get().strip())))
            except ValueError:
                messagebox.showerror(APP_TITLE,
                                     "Delay must be an integer and "
                                     "confidence a number.", parent=win)
                return
            self.settings["auto_send_master"] = master_var.get()
            self.settings["auto_send_delay_sec"] = delay
            self.settings["auto_send_min_conf"] = conf
            self.settings["use_llm_polish"] = polish_var.get()
            self.settings["use_outlook_signature"] = olsig_var.get()
            self.settings["signature"] = sig_txt.get("1.0", "end").strip()
            self.settings["toolbar_layout"] = normalize_toolbar_layout(layout)
            # v1.9.0: mailbox selection
            self.settings["outlook_folders"] = [dict(f) for f in folder_cache]
            self.settings["scan_folders"] = sorted(selected)
            self.settings["mb_hide_empty"] = hide_empty_var.get()
            self.settings["mb_show_system"] = show_system_var.get()
            try:
                self.settings["scan_max_per_folder"] = max(
                    1, int(perfolder_var.get().strip()))
            except ValueError:
                pass
            # v1.5.0: persist + apply AI settings live
            self.settings.update(collect_ai())
            clf.apply_ai_settings(self.settings)
            drafts.save_settings(self.store.dir, self.settings)
            self._build_toolbar()
            if not master_var.get():
                n = self.auto.cancel_all()
                if n:
                    self._set_status(
                        "Auto-send off — %d scheduled send(s) cancelled." % n)
            self._set_status("Settings saved.")
            self._save_geometry_for("settings", win)
            win.destroy()

        btn_row = tk.Frame(win, bg=_BG, pady=8)
        btn_row.pack(fill="x", padx=10)
        ReviewWindow._btn(btn_row, "Save", save, accent=True).pack(
            side="left", padx=4)
        ReviewWindow._btn(btn_row, "Cancel", win.destroy).pack(
            side="left", padx=4)

    # ------------------------------------------------------------------ stats
    def show_stats(self):
        stats = self.store.category_stats()
        win = tk.Toplevel(self.root)
        win.title("Graduation Status — %s" % APP_TITLE)
        win.configure(bg=_BG)
        apply_theme(win)
        self._bind_geometry(win, "stats", "700x340")
        cols = ("cat", "n", "agree", "grad", "auto")
        tree = ttk.Treeview(win, columns=cols, show="headings",
                            selectmode="browse")
        for c, h, w in (("cat", "Category", 210), ("n", "Samples", 80),
                        ("agree", "Agreement", 90),
                        ("grad", "Graduated", 90),
                        ("auto", "Auto-send", 90)):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor="w")
        for cat in clf.CATEGORIES:
            s = stats.get(cat, {"samples": 0, "agreement": 0.0,
                                 "graduated": False, "auto_send": False})
            tree.insert("", "end", iid=cat, values=(
                CATEGORY_LABELS.get(cat, cat), s["samples"],
                "%.1f%%" % (100 * s["agreement"]),
                "yes" if s["graduated"] else "no",
                "ON" if s["auto_send"] else "off"))
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        note = ("Graduation: >= %d decided samples and >= %.0f%% unchanged. "
                "Double-click a row to toggle a manual auto-send override."
                % (rec.GRADUATION_MIN_SAMPLES,
                   100 * rec.GRADUATION_MIN_AGREEMENT))
        tk.Label(win, text=note, wraplength=640, bg=_BG, fg=_FG_DIM,
                 font=(_FONT, _FONT_SZ), justify="left",
                 pady=4, padx=8).pack(fill="x")

        def toggle(_e):
            s = tree.selection()
            if not s:
                return
            cat = s[0]
            cur = self.store.auto_send_enabled(cat)
            if not cur and not messagebox.askyesno(
                    APP_TITLE,
                    "Enable AUTO-SEND for '%s'?\n"
                    "Accepted drafts in this category will be sent "
                    "without review." % cat, parent=win):
                return
            self.store.set_auto_send_override(cat, not cur)
            win.destroy()
            self.show_stats()
        tree.bind("<Double-1>", toggle)

    def export_corpus(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".jsonl",
            initialfile="replyit_corpus.jsonl",
            filetypes=[("JSON Lines", "*.jsonl")])
        if not path:
            return
        n = self.store.export_training_jsonl(path)
        self._set_status(
            "Exported %d decided records to %s" % (n, path))

    # ------------------------------------------------------------------- send
    def send_reply_async(self, message_id, sender, subject, body_text):
        # v1.8.0: when Outlook supplies the signature, strip the app's
        # plain-text one first or the reply carries both
        use_ol_sig = bool(self.settings.get("use_outlook_signature", True))
        if use_ol_sig and mail.COM_AVAILABLE:
            body_text = drafts.strip_configured_signature(
                body_text, self.settings.get("signature", ""))

        def worker():
            if mail.COM_AVAILABLE:
                ok, detail = mail.send_outlook_reply(
                    message_id, body_text,
                    use_outlook_signature=use_ol_sig)
                if not ok:
                    p = mail.write_eml_draft(
                        os.path.join(self.store.dir, "outbox"),
                        sender, subject, body_text,
                        in_reply_to=message_id)
                    detail = "%s — draft saved to %s" % (detail, p)
            else:
                p = mail.write_eml_draft(
                    os.path.join(self.store.dir, "outbox"),
                    sender, subject, body_text,
                    in_reply_to=message_id)
                ok, detail = True, "no COM — draft saved to %s" % p
            self.ui_queue.put(("send_done", ok, detail))
        threading.Thread(target=worker, daemon=True).start()


class ReviewWindow:
    """Original email + AI decision side by side — fully in-app, zero COM."""

    def __init__(self, app, row, from_no_reply=False,
                 from_deleted=False, read_only=False):
        self.app = app
        self.row = row
        self.read_only = read_only or from_deleted
        self.from_deleted = from_deleted

        win = tk.Toplevel(app.root)
        win.title(row.get("subject") or "(no subject)")
        win.configure(bg=_BG)
        self.win = win
        apply_theme(win)
        app._bind_geometry(win, "review", "940x660")

        hdr = tk.Frame(win, bg=_BG2, padx=10, pady=6)
        hdr.pack(fill="x")
        tk.Label(hdr,
                 text="From: %s    Received: %s"
                 % (row.get("sender", ""),
                    (row.get("received_at") or "")[:16].replace("T", " ")),
                 bg=_BG2, fg=_FG, font=(_FONT, _FONT_SZ)).pack(anchor="w")
        tk.Label(hdr,
                 text="AI: %s  (%.0f%% conf, %s)"
                 % (CATEGORY_LABELS.get(row.get("ai_category"),
                                        row.get("ai_category")),
                    100 * (row.get("ai_confidence") or 0),
                    row.get("ai_source") or ""),
                 bg=_BG2, fg=_ACCENT, font=(_FONT, _FONT_SZ)).pack(anchor="w")

        panes = ttk.PanedWindow(win, orient="vertical")
        panes.pack(fill="both", expand=True, padx=8, pady=4)

        f1 = ttk.LabelFrame(panes, text="Original email")
        self.body_txt = tk.Text(f1, wrap="word", height=12,
                                bg=_ENTRY_BG, fg=_FG,
                                insertbackground=_ACCENT,
                                selectbackground=_SEL_BG,
                                font=(_FONT, _FONT_SZ), relief="flat")
        self.body_txt.insert("1.0", row.get("body_full") or
                             row.get("body_preview") or "")
        self.body_txt.configure(state="disabled")
        self.body_txt.pack(fill="both", expand=True, padx=4, pady=4)
        panes.add(f1, weight=3)

        f2 = ttk.LabelFrame(panes, text="AI response draft (editable)")
        self.draft_txt = tk.Text(f2, wrap="word", height=10,
                                 bg=_ENTRY_BG, fg=_FG,
                                 insertbackground=_ACCENT,
                                 selectbackground=_SEL_BG,
                                 font=(_FONT, _FONT_SZ), relief="flat")
        self.draft_txt.insert("1.0", row.get("final_draft")
                              or row.get("ai_draft") or "")
        if self.read_only:
            self.draft_txt.configure(state="disabled")
        self.draft_txt.pack(fill="both", expand=True, padx=4, pady=4)
        panes.add(f2, weight=2)

        if self.read_only:
            btn_row = tk.Frame(win, bg=_BG, pady=6)
            btn_row.pack(fill="x", padx=8)
            if from_deleted:
                self._btn(btn_row, "Undo Delete — restore to queue",
                          self.undo_delete, accent=True).pack(
                          side="left", padx=4)
            self._btn(btn_row, "Close", win.destroy).pack(
                side="left", padx=4)
            return

        catf = ttk.LabelFrame(win, text="Response type", padding=6)
        catf.pack(fill="x", padx=8)
        self.cat_var = tk.StringVar(value=row.get("ai_category") or "")

        # v1.11.0: the quote family lives in a dropdown. As a flat radio row
        # it had already overflowed the window — the last option was cut off
        # — and it grows every time a quote response type is added.
        qrow = tk.Frame(catf, bg=_BG)
        qrow.pack(fill="x", pady=(0, 4))
        tk.Label(qrow, text="Quote:", bg=_BG, fg=_FG,
                 font=(_FONT, _FONT_SZ)).pack(side="left", padx=(0, 6))
        self._quote_labels = [CATEGORY_LABELS.get(c, c)
                              for c in clf.QUOTE_CATEGORIES]
        self._quote_by_label = dict(zip(self._quote_labels,
                                        clf.QUOTE_CATEGORIES))
        self.quote_cb = ttk.Combobox(qrow, width=34, state="readonly",
                                     values=tuple(self._quote_labels))
        self.quote_cb.pack(side="left")

        def on_quote_pick(_e=None):
            lbl = self.quote_cb.get()
            cat = self._quote_by_label.get(lbl)
            if cat:
                self.cat_var.set(cat)
                self._on_category_change()
        self.quote_cb.bind("<<ComboboxSelected>>", on_quote_pick)

        orow = tk.Frame(catf, bg=_BG)
        orow.pack(fill="x")
        for cat in clf.REPLY_CATEGORIES:
            if cat in clf.QUOTE_CATEGORIES:
                continue
            ttk.Radiobutton(orow, text=CATEGORY_LABELS[cat], value=cat,
                            variable=self.cat_var,
                            command=self._on_category_change
                            ).pack(side="left", padx=3)
        ttk.Radiobutton(orow, text=CATEGORY_LABELS[clf.CAT_NO_REPLY],
                        value=clf.CAT_NO_REPLY, variable=self.cat_var,
                        command=self._on_category_change
                        ).pack(side="left", padx=3)

        def sync_quote_box(*_a):
            """Keep the dropdown showing the current pick, and blank it when
            a non-quote radio is chosen so only one control looks active."""
            cur = self.cat_var.get()
            if cur in clf.QUOTE_CATEGORIES:
                self.quote_cb.set(CATEGORY_LABELS.get(cur, cur))
            else:
                self.quote_cb.set("")
        self.cat_var.trace_add("write", sync_quote_box)
        sync_quote_box()

        # v1.11.0: Enter accepts. Guarded so it still inserts a newline
        # while the caret is in the draft or body text.
        def on_enter(event):
            if isinstance(event.widget, tk.Text):
                return None
            self.accept()
            return "break"
        win.bind("<Return>", on_enter)
        win.bind("<KP_Enter>", on_enter)
        win.bind("<Escape>", lambda _e: win.destroy())

        btns = tk.Frame(win, bg=_BG, pady=6)
        btns.pack(fill="x", padx=8)
        self._btn(btns, "Accept",
                  self.accept, accent=True).pack(side="left", padx=3)
        self._btn(btns, "Accept & Send",
                  self.accept_send).pack(side="left", padx=3)
        self._btn(btns, "Decline (no reply sent)",
                  self.decline).pack(side="left", padx=3)
        if from_no_reply:
            self._btn(btns, "Undo — needs a reply",
                      self.undo_no_reply).pack(side="left", padx=14)
        else:
            self._btn(btns, "Move to No Reply",
                      self.move_no_reply).pack(side="left", padx=14)

    @staticmethod
    def _btn(parent, text, cmd, accent=False):
        bg = _ACCENT if accent else _BG3
        fg = _SEL_FG if accent else _FG
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=fg,
                         activebackground=_ACCENT,
                         activeforeground=_SEL_FG,
                         relief="flat", font=(_FONT, _FONT_SZ),
                         padx=8, pady=3, bd=0)

    # ------------------------------------------------------------- callbacks
    def _on_category_change(self):
        cat = self.cat_var.get()
        if cat == clf.CAT_NO_REPLY:
            self.draft_txt.delete("1.0", "end")
            return
        name = self.row.get("sender", "").split("@")[0]
        text, _src = drafts.make_draft(
            cat, name, self.row.get("sender", ""),
            self.row.get("subject", ""),
            self.row.get("body_full") or "",
            self.app.settings,
            voice_examples=self.app._voice_for(cat))
        self.draft_txt.delete("1.0", "end")
        self.draft_txt.insert("1.0", text)

    def _final_state(self):
        cat = self.cat_var.get()
        draft = self.draft_txt.get("1.0", "end").strip()
        ai_cat = self.row.get("ai_category")
        ai_draft = (self.row.get("ai_draft") or "").strip()
        if cat != ai_cat:
            action = rec.ACTION_RECATEGORIZED
        elif draft != ai_draft:
            action = rec.ACTION_EDITED
        else:
            action = rec.ACTION_ACCEPTED
        return action, cat, draft

    def accept(self):
        action, cat, draft = self._final_state()
        if cat == clf.CAT_NO_REPLY:
            self.move_no_reply()
            return
        self.app.store.record_decision(
            self.row["message_id"], action, cat, draft)
        self._close("Recorded: %s" % action)

    def accept_send(self):
        action, cat, draft = self._final_state()
        if cat == clf.CAT_NO_REPLY or not draft:
            messagebox.showinfo(APP_TITLE, "Nothing to send.",
                                parent=self.win)
            return
        self.app.store.record_decision(
            self.row["message_id"], action, cat, draft)
        self.app.send_reply_async(
            self.row["message_id"],
            self.row.get("sender", ""),
            self.row.get("subject", ""),
            draft)
        self._close("Recorded + sending…")

    def decline(self):
        self.app.store.record_decision(
            self.row["message_id"], rec.ACTION_DECLINED)
        self._close("Declined — no reply will be sent.")

    def move_no_reply(self):
        self.app.store.record_decision(
            self.row["message_id"],
            rec.ACTION_MOVED_NO_REPLY, clf.CAT_NO_REPLY, "")
        self._close("Moved to No Reply.")

    def undo_no_reply(self):
        name = self.row.get("sender", "").split("@")[0]
        text, _src = drafts.make_draft(
            clf.CAT_QUOTE_ACK, name,
            self.row.get("sender", ""),
            self.row.get("subject", ""),
            self.row.get("body_full") or "",
            self.app.settings,
            voice_examples=self.app._voice_for(clf.CAT_QUOTE_ACK))
        self.app.store.reopen(
            self.row["message_id"],
            new_ai_category=clf.CAT_QUOTE_ACK,
            new_ai_draft=text, new_needs_reply=True,
            ai_source="user_undo")
        self.app.store._audit(
            "undo_no_reply", {"message_id": self.row["message_id"]})
        self._close("Moved back to the Auto-Reply Queue.")

    def undo_delete(self):
        self.app.store.reopen(self.row["message_id"])
        self.app.store._audit(
            "undo_delete", {"message_id": self.row["message_id"]})
        self._close("Restored to queue.")

    def _close(self, status):
        # button closes bypass WM_DELETE_WINDOW, so persist geometry here too
        self.app._save_geometry_for("review", self.win)
        self.app._set_status(status)
        self.app._refresh_lists()
        self.win.destroy()


def main():
    root = tk.Tk()
    ReplyPilotApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
