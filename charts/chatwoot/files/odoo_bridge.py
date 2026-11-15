"""Odoo bridge of Chatwoot: Odoo tickets and leads as conversations in Chatwoot API inboxes.

Odoo owns every ticket, lead and mail; Chatwoot is the screen staff answer in.

Odoo to Chatwoot: Odoo (module grncloud_helpdesk) posts an event with record ids only to
EVENT_PORT, signed with HMAC-SHA256 over "<timestamp>.<body>" (X-Grncloud-Signature). The
bridge reads the record over the Odoo JSON-2 API and, in the inbox of its model, finds or
creates the contact and the conversation (custom attribute odoo_ref), adds a customer mail
as an incoming message (source_id odoo:mail.message:<id>) and resolves the conversation when
the ticket is closed. The Odoo record gets the conversation address in x_chatwoot_url.

Chatwoot to Odoo: Chatwoot posts the API inboxes' webhooks to WEBHOOK_PORT, signed with
the inbox secret (X-Chatwoot-Signature). A public reply by a staff member is posted on the
Odoo record with message_post, so Odoo mails the customer, with x_chatwoot_message_id set;
a resolved conversation moves its ticket to the solved stage. Private notes, bot messages
and the bridge's own incoming messages are never mirrored.

An Odoo event is answered 200 only once it is done, 503 while it still runs and 502 on an
error: Odoo's outbox retries it. Chatwoot never retries a webhook and marks a message failed
on any other answer, so a valid webhook is answered 200 at once and retried here, with
backoff, for about 40 minutes; when that fails too, staff get a private note. Every write is
keyed on its source id, so a redelivered event or a retry changes nothing. A reply acts only
on a record whose x_chatwoot_url names its conversation. Standard library only; prints no
credential.
"""
import hashlib
import hmac
import html
import http.server
import json
import logging
import os
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from html.parser import HTMLParser

LOG = logging.getLogger("odoo-bridge")
MAX_BODY = 1 << 20
SIGNATURE_AGE = 300
MAX_CONTENT = 150000
MESSAGE_PAGES = 10
WEBHOOK_RETRIES = (5, 30, 120, 600, 1800)
MAX_PENDING = 500
MAX_REQUESTS = 32
# Odoo events running or queued; beyond that Odoo hears 503 and retries from its outbox
MAX_EVENTS = 64
# seconds a stopping pod spends telling staff about replies it could not send
STOP_BUDGET = 10
INBOX_CACHE_SECONDS = 300
ODOO_SOURCE = "odoo:mail.message:"
CONVERSATION_PATH = re.compile(r"/conversations/(\d+)/?$")
MODELS = {
    "helpdesk.ticket": {
        "fields": ["name", "partner_id", "partner_email", "partner_name", "stage_id", "team_id", "x_chatwoot_url"],
        "email": "partner_email",
        "contact": "partner_name",
        "name": "partner_name",
    },
    "crm.lead": {
        "fields": ["name", "partner_id", "email_from", "contact_name", "partner_name", "x_chatwoot_url"],
        "email": "email_from",
        "contact": "contact_name",
        "name": "contact_name",
    },
}


def read_file(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


class Config:
    def __init__(self, env=os.environ):
        self.chatwoot_url = env.get("CHATWOOT_URL", "http://chatwoot-web:3000").rstrip("/")
        self.account_id = int(env.get("CHATWOOT_ACCOUNT_ID", "1"))
        self.public_url = env["PUBLIC_URL"].rstrip("/")
        self.token_file = env["CHATWOOT_TOKEN_FILE"]
        self.inbox_secret_file = env["INBOX_SECRET_FILE"]
        self.inboxes = {"helpdesk.ticket": env.get("TICKET_INBOX", ""), "crm.lead": env.get("LEAD_INBOX", "")}
        self.odoo_url = env["ODOO_URL"].rstrip("/")
        self.odoo_database = env.get("ODOO_DATABASE", "")
        self.odoo_key_file = env["ODOO_API_KEY_FILE"]
        self.event_secret_file = env["EVENT_SECRET_FILE"]
        self.solved_stage = env.get("SOLVED_STAGE", "Solved")
        self.webhook_port = int(env.get("WEBHOOK_PORT", "8080"))
        self.event_port = int(env.get("EVENT_PORT", "8081"))
        # Odoo reads for 3 s: answer before it gives up
        self.event_deadline = float(env.get("EVENT_DEADLINE", "2.5"))
        self.webhook_retries = tuple(float(x) for x in env.get("WEBHOOK_RETRIES", "").split(",") if x.strip()) \
            or WEBHOOK_RETRIES
        if not self.public_url.startswith("https://"):
            raise ValueError("PUBLIC_URL must be an https address")
        if not self.odoo_url.startswith("https://") and not env.get("ODOO_ALLOW_HTTP"):
            raise ValueError("ODOO_URL must be an https address")


# ---------------------------------------------------------------------------- signatures


def _timestamp(value):
    # the header is ASCII digits only; int() would also take signs and other digits
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 12:
        return int(value)
    return None


def verify(secret, timestamp, body, signature, now=None):
    """True when signature is sha256=<hex HMAC of "<timestamp>.<body>"> made recently.

    The same check for Odoo's events and Chatwoot's webhooks; any malformed input is False.
    """
    if not secret or not isinstance(signature, str) or not signature.isascii():
        return False
    ts = _timestamp(timestamp)
    if ts is None or abs((time.time() if now is None else now) - ts) > SIGNATURE_AGE:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), b"%d." % ts + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.encode())


# ---------------------------------------------------------------------------- text


class _Text(HTMLParser):
    """Visible text of an Odoo mail body, without the quoted history Odoo marks."""

    BLOCK = {"p", "div", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "ul", "ol", "blockquote"}
    VOID = {"br", "img", "hr", "meta", "link", "input", "wbr", "col", "area", "base", "source"}
    SKIP = {"script", "style", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.stack = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            if not self.hidden and tag == "br":
                self.out.append("\n")
            return
        hide = tag in self.SKIP or any(k == "data-o-mail-quote" for k, _ in attrs)
        self.stack.append((tag, hide))
        if hide:
            self.hidden += 1
        elif not self.hidden:
            if tag in self.BLOCK:
                self.out.append("\n")
            elif tag == "li":
                self.out.append("\n- ")

    def handle_endtag(self, tag):
        # a stray or void end tag (</br>, <br/>) closes nothing
        if not any(open_tag == tag for open_tag, _ in self.stack):
            return
        while self.stack:
            open_tag, hide = self.stack.pop()
            if hide:
                self.hidden -= 1
            elif not self.hidden and open_tag in self.BLOCK:
                self.out.append("\n")
            if open_tag == tag:
                break

    def handle_data(self, data):
        if not self.hidden:
            self.out.append(data)


def html_to_text(body):
    parser = _Text()
    parser.feed(body or "")
    parser.close()
    text = "".join(parser.out).replace("\xa0", " ")
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def text_to_html(text):
    """A staff reply as Odoo mail HTML: escaped, one paragraph per block, line breaks kept."""
    blocks = [b for b in re.split(r"\n\s*\n", (text or "").strip()) if b.strip()]
    return "".join("<p>%s</p>" % html.escape(b.strip()).replace("\n", "<br>") for b in blocks)


def clip(text):
    return text if len(text) <= MAX_CONTENT else text[: MAX_CONTENT - 40] + "\n\n[shortened, full text in Odoo]"


# ---------------------------------------------------------------------------- clients


class Gone(Exception):
    """The Odoo record no longer exists (MissingError): nothing left to do."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # urllib would resend the Authorization header to wherever a redirect points
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


class HttpError(Exception):
    def __init__(self, status, name=""):
        super().__init__(f"HTTP {status}{' ' + name if name else ''}")
        self.status = status
        self.name = name


def http_json(method, url, body=None, headers=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        name = ""
        try:
            name = str(json.loads(e.read() or b"{}").get("name", ""))[:120]
        except (ValueError, AttributeError):
            pass
        raise HttpError(e.code, name) from None
    return json.loads(raw) if raw else None


class Odoo:
    """The Odoo JSON-2 API (/json/2/<model>/<method>) with the bridge user's API key."""

    def __init__(self, cfg):
        self.cfg = cfg

    def call(self, model, method, **kwargs):
        headers = {"Authorization": "bearer " + read_file(self.cfg.odoo_key_file)}
        if self.cfg.odoo_database:
            headers["X-Odoo-Database"] = self.cfg.odoo_database
        try:
            return http_json("POST", f"{self.cfg.odoo_url}/json/2/{model}/{method}", kwargs, headers)
        except HttpError as e:
            # only a deleted record ends the retries; a 404 of an unknown route or method does not
            if e.name.endswith("MissingError"):
                raise Gone(f"{model} {method}: {e}") from None
            raise

    def read(self, model, rid, fields):
        rows = self.call(model, "read", ids=[rid], fields=fields)
        if not rows:
            raise Gone(f"{model} {rid}")
        return rows[0]

    def search_read(self, model, domain, fields, limit=1):
        return self.call(model, "search_read", domain=domain, fields=fields, limit=limit)

    def url(self, model, rid):
        return f"{self.cfg.odoo_url}/odoo/{model}/{rid}"


class Chatwoot:
    """The Chatwoot Application API of one account, as the bridge user."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._inboxes = {}
        self._inboxes_at = 0.0

    def api(self, method, path, body=None):
        # a 404 stays an error: a wrong account or a missing right must not end Odoo's retries
        url = f"{self.cfg.chatwoot_url}/api/v1/accounts/{self.cfg.account_id}{path}"
        return http_json(method, url, body, {"api_access_token": read_file(self.cfg.token_file)})

    def inbox_id(self, name):
        if name not in self._inboxes or time.monotonic() - self._inboxes_at > INBOX_CACHE_SECONDS:
            self._inboxes = {i["name"]: i["id"] for i in (self.api("GET", "/inboxes") or {}).get("payload", [])}
            self._inboxes_at = time.monotonic()
        if name not in self._inboxes:
            raise RuntimeError(f"no Chatwoot inbox named {name!r}")
        return self._inboxes[name]

    def find_contact(self, email, identifier):
        """The contact with exactly this address, else this identifier.

        The filter matches exactly; /contacts/search matches substrings and returns one page.
        """
        for key, value in (("email", email), ("identifier", identifier)):
            if not value:
                continue
            query = {"payload": [{"attribute_key": key, "filter_operator": "equal_to", "values": [value],
                                  "query_operator": None}]}
            for contact in (self.api("POST", "/contacts/filter", query) or {}).get("payload", []):
                if (contact.get(key) or "").lower() == value.lower():
                    return contact
        return None

    def create_contact(self, name, email, identifier):
        body = {"name": name or email or identifier}
        if email:
            body["email"] = email
        if identifier:
            body["identifier"] = identifier
        created = self.api("POST", "/contacts", body) or {}
        return (created.get("payload") or {}).get("contact") or created.get("payload") or created

    def contact_conversations(self, contact_id):
        return (self.api("GET", f"/contacts/{contact_id}/conversations") or {}).get("payload", [])

    def conversation(self, cid):
        """The conversation, or None when it was deleted."""
        try:
            return self.api("GET", f"/conversations/{cid}")
        except HttpError as e:
            if e.status == 404:
                return None
            raise

    def create_conversation(self, inbox_id, contact_id, custom, additional):
        return self.api("POST", "/conversations", {
            "inbox_id": inbox_id, "contact_id": contact_id, "status": "open",
            "custom_attributes": custom, "additional_attributes": additional,
        })

    def messages(self, cid):
        """The conversation's messages, newest page first, MESSAGE_PAGES pages at most."""
        before = None
        for _ in range(MESSAGE_PAGES):
            query = f"?before={before}" if before else ""
            page = (self.api("GET", f"/conversations/{cid}/messages{query}") or {}).get("payload", [])
            if not page:
                return
            yield from reversed(page)
            before = min(m["id"] for m in page)

    def create_message(self, cid, content, message_type="incoming", source_id=None, private=False):
        body = {"content": content, "message_type": message_type, "private": private}
        if source_id:
            body["source_id"] = source_id
        return self.api("POST", f"/conversations/{cid}/messages", body)

    def resolve(self, cid):
        return self.api("POST", f"/conversations/{cid}/toggle_status", {"status": "resolved"})


# ---------------------------------------------------------------------------- bridge


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.counts = {}

    def seed(self, name, **labels):
        """A counter at 0, so the first increment is an increase an alert can see."""
        with self.lock:
            self.counts.setdefault((name, tuple(sorted(labels.items()))), 0)

    def inc(self, name, **labels):
        key = (name, tuple(sorted(labels.items())))
        with self.lock:
            self.counts[key] = self.counts.get(key, 0) + 1

    def render(self):
        lines = []
        with self.lock:
            for (name, labels), value in sorted(self.counts.items()):
                inner = ",".join(f'{k}="{v}"' for k, v in labels)
                lines.append(f"chatwoot_odoo_bridge_{name}_total{{{inner}}} {value}")
        return "\n".join(lines) + "\n"


class Bridge:
    def __init__(self, cfg, odoo=None, chatwoot=None):
        self.cfg = cfg
        self.odoo = odoo or Odoo(cfg)
        self.chatwoot = chatwoot or Chatwoot(cfg)
        self.metrics = Metrics()
        # striped: bounded memory, and re-entrant for a lock taken twice on one path
        self._locks = [threading.RLock() for _ in range(64)]
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="work")
        self.retries = cfg.webhook_retries
        self._pending = 0
        self._pending_lock = threading.Lock()
        self._timers = {}  # Timer -> payload waiting for its retry
        self._dropped = []
        self._stopping = False
        self._events = 0
        for name, result in (("webhooks", "given_up"), ("webhooks", "dropped"), ("events", "error")):
            self.metrics.seed(name, result=result)

    def lock(self, ref):
        return self._locks[int(hashlib.sha256(ref.encode()).hexdigest(), 16) % len(self._locks)]

    def message_key(self, mid):
        """x_chatwoot_message_id of a Chatwoot message: unique across instances and accounts."""
        host = urllib.parse.urlsplit(self.cfg.public_url).hostname
        return f"{host}/{self.cfg.account_id}/{mid}"

    def conversation_url(self, cid):
        return f"{self.cfg.public_url}/app/accounts/{self.cfg.account_id}/conversations/{cid}"

    # ------------------------------------------------------------ Odoo to Chatwoot

    def handle_event(self, event):
        name, model, rid = event.get("event"), event.get("model"), event.get("id")
        if model not in MODELS or not isinstance(rid, int) or isinstance(rid, bool):
            LOG.warning("event %s for %s ignored: not a bridged record", name, model)
            return "ignored"
        if not self.cfg.inboxes.get(model):
            return "ignored"
        if name in ("ticket.created", "lead.created"):
            self.ensure_conversation(model, rid)
        elif name == "message.created":
            mid = event.get("message_id")
            if not isinstance(mid, int) or isinstance(mid, bool):
                LOG.warning("event message.created for %s %s has no message id", model, rid)
                return "ignored"
            self.mirror_mail(model, rid, mid)
        elif name == "ticket.closed":
            self.close_conversation(model, rid)
        else:
            LOG.warning("event %s ignored: unknown", name)
            return "ignored"
        return "done"

    def ensure_conversation(self, model, rid, create=True):
        """The display id of the record's conversation, created when missing (and create)."""
        ref = f"{model}:{rid}"
        with self.lock(ref):
            record = self.odoo.read(model, rid, MODELS[model]["fields"])
            cid = self._linked(record, ref)
            if cid:
                return cid
            spec = MODELS[model]
            email = (record.get(spec["email"]) or "").strip() or None
            partner = record.get("partner_id")
            # without a partner the record itself names the contact, so a retry finds it again
            identifier = f"odoo:res.partner:{partner[0]}" if partner else f"odoo:{model}:{rid}"
            contact = self.chatwoot.find_contact(email, identifier)
            if contact:
                # odoo_ref is editable by staff: only a conversation in the record's own inbox counts
                for conv in self.chatwoot.contact_conversations(contact["id"]):
                    if ((conv.get("custom_attributes") or {}).get("odoo_ref") == ref
                            and conv.get("inbox_id") == self.chatwoot.inbox_id(self.cfg.inboxes[model])):
                        self._link(model, rid, conv["id"])
                        return conv["id"]
            if not create:
                return None
            if not contact:
                name = record.get(spec["contact"]) or record.get("partner_name") or (partner[1] if partner else None)
                try:
                    contact = self.chatwoot.create_contact(name, email, identifier)
                except HttpError as e:
                    # address or identifier taken meanwhile: use that contact
                    contact = e.status == 422 and self.chatwoot.find_contact(email, identifier)
                    if not contact:
                        raise
            conv = self.chatwoot.create_conversation(
                self.chatwoot.inbox_id(self.cfg.inboxes[model]), contact["id"],
                {"odoo_ref": ref, "odoo_url": self.odoo.url(model, rid)},
                {"mail_subject": record.get("name") or ref},
            )
            self.metrics.inc("conversations_created", model=model)
            LOG.info("%s linked to new conversation %s", ref, conv["id"])
            self._link(model, rid, conv["id"])
            return conv["id"]

    def _linked(self, record, ref):
        match = CONVERSATION_PATH.search(record.get("x_chatwoot_url") or "")
        if not match:
            return None
        conv = self.chatwoot.conversation(int(match.group(1)))
        if not conv or (conv.get("custom_attributes") or {}).get("odoo_ref") != ref:
            return None
        return conv["id"]

    def _link(self, model, rid, cid):
        self.odoo.call(model, "write", ids=[rid], vals={"x_chatwoot_url": self.conversation_url(cid)})

    def mirror_mail(self, model, rid, mid):
        # under the record's lock: Chatwoot has no unique source id, the check below is the dedupe
        with self.lock(f"{model}:{rid}"):
            self._mirror_mail(model, rid, mid)

    def _mirror_mail(self, model, rid, mid):
        cid = self.ensure_conversation(model, rid)
        source = f"{ODOO_SOURCE}{mid}"
        msg = self.odoo.read("mail.message", mid, ["body", "email_from", "model", "res_id", "attachment_ids", "subject"])
        if msg.get("model") != model or msg.get("res_id") != rid:
            LOG.warning("mail.message %s is not on %s %s; not mirrored", mid, model, rid)
            return
        for existing in self.chatwoot.messages(cid):
            if existing.get("source_id") == source:
                return
        text = html_to_text(msg.get("body"))
        contact_email = self._contact_email(cid)
        sender = (msg.get("email_from") or "").strip()
        if sender and contact_email and contact_email.lower() not in sender.lower():
            text = f"From: {sender}\n\n{text}"
        if msg.get("attachment_ids"):
            text += f"\n\n[{len(msg['attachment_ids'])} attachment(s) in Odoo: {self.odoo.url(model, rid)}]"
        self.chatwoot.create_message(cid, clip(text or "(empty mail)"), "incoming", source)
        self.metrics.inc("messages_mirrored", direction="odoo_to_chatwoot")

    def _contact_email(self, cid):
        conv = self.chatwoot.conversation(cid) or {}
        return (((conv.get("meta") or {}).get("sender") or {}).get("email") or "").strip()

    def close_conversation(self, model, rid):
        cid = self.ensure_conversation(model, rid, create=False)
        if not cid:
            return
        conv = self.chatwoot.conversation(cid)
        if conv and conv.get("status") != "resolved":
            self.chatwoot.resolve(cid)
            LOG.info("%s:%s closed in Odoo; conversation %s resolved", model, rid, cid)

    # ------------------------------------------------------------ Chatwoot to Odoo

    def handle_webhook(self, payload):
        event = payload.get("event")
        if event == "message_created":
            return self.reply(payload)
        if event == "conversation_status_changed":
            return self.solve(payload)
        return "ignored"

    def _bridged(self, conv):
        """(model, id) of a conversation in one of the bridge's inboxes, else None."""
        if not conv:
            return None
        ref = (conv.get("custom_attributes") or {}).get("odoo_ref") or ""
        model, _, rid = ref.partition(":")
        if model not in MODELS or not rid.isdigit() or not self.cfg.inboxes.get(model):
            return None
        if conv.get("inbox_id") != self.chatwoot.inbox_id(self.cfg.inboxes[model]):
            return None
        return model, int(rid)

    def _record_of(self, conv, target, fields):
        """The record, when its x_chatwoot_url names this conversation; staff can edit odoo_ref."""
        model, rid = target
        record = self.odoo.read(model, rid, ["x_chatwoot_url", *fields])
        match = CONVERSATION_PATH.search(record.get("x_chatwoot_url") or "")
        if not match or int(match.group(1)) != conv["id"]:
            LOG.warning("conversation %s names %s:%s, which links another conversation; not acted on",
                        conv["id"], model, rid)
            return None
        return record

    def _customer(self, model, record):
        """res.partner id the reply is mailed to: the record's partner, or one for its address."""
        if record.get("partner_id"):
            return record["partner_id"][0]
        spec = MODELS[model]
        email = (record.get(spec["email"]) or "").strip()
        if not email:
            return None
        name = (record.get(spec["name"]) or "").replace('"', "").replace("\\", "").strip()
        found = self.odoo.call("res.partner", "find_or_create", email=f'"{name}" <{email}>' if name else email)
        return found[0] if isinstance(found, list) else found

    def reply(self, payload):
        if payload.get("message_type") != "outgoing" or payload.get("private"):
            return "ignored"
        cid = (payload.get("conversation") or {}).get("id")
        mid = payload.get("id")
        if not isinstance(cid, int) or not isinstance(mid, int):
            return "ignored"
        # webhooks carry what Chatwoot sent; act only on what the API says now
        conv = self.chatwoot.conversation(cid)
        target = self._bridged(conv)
        if not target:
            return "ignored"
        msg = next((m for m in self.chatwoot.messages(cid) if m.get("id") == mid), None)
        if not msg or msg.get("private") or msg.get("message_type") not in (1, "outgoing"):
            return "ignored"
        # the API names the sender without an email; the signed webhook carries the email
        sender = msg.get("sender") or {}
        hooked = payload.get("sender") or {}
        if sender.get("type") != "user" or hooked.get("type") != "user" or hooked.get("id") != sender.get("id"):
            LOG.info("conversation %s message %s is not by a staff member; not sent", cid, mid)
            return "ignored"
        sender = hooked
        model, rid = target
        spec = MODELS[model]
        key = self.message_key(mid)
        with self.lock(f"chatwoot:{mid}"):
            if self.odoo.search_read("mail.message", [["x_chatwoot_message_id", "=", key]], ["id"]):
                return "done"
            record = self._record_of(conv, target, ["partner_id", spec["email"], spec["name"]])
            if not record:
                return "ignored"
            customer = self._customer(model, record)
            if not customer:
                self.chatwoot.create_message(cid, "This reply was not sent: the Odoo record has no customer address. "
                                             + self.odoo.url(model, rid), "outgoing", private=True)
                return "ignored"
            kwargs = {
                "ids": [rid],
                "body": text_to_html(msg.get("content") or ""),
                # escaped by text_to_html; JSON carries no Markup, so Odoo would escape it again
                "body_is_html": True,
                "message_type": "comment",
                "subtype_xmlid": "mail.mt_comment",
                "x_chatwoot_message_id": key,
                "partner_ids": [customer],
            }
            author = self._staff_partner(sender.get("email"))
            if author:
                kwargs["author_id"] = author
            self.odoo.call(model, "message_post", **kwargs)
        self.metrics.inc("messages_mirrored", direction="chatwoot_to_odoo")
        LOG.info("conversation %s message %s posted on %s:%s", cid, mid, model, rid)
        if msg.get("attachments"):
            self.chatwoot.create_message(cid, "The attachments of this reply were not sent to the customer: "
                                         "Odoo sends the mail, attach them there. " + self.odoo.url(model, rid),
                                         "outgoing", private=True)
        return "done"

    def _staff_partner(self, email):
        """The res.partner of the Odoo user with this login or email, or None."""
        if not email:
            return None
        # =ilike is a LIKE: an address with _ or % would match other users
        exact = email.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        for domain in ([["login", "=ilike", exact]], [["email", "=ilike", exact]]):
            users = self.odoo.search_read("res.users", domain, ["partner_id"])
            if users and users[0].get("partner_id"):
                return users[0]["partner_id"][0]
        return None

    def solve(self, payload):
        if payload.get("status") != "resolved" or not isinstance(payload.get("id"), int):
            return "ignored"
        conv = self.chatwoot.conversation(payload["id"])
        target = self._bridged(conv)
        if not target or target[0] != "helpdesk.ticket" or conv.get("status") != "resolved":
            return "ignored"
        rid = target[1]
        ticket = self._record_of(conv, target, ["stage_id", "team_id"])
        if not ticket:
            return "ignored"
        if ticket.get("stage_id"):
            stage = self.odoo.read("helpdesk.stage", ticket["stage_id"][0], ["fold"])
            if stage.get("fold"):
                return "done"
        domain = [["fold", "=", True]]
        if ticket.get("team_id"):
            domain.append(["team_ids", "in", [ticket["team_id"][0]]])
        stages = self.odoo.call("helpdesk.stage", "search_read", domain=domain, fields=["name"], order="sequence")
        if not stages:
            LOG.warning("helpdesk.ticket %s: no folded stage for its team; not solved", rid)
            return "ignored"
        stage = next((s for s in stages if s["name"] == self.cfg.solved_stage), stages[0])
        self.odoo.call("helpdesk.ticket", "write", ids=[rid], vals={"stage_id": stage["id"]})
        LOG.info("conversation %s resolved; helpdesk.ticket %s moved to %s", conv["id"], rid, stage["name"])
        return "done"

    # ------------------------------------------------------------ run with a deadline

    def run(self, kind, func, arg, deadline):
        """(status, text): 200 when done in time, 503 while it is still running or busy, 502 on error."""
        with self._pending_lock:
            if self._events >= MAX_EVENTS:
                return 503, "busy"
            self._events += 1
        future = self.pool.submit(self._guarded, kind, func, arg)
        future.add_done_callback(self._event_done)
        try:
            result = future.result(timeout=deadline)
        except FutureTimeout:
            self.metrics.inc(kind, result="late")
            return 503, "in progress"
        if isinstance(result, Exception):
            return 502, type(result).__name__
        return 200, result

    def accept_webhook(self, payload):
        """Queue a verified webhook; False when the queue is full."""
        with self._pending_lock:
            if self._pending >= MAX_PENDING:
                return False
            self._pending += 1
        self.pool.submit(self._attempt, payload, 0)
        return True

    def _event_done(self, _future):
        with self._pending_lock:
            self._events -= 1

    def _attempt(self, payload, attempt):
        if self._stopping:
            return self._drop(payload)
        result = self._guarded("webhooks", self.handle_webhook, payload)
        if not isinstance(result, Exception):
            self._done()
            return
        if self._stopping:
            return self._drop(payload)
        if attempt < len(self.retries):
            timer = threading.Timer(self.retries[attempt], self._retry, (payload, attempt + 1))
            timer.daemon = True
            with self._pending_lock:
                self._timers[timer] = payload
            timer.start()
            return
        self._done()
        self.metrics.inc("webhooks", result="given_up")
        LOG.error("webhook %s for conversation %s given up after %d attempts", payload.get("event"),
                  (payload.get("conversation") or {}).get("id") or payload.get("id"), attempt + 1)
        self._tell_staff(payload)

    def _retry(self, payload, attempt):
        with self._pending_lock:
            mine = self._timers.pop(threading.current_thread(), None)
        if mine is None:
            return  # stop() took this one over
        try:
            self.pool.submit(self._attempt, payload, attempt)
        except RuntimeError:  # the pool is shut down
            self._drop(payload)

    def _drop(self, payload):
        with self._pending_lock:
            self._pending -= 1
            self._dropped.append(payload)
        self.metrics.inc("webhooks", result="dropped")

    def _done(self):
        with self._pending_lock:
            self._pending -= 1

    def _tell_staff(self, payload, why="This reply did not reach Odoo"):
        if payload.get("event") != "message_created":
            return
        cid = (payload.get("conversation") or {}).get("id")
        try:
            self.chatwoot.create_message(cid, f"{why} and was not sent to the customer. "
                                         "Send it again, or answer in Odoo.", "outgoing", private=True)
        except Exception as e:  # noqa: BLE001
            LOG.error("conversation %s: no note about the lost reply either: %s", cid, type(e).__name__)

    def pending(self):
        with self._pending_lock:
            return self._pending

    def stop(self, budget=STOP_BUDGET):
        """Stop retrying and finish running work; returns how many webhooks were not delivered.

        Chatwoot already shows those replies as sent, so each gets a private note while the
        budget lasts.
        """
        self._stopping = True
        with self._pending_lock:
            timers, self._timers = self._timers, {}
        for timer, payload in timers.items():
            timer.cancel()
            self._drop(payload)
        # running work finishes; queued webhooks see _stopping and are dropped
        self.pool.shutdown(wait=True)
        with self._pending_lock:
            dropped, self._dropped = self._dropped, []
        end = time.monotonic() + budget
        for payload in dropped:
            if time.monotonic() > end:
                break
            self._tell_staff(payload, "The bridge restarted before this reply reached Odoo")
        return len(dropped)

    def _guarded(self, kind, func, arg):
        try:
            result = func(arg)
        except Gone as e:
            LOG.info("%s: %s is gone; nothing to do", kind, e)
            result = "gone"
        except Exception as e:  # noqa: BLE001
            # class and status only: a body or header may carry customer content or a key
            LOG.error("%s failed: %s %s", kind, type(e).__name__, e if isinstance(e, HttpError) else "")
            self.metrics.inc(kind, result="error")
            return e
        self.metrics.inc(kind, result=result)
        return result


# ---------------------------------------------------------------------------- HTTP


def make_handler(bridge, port_kind):
    cfg = bridge.cfg
    busy = threading.BoundedSemaphore(MAX_REQUESTS)

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "odoo-bridge"
        sys_version = ""
        # the event port is public: a slow client gives up its thread
        timeout = 15

        def log_message(self, *args):
            pass

        def _send(self, status, text):
            data = (text + "\n").encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/healthz":
                return self._send(200, "ok")
            if self.path == "/metrics" and port_kind == "webhook":
                gauge = f"chatwoot_odoo_bridge_webhooks_pending {bridge.pending()}"
                return self._send(200, bridge.metrics.render() + gauge)
            self._send(404, "not found")

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if length <= 0 or length > MAX_BODY:
                return None
            return self.rfile.read(length)

        def do_POST(self):
            if not busy.acquire(blocking=False):
                return self._send(503, "busy")
            try:
                self._post()
            finally:
                busy.release()

        def _post(self):
            expected = "/odoo/events" if port_kind == "event" else "/chatwoot/webhook"
            if self.path != expected:
                return self._send(404, "not found")
            body = self._body()
            if body is None:
                return self._send(400, "bad request")
            if port_kind == "event":
                secret = read_file(cfg.event_secret_file)
                ok = verify(secret, self.headers.get("X-Grncloud-Timestamp"), body, self.headers.get("X-Grncloud-Signature"))
            else:
                secret = read_file(cfg.inbox_secret_file)
                ok = verify(secret, self.headers.get("X-Chatwoot-Timestamp"), body, self.headers.get("X-Chatwoot-Signature"))
            if not ok:
                bridge.metrics.inc("refused", port=port_kind)
                return self._send(401, "bad signature")
            try:
                payload = json.loads(body)
            except ValueError:
                return self._send(400, "bad json")
            if not isinstance(payload, dict):
                return self._send(400, "bad json")
            if port_kind == "event":
                status, text = bridge.run("events", bridge.handle_event, payload, cfg.event_deadline)
            elif bridge.accept_webhook(payload):
                status, text = 200, "accepted"
            else:
                status, text = 503, "queue full"
            self._send(status, text)

    return Handler


def serve(bridge):
    servers = []
    for kind, port in (("webhook", bridge.cfg.webhook_port), ("event", bridge.cfg.event_port)):
        server = http.server.ThreadingHTTPServer(("", port), make_handler(bridge, kind))
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, name=kind, daemon=True).start()
        servers.append(server)
        LOG.info("listening for %ss on %d", kind, port)
    return servers


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(message)s")
    bridge = Bridge(Config())
    servers = serve(bridge)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    for server in servers:
        server.shutdown()
    # running events finish; an unfinished one is retried by Odoo
    dropped = bridge.stop()
    if dropped:
        LOG.error("%d webhook(s) not delivered at shutdown: the replies were not sent", dropped)


if __name__ == "__main__":
    main()
