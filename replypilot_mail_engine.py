# replypilot_mail_engine.py
# ReplyPilot Mail Engine v1.0.0
# Ingestion: .eml files (stdlib email parser) and optional Outlook COM scan.
# Sending: Outlook COM reply (found by Internet Message-ID, never EntryID),
# or .eml draft written to disk when COM is unavailable.
#
# COM rules (same discipline as MaINbox):
#   - never on the Tk main thread; callers run these on worker threads
#   - CoInitialize / CoUninitialize bracketed per thread (thread_init/uninit)
#   - fresh Dispatch per worker session

ENGINE_VERSION = "1.3.0"  # v1.3.0: system-folder marking
                          # v1.2.0: multi-folder enumeration + scanning
                          # v1.1.0: HTMLBody send preserves Outlook signature+images

import os
import re
import glob
import email
import email.policy
import email.utils
from datetime import datetime, timezone

# ------------------------------------------------------------ guarded pywin32
try:
    import pythoncom            # type: ignore
    import win32com.client      # type: ignore
    COM_AVAILABLE = True
except Exception:
    pythoncom = None
    win32com = None
    COM_AVAILABLE = False

# DASL property for PR_INTERNET_MESSAGE_ID — the drift-proof key
PR_INTERNET_MESSAGE_ID = "http://schemas.microsoft.com/mapi/proptag/0x1035001F"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_BLANKS_RE = re.compile(r"\n{3,}")
_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_BR_RE = re.compile(r"<br\s*/?>|</p>|</div>|</tr>", re.I)


def html_to_text(html):
    if not html:
        return ""
    txt = _STYLE_RE.sub("", html)
    txt = _BR_RE.sub("\n", txt)
    txt = _TAG_RE.sub("", txt)
    txt = txt.replace("&nbsp;", " ").replace("&amp;", "&") \
             .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    txt = _WS_RE.sub(" ", txt)
    txt = _BLANKS_RE.sub("\n\n", txt)
    return txt.strip()


class MailItem(dict):
    """Plain dict subclass: message_id, subject, sender, sender_name,
    received_at (ISO), body, source ('eml'|'outlook'), source_path."""
    pass


# ------------------------------------------------------------------ .eml side

def parse_eml_bytes(raw, source_path=""):
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = str(msg.get("Subject", "") or "")
    frm = str(msg.get("From", "") or "")
    name, addr = email.utils.parseaddr(frm)
    mid = str(msg.get("Message-ID", "") or "").strip()
    date_hdr = msg.get("Date")
    try:
        dt = email.utils.parsedate_to_datetime(date_hdr) if date_hdr else None
    except Exception:
        dt = None
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    body_text, body_html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if part.get_content_disposition() == "attachment":
                continue
            try:
                content = part.get_content()
            except Exception:
                continue
            if ct == "text/plain" and not body_text:
                body_text = content
            elif ct == "text/html" and not body_html:
                body_html = content
    else:
        try:
            content = msg.get_content()
        except Exception:
            content = ""
        if msg.get_content_type() == "text/html":
            body_html = content
        else:
            body_text = content

    body = body_text.strip() if body_text and body_text.strip() \
        else html_to_text(body_html)

    return MailItem(
        message_id=mid,  # may be empty; caller fills fallback
        subject=subject,
        sender=addr or frm,
        sender_name=name or (addr.split("@")[0] if addr else ""),
        received_at=dt.isoformat(timespec="seconds"),
        body=body,
        source="eml",
        source_path=source_path,
    )


def parse_eml_file(path):
    with open(path, "rb") as f:
        return parse_eml_bytes(f.read(), source_path=path)


def scan_eml_folder(folder):
    items = []
    for path in sorted(glob.glob(os.path.join(folder, "*.eml"))):
        try:
            items.append(parse_eml_file(path))
        except Exception as e:
            items.append(MailItem(message_id="", subject="(parse error)",
                                  sender="", sender_name="",
                                  received_at=datetime.now(timezone.utc)
                                  .isoformat(timespec="seconds"),
                                  body="Parse error: %s" % e,
                                  source="eml", source_path=path))
    return items


def write_eml_draft(out_dir, to_addr, subject, body, in_reply_to=""):
    """COM-free sending fallback: write an RFC-822 draft the user can open."""
    os.makedirs(out_dir, exist_ok=True)
    msg = email.message.EmailMessage()
    msg["To"] = to_addr
    msg["Subject"] = subject if subject.lower().startswith("re:") \
        else "RE: " + subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", subject)[:60] or "reply"
    path = os.path.join(out_dir, "draft_%s_%s.eml"
                        % (safe, datetime.now().strftime("%Y%m%d_%H%M%S")))
    with open(path, "wb") as f:
        f.write(bytes(msg))
    return path


# --------------------------------------------------------------- Outlook side
# Everything below is Windows-only and must be called from a worker thread.

def outlook_thread_init():
    if COM_AVAILABLE:
        pythoncom.CoInitialize()


def outlook_thread_uninit():
    if COM_AVAILABLE:
        pythoncom.CoUninitialize()


def fresh_outlook():
    if not COM_AVAILABLE:
        raise RuntimeError("pywin32/Outlook COM not available on this system")
    return win32com.client.Dispatch("Outlook.Application")


def _item_message_id(item):
    try:
        pa = item.PropertyAccessor
        return (pa.GetProperty(PR_INTERNET_MESSAGE_ID) or "").strip()
    except Exception:
        return ""


OL_FOLDER_INBOX = 6      # olFolderInbox
OL_ITEM_MAIL = 0         # olMailItem

# Outlook creates a lot of internal plumbing that shows up as mail folders.
# None of it ever holds mail a person would triage, and on a real profile it
# buries the handful of folders that matter. Marked at enumeration so the UI
# can hide it while the data stays available.
SYSTEM_FOLDER_NAMES = {
    "yammer root", "webextaddins", "outlook customer manager",
    "sync issues", "conflicts", "local failures", "server failures",
    "social activity notifications", "quick step settings",
    "conversation action settings", "conversation history", "team chat",
    "eventcheckpoints", "files", "rss feeds", "rss subscriptions",
    "personmetadata", "recipient cache", "external contacts",
    "organizational contacts", "gal contacts", "companies",
    "suggested contacts", "sharing", "reminders", "clean up folder",
    "large mail", "todo search", "to-do search", "the file so far",
    "imapmail", "yammer", "quarantine", "purposeful", "sms",
    "calendar", "contacts", "tasks", "notes", "journal",
}
_GUID_NAME_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-", )


def is_system_folder_name(name):
    n = (name or "").strip().lower()
    if not n:
        return False
    return n in SYSTEM_FOLDER_NAMES or bool(_GUID_NAME_RE.match(n))


def list_mail_folders(max_depth=3, with_counts=True):
    """Worker-thread only. Enumerate mail folders across every store the
    profile has open — the user's own mailbox, shared mailboxes, archives.

    Returns [{path, name, store, depth, count}]. `path` is Outlook's
    FolderPath ("\\\\sales@example.com\\Inbox") and is what gets saved: it is
    human-readable and stable across restarts, unlike EntryID, which churns
    under Cached Exchange Mode.
    """
    outlook_thread_init()
    try:
        app = fresh_outlook()
        ns = app.GetNamespace("MAPI")
        out = []

        def walk(folders, depth, store_name, parent_system):
            if depth > max_depth:
                return
            try:
                count = folders.Count
            except Exception:
                return
            for i in range(1, count + 1):
                try:
                    f = folders.Item(i)
                except Exception:
                    continue
                try:
                    is_mail = (f.DefaultItemType == OL_ITEM_MAIL)
                except Exception:
                    is_mail = True   # assume mail if the type isn't exposed
                try:
                    path = f.FolderPath
                    name = f.Name
                except Exception:
                    continue
                # a system folder taints its whole subtree — Yammer Root's
                # Inbound/Outbound/Feeds children are plumbing too
                sys_flag = parent_system or is_system_folder_name(name)
                if is_mail:
                    n = -1
                    if with_counts:
                        try:
                            n = f.Items.Count
                        except Exception:
                            n = -1
                    out.append({"path": path, "name": name,
                                "store": store_name or name,
                                "depth": depth, "count": n,
                                "system": bool(sys_flag)})
                try:
                    walk(f.Folders, depth + 1, store_name or name, sys_flag)
                except Exception:
                    continue

        try:
            roots = ns.Folders
            root_count = roots.Count
        except Exception:
            return out
        for i in range(1, root_count + 1):
            try:
                root = roots.Item(i)
                store_name = root.Name
            except Exception:
                continue
            try:
                walk(root.Folders, 0, store_name, False)
            except Exception:
                continue
        return out
    finally:
        outlook_thread_uninit()


def default_inbox_path():
    """Worker-thread only. FolderPath of the default Inbox, or ''."""
    outlook_thread_init()
    try:
        app = fresh_outlook()
        ns = app.GetNamespace("MAPI")
        return ns.GetDefaultFolder(OL_FOLDER_INBOX).FolderPath
    except Exception:
        return ""
    finally:
        outlook_thread_uninit()


def _resolve_folder(ns, path):
    """Find a folder by its FolderPath. Returns the folder or None."""
    target = (path or "").strip()
    if not target:
        return None
    found = []

    def walk(folders, depth):
        if found or depth > 6:
            return
        try:
            count = folders.Count
        except Exception:
            return
        for i in range(1, count + 1):
            if found:
                return
            try:
                f = folders.Item(i)
                fp = f.FolderPath
            except Exception:
                continue
            if fp == target:
                found.append(f)
                return
            # only descend when the target sits below this node
            if target.startswith(fp):
                try:
                    walk(f.Folders, depth + 1)
                except Exception:
                    continue

    try:
        roots = ns.Folders
        for i in range(1, roots.Count + 1):
            if found:
                break
            try:
                root = roots.Item(i)
            except Exception:
                continue
            if root.FolderPath == target:
                found.append(root)
                break
            walk(root.Folders, 0)
    except Exception:
        return None
    return found[0] if found else None


def _items_from_folder(folder, max_items, unread_only, source_label):
    """Read MailItems out of an open folder. Assumes COM is initialized."""
    out = []
    try:
        items = folder.Items
        items.Sort("[ReceivedTime]", True)
        if unread_only:
            items = items.Restrict("[Unread] = True")
    except Exception:
        return out
    count = 0
    for item in items:
        if count >= max_items:
            break
        try:
            if getattr(item, "Class", None) != 43:  # olMail
                continue
            mid = _item_message_id(item)
            rt = item.ReceivedTime
            received = datetime(rt.year, rt.month, rt.day, rt.hour,
                                rt.minute, rt.second,
                                tzinfo=timezone.utc).isoformat(
                                    timespec="seconds")
            sender_addr = ""
            try:
                sender_addr = item.SenderEmailAddress or ""
                if sender_addr.startswith("/"):
                    exu = item.Sender.GetExchangeUser()
                    if exu is not None:
                        sender_addr = exu.PrimarySmtpAddress or sender_addr
            except Exception:
                pass
            out.append(MailItem(
                message_id=mid,
                subject=item.Subject or "",
                sender=sender_addr,
                sender_name=item.SenderName or "",
                received_at=received,
                body=(item.Body or "").strip(),
                source="outlook",
                source_path=source_label,
            ))
            count += 1
        except Exception:
            continue
    return out


def scan_outlook_folders(paths=None, max_items=100, unread_only=False):
    """Worker-thread only. Scan the given FolderPaths (default Inbox when
    none are given). max_items applies PER FOLDER so one busy mailbox can't
    starve the others. Returns (items, per_folder_report)."""
    outlook_thread_init()
    try:
        app = fresh_outlook()
        ns = app.GetNamespace("MAPI")
        targets = []
        if paths:
            for p in paths:
                f = _resolve_folder(ns, p)
                targets.append((p, f))
        else:
            try:
                f = ns.GetDefaultFolder(OL_FOLDER_INBOX)
                targets.append((f.FolderPath, f))
            except Exception:
                targets.append(("(default inbox)", None))
        all_items, report = [], []
        for label, folder in targets:
            if folder is None:
                report.append((label, 0, "not found"))
                continue
            got = _items_from_folder(folder, max_items, unread_only, label)
            all_items.extend(got)
            report.append((label, len(got), "ok"))
        return all_items, report
    finally:
        outlook_thread_uninit()


def scan_outlook_inbox(max_items=100, unread_only=False):
    """Back-compat wrapper: default Inbox only, items list only."""
    items, _report = scan_outlook_folders(None, max_items, unread_only)
    return items


def find_outlook_item_by_message_id(ns, message_id):
    """DASL filter on PR_INTERNET_MESSAGE_ID across Inbox. EntryID churns
    under Cached Exchange Mode; Message-ID does not."""
    inbox = ns.GetDefaultFolder(6)
    flt = "@SQL=\"%s\" = '%s'" % (PR_INTERNET_MESSAGE_ID,
                                  message_id.replace("'", "''"))
    try:
        found = inbox.Items.Restrict(flt)
        for item in found:
            return item
    except Exception:
        pass
    return None


def _text_to_html(text):
    """Minimal, safe HTML for a plain-text draft. Deliberately unstyled so it
    inherits the font of the surrounding Outlook message."""
    import html as _html
    esc = _html.escape(text or "")
    paras = [p for p in esc.split("\n\n")]
    body = "".join("<p>%s</p>" % p.replace("\n", "<br>\n") for p in paras)
    return "<div>%s</div>" % body


def send_outlook_reply(message_id, body, use_outlook_signature=True):
    """Worker-thread only. Finds the original by Message-ID, creates a Reply,
    puts the draft above everything Outlook already prepared, sends.

    v1.1.0: writes HTMLBody rather than Body when keeping Outlook's
    signature. Item.Reply() already contains the user's configured reply
    signature — images, logos, formatting and all — plus the quoted
    original. Assigning .Body collapses that whole document to plain text
    and the signature's images are lost. Prepending to .HTMLBody keeps it
    intact, which is why the sent mail looks like the rest of the user's
    mail without Replyit having to reconstruct a signature at all.

    Returns (ok, detail).
    """
    outlook_thread_init()
    try:
        app = fresh_outlook()
        ns = app.GetNamespace("MAPI")
        item = find_outlook_item_by_message_id(ns, message_id)
        if item is None:
            return False, "original not found by Message-ID"
        reply = item.Reply()
        if use_outlook_signature:
            existing = ""
            try:
                existing = reply.HTMLBody or ""
            except Exception:
                existing = ""
            if existing:
                html_draft = _text_to_html(body)
                # insert just inside <body> when present so the document
                # structure (and any signature styling) is preserved
                lowered = existing.lower()
                idx = lowered.find("<body")
                if idx != -1:
                    end = lowered.find(">", idx)
                    if end != -1:
                        reply.HTMLBody = (existing[:end + 1] + html_draft
                                          + existing[end + 1:])
                    else:
                        reply.HTMLBody = html_draft + existing
                else:
                    reply.HTMLBody = html_draft + existing
            else:
                # no HTML available (plain-text account) — fall back
                reply.Body = body + "\n\n" + (reply.Body or "")
        else:
            reply.Body = body + "\n\n" + (reply.Body or "")
        reply.Send()
        return True, "sent"
    except Exception as e:
        return False, "COM error: %s" % e
    finally:
        outlook_thread_uninit()
