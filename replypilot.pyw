# replypilot.pyw
# Replyit v1.3.0 — standalone email auto-reply trainer
#
# v1.0.0: Initial release. v1.1.0: MaINbox theme, multi-select, Deleted
# bucket. v1.2.0: acknowledgement category, AI Review, checkboxes,
# auto-send engine + settings.
# v1.3.0 changelog:
#   - Manifest-driven toolbar: buttons can be hidden and re-arranged from
#     Settings -> Visual settings (Up/Down/Show-Hide). Layout persists in
#     settings.json; Settings button itself can never be hidden. Unknown
#     saved ids are dropped, new buttons auto-append (future-proof).
#   - Clicking the ✓ column HEADING selects all / none on the queue tab.
#   - AI Review actually usable: fast Ollama reachability preflight (3s)
#     instead of silently eating a 30s timeout per email; live per-email
#     progress in the status bar; polish output is repaired (fences/
#     preamble stripped, signature re-appended) instead of discarded on
#     any deviation; failure reasons reported in the status line.

APP_TITLE = "Replyit v1.3.0"

import os
import threading
import queue as _queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import replypilot_record_engine as rec
import replypilot_classify_engine as clf
import replypilot_draft_engine as drafts
import replypilot_mail_engine as mail
import replypilot_auto_engine as auto

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
    clf.CAT_NO_QUOTE:  "No quote",
    clf.CAT_NEED_INFO: "Need more information",
    clf.CAT_JOB_NAME:  "Ask for job name",
    clf.CAT_ACK:       "Acknowledgement (thank you)",
    clf.CAT_ESCALATE:  "Escalate (handle personally)",
    clf.CAT_NO_REPLY:  "No reply needed",
}

# Tab indices
_TAB_QUEUE   = 0
_TAB_NOREPLY = 1
_TAB_DELETED = 2
_TAB_DECIDED = 3

# v1.3.0: toolbar manifest — single source of truth for every button.
# (id, label, app method name). The saved layout in settings.json is a list
# of {"id":..., "visible":...} in display order.
TOOLBAR_BUTTONS = (
    ("import_files",  "Import .eml Files",   "import_files"),
    ("import_folder", "Import Folder",       "import_folder"),
    ("scan_outlook",  "Scan Outlook Inbox",  "scan_outlook"),
    ("ai_review",     "AI Review",           "ai_review"),
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


class ReplyPilotApp:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("1060x660")
        apply_theme(root)

        self.store = rec.RecordStore()
        self.settings = drafts.load_settings(self.store.dir)
        self.ui_queue = _queue.Queue()
        self.busy = False
        self.checked = set()   # v1.2.0: message_ids checked in queue tab
        self.auto = auto.AutoSendEngine(self.store, self.settings)

        self._build_ui()
        self._refresh_lists()
        self.root.after(150, self._drain_ui_queue)

    # ------------------------------------------------------------------- UI
    def _build_ui(self):
        self.toolbar = tk.Frame(self.root, bg=_BG2, pady=4)
        self.toolbar.pack(fill="x")
        self._build_toolbar()

        self.status_var = tk.StringVar(value="Ready. Data dir: %s"
                                       % self.store.dir)
        tk.Label(self.root, textvariable=self.status_var, anchor="w",
                 bg=_BG2, fg=_FG_DIM, font=(_FONT, _FONT_SZ),
                 pady=3, padx=8).pack(fill="x", side="bottom")

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=6, pady=(4, 0))

        self.tree_queue   = self._make_tree(checks=True)
        self.tree_noreply = self._make_tree()
        self.tree_deleted = self._make_tree(deleted=True)
        self.tree_done    = self._make_tree(done=True)

        self.nb.add(self.tree_queue.master,   text="Auto-Reply Queue (0)")
        self.nb.add(self.tree_noreply.master, text="No Reply (0)")
        self.nb.add(self.tree_deleted.master, text="Deleted (0)")
        self.nb.add(self.tree_done.master,    text="Decided (0)")

        # double-click → review
        self.tree_queue.bind("<Double-1>",
            lambda e: self._open_review(self.tree_queue))
        self.tree_noreply.bind("<Double-1>",
            lambda e: self._open_review(self.tree_noreply, from_no_reply=True))
        self.tree_deleted.bind("<Double-1>",
            lambda e: self._open_review(self.tree_deleted, from_deleted=True))
        self.tree_done.bind("<Double-1>",
            lambda e: self._open_review(self.tree_done, read_only=True))

        # Delete key on queue / no-reply / decided → soft-delete selection
        for tree in (self.tree_queue, self.tree_noreply, self.tree_done):
            tree.bind("<Delete>", lambda e, t=tree: self._delete_selection(t))
        # Delete on deleted tab → permanent hard-delete (with confirm)
        self.tree_deleted.bind("<Delete>",
            lambda e: self._purge_selection(self.tree_deleted))
        # v1.2.0: Space toggles the checkbox on the selected queue row(s);
        # arrow keys already move the selection natively. "break" stops the
        # default Space behavior (which would re-toggle selection).
        self.tree_queue.bind("<space>",
            lambda e: (self._toggle_checks(), "break")[1])
        # click on the checkbox cell also toggles
        self.tree_queue.bind("<Button-1>", self._on_queue_click)

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

    def _make_tree(self, done=False, deleted=False, checks=False):
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
        tree = ttk.Treeview(frame, columns=cols, show="headings",
                            selectmode="extended")   # ← multi-select
        widths = {"chk": 34, "received": 140, "sender": 185, "subject": 300,
                  "category": 155, "conf": 50, "action": 110,
                  "deleted_at": 130}
        for c, h in zip(cols, heads):
            tree.heading(c, text=h)
            tree.column(c, width=widths.get(c, 100),
                        anchor=("center" if c == "chk" else "w"),
                        stretch=(c == "subject"))
        if checks:
            # v1.3.0: clicking the ✓ heading toggles select all / none
            tree.heading("chk", text="✓",
                         command=self._toggle_all_checks)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        return tree

    # ------------------------------------------------------------- refreshing
    def _refresh_lists(self):
        pending  = self.store.pending()
        q_rows   = [r for r in pending if r["ai_needs_reply"]]
        nr_rows  = [r for r in pending if not r["ai_needs_reply"]]
        nr_rows += self.store.by_action(rec.ACTION_MOVED_NO_REPLY)
        del_rows = self.store.by_action(rec.ACTION_DELETED)
        done = []
        for a in (rec.ACTION_ACCEPTED, rec.ACTION_RECATEGORIZED,
                  rec.ACTION_EDITED, rec.ACTION_DECLINED,
                  rec.ACTION_AUTO_SENT):
            done.extend(self.store.by_action(a))
        done.sort(key=lambda r: r.get("decided_at") or "", reverse=True)

        self._fill(self.tree_queue,   q_rows, checks=True)
        self._fill(self.tree_noreply, nr_rows)
        self._fill(self.tree_deleted, del_rows, deleted=True)
        self._fill(self.tree_done,    done,     done=True)
        self.nb.tab(_TAB_QUEUE,   text="Auto-Reply Queue (%d)" % len(q_rows))
        self.nb.tab(_TAB_NOREPLY, text="No Reply (%d)" % len(nr_rows))
        self.nb.tab(_TAB_DELETED, text="Deleted (%d)" % len(del_rows))
        self.nb.tab(_TAB_DECIDED, text="Decided (%d)" % len(done))

    def _fill(self, tree, rows, done=False, deleted=False, checks=False):
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
            tree.insert("", "end", iid=r["message_id"], values=vals)
        if checks:
            # prune checked ids that fell off the queue
            live = set(tree.get_children())
            self.checked &= live

    # -------------------------------------------------------- checkbox logic
    def _toggle_all_checks(self):
        """v1.3.0: ✓ heading click — if every visible row is checked,
        uncheck all; otherwise check all."""
        rows = list(self.tree_queue.get_children())
        if not rows:
            return
        if all(mid in self.checked for mid in rows):
            for mid in rows:
                self.checked.discard(mid)
                self.tree_queue.set(mid, "chk", "☐")
            self._set_status("All unchecked.")
        else:
            for mid in rows:
                self.checked.add(mid)
                self.tree_queue.set(mid, "chk", "☑")
            self._set_status("All %d checked." % len(rows))

    def _toggle_checks(self):
        """Space: flip the checkbox on every selected queue row."""
        sel = self.tree_queue.selection()
        for mid in sel:
            if mid in self.checked:
                self.checked.discard(mid)
                self.tree_queue.set(mid, "chk", "☐")
            else:
                self.checked.add(mid)
                self.tree_queue.set(mid, "chk", "☑")

    def _on_queue_click(self, event):
        """Click directly on the ✓ column toggles that row's checkbox."""
        if self.tree_queue.identify_column(event.x) != "#1":
            return
        mid = self.tree_queue.identify_row(event.y)
        if not mid:
            return
        if mid in self.checked:
            self.checked.discard(mid)
            self.tree_queue.set(mid, "chk", "☐")
        else:
            self.checked.add(mid)
            self.tree_queue.set(mid, "chk", "☑")
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
        self._ingest_async(
            lambda: mail.scan_outlook_inbox(max_items=100))

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
                            self.settings)
                    inserted = self.store.upsert_intake(
                        mid, it.get("received_at", ""),
                        it.get("subject", ""), it.get("sender", ""),
                        res["features"], res["needs_reply"],
                        res["category"], res["confidence"],
                        draft_text,
                        "%s/%s" % (res["source"], dsrc),
                        it.get("body", ""))
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
                        self._set_status(
                            "Scan complete: %d new, %d already known."
                            % (new_count, seen_count))
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
                elif msg[0] == "ai_review_progress":
                    _, i, n, subject = msg
                    self._set_status(
                        "AI Review %d/%d: %s" % (i, n, subject[:60]))
                elif msg[0] == "ai_review_done":
                    _, improved, total, err = msg
                    self.busy = False
                    if err:
                        self._set_status("AI Review error: %s" % err)
                    else:
                        self._set_status(
                            "AI Review: %d of %d draft(s) tailored."
                            % (improved, total))
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

    # -------------------------------------------------------------- AI review
    def ai_review(self):
        """v1.3.0: LLM pass over the checked queue emails (falls back to the
        current selection). Preflights Ollama reachability (3s) so failure
        is loud and immediate; reports per-email progress and, if drafts
        weren't changed, the reasons why. Worker thread — network never on
        the Tk thread. Only pending items are touched."""
        # tree order, checked first choice, selection fallback
        mids = [m for m in self.tree_queue.get_children()
                if m in self.checked] or list(self.tree_queue.selection())
        if not mids:
            messagebox.showinfo(
                APP_TITLE,
                "Check some emails first (Space or the ✓ column toggles, "
                "✓ heading = all/none), or select rows in the queue.")
            return
        if self.busy:
            messagebox.showinfo(APP_TITLE, "A task is already running.")
            return
        if clf.NO_LLM:
            messagebox.showinfo(
                APP_TITLE, "AI Review needs the LLM "
                "(REPLYPILOT_NO_LLM=1 is set).")
            return
        self.busy = True
        self._set_status("AI Review: checking Ollama at %s:%d…"
                         % (clf.OLLAMA_HOST, clf.OLLAMA_PORT))
        rows = [self.store.get(m) for m in mids]
        settings = dict(self.settings)

        def worker():
            if not drafts.ollama_reachable():
                self.ui_queue.put((
                    "ai_review_done", 0, len(rows),
                    "Ollama unreachable at %s:%d — is tillium-bridge up "
                    "on Tailscale?" % (clf.OLLAMA_HOST, clf.OLLAMA_PORT)))
                return
            improved, reasons, err = 0, {}, None
            try:
                for i, row in enumerate(rows, 1):
                    if row is None:
                        reasons["gone"] = reasons.get("gone", 0) + 1
                        continue
                    self.ui_queue.put((
                        "ai_review_progress", i, len(rows),
                        row.get("subject") or "(no subject)"))
                    if not (row.get("ai_draft") or "").strip():
                        reasons["no_draft"] = reasons.get("no_draft", 0) + 1
                        continue
                    polished, reason = drafts.polish_draft(
                        row["ai_draft"], row.get("subject", ""),
                        row.get("body_full") or "", settings=settings)
                    if polished and self.store.update_ai_draft(
                            row["message_id"], polished):
                        improved += 1
                    else:
                        reasons[reason] = reasons.get(reason, 0) + 1
            except Exception as e:
                err = str(e)
            if err is None and improved == 0 and reasons:
                err = "no drafts changed: " + ", ".join(
                    "%s x%d" % (k, v) for k, v in sorted(reasons.items()))
            self.ui_queue.put(("ai_review_done", improved, len(rows), err))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------ settings UI
    def open_settings(self):
        """v1.2.0: auto-send + draft settings. v1.3.0: visual settings —
        hide/unhide and re-arrange toolbar buttons."""
        win = tk.Toplevel(self.root)
        win.title("Settings — %s" % APP_TITLE)
        win.configure(bg=_BG)
        win.geometry("560x680")
        apply_theme(win)

        f = ttk.LabelFrame(win, text="Auto-send engine", padding=8)
        f.pack(fill="x", padx=10, pady=(10, 4))
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
        tk.Entry(row1, textvariable=delay_var, width=6, bg=_ENTRY_BG,
                 fg=_FG, insertbackground=_ACCENT,
                 relief="flat").pack(side="left", padx=6)
        row2 = tk.Frame(f, bg=_BG); row2.pack(fill="x", pady=(6, 0))
        tk.Label(row2, text="Minimum AI confidence (0.0–1.0):", bg=_BG,
                 fg=_FG, font=(_FONT, _FONT_SZ)).pack(side="left")
        conf_var = tk.StringVar(
            value=str(self.settings.get("auto_send_min_conf", 0.85)))
        tk.Entry(row2, textvariable=conf_var, width=6, bg=_ENTRY_BG,
                 fg=_FG, insertbackground=_ACCENT,
                 relief="flat").pack(side="left", padx=6)
        tk.Label(f, text="Escalate and No-Reply are never auto-sent, "
                 "regardless of settings.", bg=_BG, fg=_FG_DIM,
                 font=(_FONT, _FONT_SZ)).pack(anchor="w", pady=(6, 0))

        f2 = ttk.LabelFrame(win, text="Drafting", padding=8)
        f2.pack(fill="both", expand=True, padx=10, pady=4)
        polish_var = tk.BooleanVar(
            value=bool(self.settings.get("use_llm_polish", False)))
        ttk.Checkbutton(
            f2, text="LLM-polish drafts automatically at scan time "
            "(AI Review button works either way)",
            variable=polish_var).pack(anchor="w")
        tk.Label(f2, text="Signature:", bg=_BG, fg=_FG,
                 font=(_FONT, _FONT_SZ)).pack(anchor="w", pady=(6, 0))
        sig_txt = tk.Text(f2, height=4, bg=_ENTRY_BG, fg=_FG,
                          insertbackground=_ACCENT, relief="flat",
                          font=(_FONT, _FONT_SZ))
        sig_txt.insert("1.0", self.settings.get("signature", ""))
        sig_txt.pack(fill="both", expand=True, pady=2)

        # ---------------- v1.3.0: Visual settings — toolbar layout --------
        f3 = ttk.LabelFrame(win, text="Visual settings — toolbar buttons",
                            padding=8)
        f3.pack(fill="both", expand=True, padx=10, pady=4)
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
            self.settings["signature"] = sig_txt.get("1.0", "end").strip()
            self.settings["toolbar_layout"] = normalize_toolbar_layout(layout)
            drafts.save_settings(self.store.dir, self.settings)
            self._build_toolbar()   # v1.3.0: apply visual changes live
            if not master_var.get():
                n = self.auto.cancel_all()
                if n:
                    self._set_status(
                        "Auto-send off — %d scheduled send(s) cancelled." % n)
            self._set_status("Settings saved.")
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
        win.geometry("680x310")
        apply_theme(win)
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
        def worker():
            if mail.COM_AVAILABLE:
                ok, detail = mail.send_outlook_reply(message_id, body_text)
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
        win.geometry("940x660")
        self.win = win
        apply_theme(win)

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

        catf = ttk.LabelFrame(win, text="Response type", padding=4)
        catf.pack(fill="x", padx=8)
        self.cat_var = tk.StringVar(value=row.get("ai_category") or "")
        for cat in clf.REPLY_CATEGORIES + (clf.CAT_NO_REPLY,):
            ttk.Radiobutton(catf,
                            text=CATEGORY_LABELS[cat],
                            value=cat,
                            variable=self.cat_var,
                            command=self._on_category_change
                            ).pack(side="left", padx=3)

        btns = tk.Frame(win, bg=_BG, pady=6)
        btns.pack(fill="x", padx=8)
        self._btn(btns, "Accept",
                  self.accept, accent=True).pack(side="left", padx=3)
        self._btn(btns, "Accept && Send",
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
            self.app.settings)
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
            self.app.settings)
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
        self.app._set_status(status)
        self.app._refresh_lists()
        self.win.destroy()


def main():
    root = tk.Tk()
    ReplyPilotApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
