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

ENGINE_VERSION = "1.0.0"

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


def scan_outlook_inbox(max_items=100, unread_only=False):
    """Worker-thread only. Returns list of MailItem from the default Inbox,
    newest first. Brief walk, no body mutation, read-only."""
    outlook_thread_init()
    try:
        app = fresh_outlook()
        ns = app.GetNamespace("MAPI")
        inbox = ns.GetDefaultFolder(6)  # olFolderInbox
        items = inbox.Items
        items.Sort("[ReceivedTime]", True)
        if unread_only:
            items = items.Restrict("[Unread] = True")
        out, count = [], 0
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
                                    tzinfo=timezone.utc)\
                    .isoformat(timespec="seconds")
                sender_addr = ""
                try:
                    sender_addr = item.SenderEmailAddress or ""
                    if item.SenderEmailAddress and \
                       item.SenderEmailAddress.startswith("/"):
                        # Exchange DN — try SMTP via sender object
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
                    source_path="",
                ))
                count += 1
            except Exception:
                continue
        return out
    finally:
        outlook_thread_uninit()


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


def send_outlook_reply(message_id, body):
    """Worker-thread only. Finds the original by Message-ID, creates a Reply,
    sets the body above the quoted original, sends. Returns (ok, detail)."""
    outlook_thread_init()
    try:
        app = fresh_outlook()
        ns = app.GetNamespace("MAPI")
        item = find_outlook_item_by_message_id(ns, message_id)
        if item is None:
            return False, "original not found by Message-ID"
        reply = item.Reply()
        reply.Body = body + "\n\n" + (reply.Body or "")
        reply.Send()
        return True, "sent"
    except Exception as e:
        return False, "COM error: %s" % e
    finally:
        outlook_thread_uninit()
