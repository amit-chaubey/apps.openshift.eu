"""Agent Bot of Chatwoot that answers through an A2A (Agent2Agent) agent.

Chatwoot sends the bot's webhooks here (worker pods only, by NetworkPolicy). Each one is
signed with the bot's secret (X-Chatwoot-Signature: sha256 HMAC of "<timestamp>.<body>");
anything else is refused. For an incoming customer message in a conversation the bot still
holds (status pending), the text goes to the agent as A2A message/send, one contextId per
conversation, and the answer comes back as an outgoing message with the bot's access token.
A listed keyword, an answer that is the handoff marker, or a failure sets the conversation
to open, so staff take over. The bot never sees a conversation that is open.

With PANEL_PORT it serves the agent panel instead or as well, a Chatwoot Dashboard App in
the conversation sidebar: staff ask the same agent to summarise the conversation, suggest a
reply or answer a question. It listens on 127.0.0.1 next to oauth2-proxy, so only a request
after the staff login arrives; its answers go back to the panel, never to the customer.
With PANEL_FORWARD_URL that panel asks nobody itself: it hands each request to a bridge in
the agent's cluster, with the staff member's access token from oauth2-proxy, and that
bridge (STAFF_ISSUER set) checks the token's signature, issuer, client and group.

With DRAFT_TOKEN_FILE it writes AI drafts: an account webhook of Chatwoot (message_created,
signed with its own secret) arrives on /draft; for an incoming customer message in one of
the listed inboxes the agent drafts the next reply from the conversation's transcript, and
the draft goes into the conversation as a private note. Staff read it, edit it and send it
themselves. Every write of the draft path passes PrivateNotes, which refuses any message
that is not private: a draft can never reach the customer.

With TRIAGE_CATEGORIES the draft path sorts each new conversation first: the agent names one
of the categories and sums the conversation up, and the bridge labels the conversation with
the category and the label TRIAGE_LABEL, gives it to the category's team (Chatwoot assigns
an online member) and adds the summary as a private note. A conversation that already has a
team or an assignee keeps it. An answer without a known category, or no answer, takes the
category TRIAGE_FALLBACK, so every conversation reaches a team. The label TRIAGE_LABEL marks
the conversation as sorted; it is the last write, so a failed triage is done again.

With FACTS_EMAIL_ANNOTATION the triage note carries facts about the customer's tenant, read
from the Kubernetes API: the namespaces whose annotation holds the contact's email name the
tenant (label FACTS_TENANT_LABEL), and the bridge reads pods, CronJobs, volume claims,
warning events and Velero backups of that tenant's namespaces. The tenant comes from the
contact's email, never from what the customer writes; no Secret or ConfigMap is read.

With SWEEP_SECONDS the bridge looks at the open conversations of its inboxes that often and
does what a webhook missed: the triage of a conversation without TRIAGE_LABEL, and a draft
for a customer message that waits without one. After a failure it waits longer each time.

Standard library only; prints no credential.
"""
import base64
import datetime
import hashlib
import hmac
import http.server
import json
import logging
import os
import re
import signal
import ssl
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

LOG = logging.getLogger("agent-bot")
MAX_BODY = 1 << 20
SIGNATURE_AGE = 300
TERMINAL = {"completed", "failed", "canceled", "rejected", "input-required", "auth-required"}


def read_file(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


class Config:
    def __init__(self, env=os.environ):
        self.chatwoot_url = env.get("CHATWOOT_URL", "http://chatwoot-web:3000").rstrip("/")
        # without them only the panel runs
        self.bot_token_file = env.get("BOT_TOKEN_FILE", "")
        self.webhook_secret_file = env.get("WEBHOOK_SECRET_FILE", "")
        self.bot_enabled = bool(self.bot_token_file and self.webhook_secret_file)
        self.a2a_url = env["A2A_URL"]
        self.a2a_timeout = int(env.get("A2A_TIMEOUT", "300"))
        self.auth = env.get("A2A_AUTH", "none")
        self.token_file = env.get("A2A_TOKEN_FILE", "")
        self.token_url = env.get("A2A_TOKEN_URL", "")
        self.client_id = env.get("A2A_CLIENT_ID", "")
        self.client_secret_file = env.get("A2A_CLIENT_SECRET_FILE", "")
        self.scope = env.get("A2A_SCOPE", "")
        self.instructions = env.get("INSTRUCTIONS", "")
        self.keywords = {normalize(k) for k in env.get("HANDOFF_KEYWORDS", "").split(",") if k.strip()}
        self.marker = env.get("HANDOFF_MARKER", "HANDOFF").strip()
        self.handoff_message = env.get("HANDOFF_MESSAGE", "")
        self.failure_message = env.get("FAILURE_MESSAGE", "")
        self.context_attribute = env.get("CONTEXT_ATTRIBUTE", "a2a_context_id")
        self.port = int(env.get("LISTEN_PORT", "8080"))
        # AI drafts as private notes; without the token file the draft path is off
        self.draft_token_file = env.get("DRAFT_TOKEN_FILE", "")
        self.draft_secret_file = env.get("DRAFT_WEBHOOK_SECRET_FILE", "")
        self.drafts_enabled = bool(self.draft_token_file and self.draft_secret_file)
        self.draft_inboxes = {i.strip() for i in env.get("DRAFT_INBOXES", "").split(",") if i.strip()}
        self.draft_heading = env.get("DRAFT_HEADING", "AI draft").strip()
        self.draft_instructions = env.get("DRAFT_INSTRUCTIONS", "").strip()
        # triage of each new conversation of the draft inboxes; without categories it is off
        self.categories = parse_categories(env.get("TRIAGE_CATEGORIES", ""))
        self.triage_enabled = bool(self.categories) and self.drafts_enabled
        self.triage_fallback = env.get("TRIAGE_FALLBACK", "").strip().lower()
        self.triage_label = env.get("TRIAGE_LABEL", "ai-triaged").strip().lower()
        self.triage_heading = env.get("TRIAGE_HEADING", "AI triage:").strip()
        self.triage_instructions = env.get("TRIAGE_INSTRUCTIONS", "").strip()
        # the sweep of the account's open conversations; 0: off
        self.account_id = int(env.get("CHATWOOT_ACCOUNT_ID", "1"))
        self.sweep_seconds = int(env.get("SWEEP_SECONDS", "0"))
        self.sweep_max_age = int(float(env.get("SWEEP_MAX_AGE_HOURS", "24")) * 3600)
        # facts about the contact's tenant from the Kubernetes API; without the annotation off
        self.facts_annotation = env.get("FACTS_EMAIL_ANNOTATION", "").strip()
        self.facts_tenant_label = env.get("FACTS_TENANT_LABEL", "").strip()
        self.facts_velero_namespace = env.get("FACTS_VELERO_NAMESPACE", "").strip()
        self.facts_event_hours = float(env.get("FACTS_EVENT_HOURS", "24"))
        self.facts_ignore_reasons = {r.strip() for r in env.get("FACTS_IGNORE_EVENT_REASONS", "").split(",") if r.strip()}
        self.kube_url = env.get("KUBE_URL", "https://kubernetes.default.svc").rstrip("/")
        self.kube_token_file = env.get("KUBE_TOKEN_FILE", "")
        self.kube_ca_file = env.get("KUBE_CA_FILE", "")
        if self.triage_enabled:
            names = [c["name"] for c in self.categories]
            if self.triage_fallback not in names:
                raise ValueError(f"TRIAGE_FALLBACK {self.triage_fallback!r} is not one of the categories")
            if not CATEGORY.match(self.triage_label) or self.triage_label in names:
                raise ValueError(f"TRIAGE_LABEL {self.triage_label!r} is a label that is no category")
            if not self.triage_heading or self.triage_heading.startswith(self.draft_heading) \
                    or self.draft_heading.startswith(self.triage_heading):
                raise ValueError("TRIAGE_HEADING and DRAFT_HEADING must not start alike")
        if self.facts_annotation and not (self.facts_tenant_label and self.kube_token_file):
            raise ValueError("FACTS_TENANT_LABEL and KUBE_TOKEN_FILE are required with FACTS_EMAIL_ANNOTATION")
        if self.auth not in ("none", "serviceAccountToken", "clientAssertion", "clientSecret"):
            raise ValueError(f"A2A_AUTH {self.auth!r} is none, serviceAccountToken, clientAssertion or clientSecret")
        if self.auth in ("serviceAccountToken", "clientAssertion") and not self.token_file:
            raise ValueError("A2A_TOKEN_FILE is required with A2A_AUTH " + self.auth)
        if self.auth in ("clientAssertion", "clientSecret") and not (self.token_url and self.client_id):
            raise ValueError("A2A_TOKEN_URL and A2A_CLIENT_ID are required with A2A_AUTH " + self.auth)
        if self.auth == "clientSecret" and not self.client_secret_file:
            raise ValueError("A2A_CLIENT_SECRET_FILE is required with A2A_AUTH clientSecret")


def normalize(text):
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


# a category is a label the bridge sets; LABEL is any label of Chatwoot (letters, digits, - and _)
CATEGORY = re.compile(r"^[a-z0-9_-]{1,64}$")
LABEL = re.compile(r"^[\w-]{1,255}$")


def parse_categories(raw):
    """The categories of TRIAGE_CATEGORIES, a JSON list of {name, team, description}."""
    items = json.loads(raw) if raw.strip() else []
    if not isinstance(items, list):
        raise ValueError("TRIAGE_CATEGORIES is a JSON list")
    out = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("a category is an object with name, team and description")
        name = str(item.get("name") or "").strip().lower()
        if not CATEGORY.match(name):
            raise ValueError(f"category {name!r} is no label: a-z, 0-9, - and _")
        team = str(item.get("team") or "").strip().lower()
        out.append({"name": name, "team": team, "description": " ".join(str(item.get("description") or "").split())})
    if len({c["name"] for c in out}) != len(out):
        raise ValueError("TRIAGE_CATEGORIES names a category twice")
    return out


def verify(secret, timestamp, signature, body, now=None):
    """True when the signature is Chatwoot's HMAC of this body, made recently."""
    if not secret or not timestamp or not signature or not signature.startswith("sha256="):
        return False
    try:
        age = abs((now or time.time()) - int(timestamp))
    except ValueError:
        return False
    if age > SIGNATURE_AGE:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature[len("sha256="):])


def http_json(url, body=None, headers=None, timeout=30, form=False):
    data = None
    hdrs = {"Accept": "application/json"}
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def text_of_parts(parts):
    out = []
    for part in parts or []:
        if part.get("kind", part.get("type")) == "text" and part.get("text"):
            out.append(part["text"])
    return "\n".join(out).strip()


def answer_text(result):
    """The text of an A2A result: a Message, or a Task with artifacts or a status message."""
    if result.get("kind") == "message" or ("parts" in result and "status" not in result):
        return text_of_parts(result.get("parts"))
    texts = [text_of_parts(a.get("parts")) for a in result.get("artifacts") or []]
    text = "\n".join(t for t in texts if t).strip()
    return text or text_of_parts(((result.get("status") or {}).get("message") or {}).get("parts"))


class Credentials:
    """The bearer the agent gets: none, the projected token, or an access token of a client
    credentials grant (client assertion: the projected token; client secret: a mounted one)."""

    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.cached = ("", 0.0)

    def header(self):
        c = self.config
        if c.auth == "none":
            return {}
        if c.auth == "serviceAccountToken":
            return {"Authorization": "Bearer " + read_file(c.token_file)}
        with self.lock:
            token, expires = self.cached
            if not token or time.time() > expires - 60:
                form = {"grant_type": "client_credentials", "client_id": c.client_id}
                if c.auth == "clientSecret":
                    form["client_secret"] = read_file(c.client_secret_file)
                else:
                    form["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
                    form["client_assertion"] = read_file(c.token_file)
                if c.scope:
                    form["scope"] = c.scope
                got = http_json(c.token_url, form, form=True)
                token = got["access_token"]
                self.cached = (token, time.time() + int(got.get("expires_in", 300)))
        return {"Authorization": "Bearer " + token}


class Bot:
    def __init__(self, config, credentials=None):
        self.config = config
        self.credentials = credentials or Credentials(config)
        self.contexts = {}
        self.locks = {}
        self.locks_lock = threading.Lock()
        self.seen = []

    # --- Chatwoot ---------------------------------------------------------------------
    def chatwoot(self, path, body):
        return http_json(self.config.chatwoot_url + path, body,
                         {"api_access_token": read_file(self.config.bot_token_file)})

    def say(self, account, conversation, content):
        if content:
            self.chatwoot(f"/api/v1/accounts/{account}/conversations/{conversation}/messages",
                          {"content": content, "message_type": "outgoing"})

    def hand_off(self, account, conversation, content):
        try:
            self.say(account, conversation, content)
        finally:
            self.chatwoot(f"/api/v1/accounts/{account}/conversations/{conversation}/toggle_status",
                          {"status": "open"})

    def remember(self, account, conv, context_id):
        key = (account, conv["id"])
        if not context_id or self.contexts.get(key) == context_id:
            return
        self.contexts[key] = context_id
        attributes = dict(conv.get("custom_attributes") or {})
        attributes[self.config.context_attribute] = context_id
        try:
            self.chatwoot(f"/api/v1/accounts/{account}/conversations/{conv['id']}/custom_attributes",
                          {"custom_attributes": attributes})
        except (urllib.error.URLError, OSError) as e:
            LOG.warning("conversation %s: context not saved: %s", conv["id"], e)

    # --- A2A --------------------------------------------------------------------------
    def rpc(self, method, params):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}
        got = http_json(self.config.a2a_url, body, self.credentials.header(), timeout=self.config.a2a_timeout)
        if "error" in got:
            raise RuntimeError(f"{method}: {got['error'].get('message', got['error'])}")
        return got.get("result") or {}

    def ask(self, text, context_id, metadata):
        message = {"kind": "message", "role": "user", "messageId": str(uuid.uuid4()),
                   "parts": [{"kind": "text", "text": text}], "metadata": metadata}
        if context_id:
            message["contextId"] = context_id
        result = self.rpc("message/send", {"message": message, "configuration": {"blocking": True}})
        deadline = time.time() + self.config.a2a_timeout
        while result.get("kind") == "task" and (result.get("status") or {}).get("state") not in TERMINAL:
            if time.time() > deadline:
                raise TimeoutError("no answer within %ss" % self.config.a2a_timeout)
            time.sleep(3)
            result = self.rpc("tasks/get", {"id": result["id"]})
        state = (result.get("status") or {}).get("state")
        if state in ("failed", "canceled", "rejected", "auth-required"):
            raise RuntimeError("task " + state)
        return answer_text(result), result.get("contextId") or context_id

    # --- events -----------------------------------------------------------------------
    def wanted(self, event):
        """The incoming customer messages of conversations the bot holds."""
        conv = event.get("conversation") or {}
        return (event.get("event") == "message_created"
                and event.get("message_type") == "incoming"
                and not event.get("private")
                and conv.get("status") == "pending"
                and conv.get("id") is not None)

    def conversation_lock(self, key):
        with self.locks_lock:
            return self.locks.setdefault(key, threading.Lock())

    def handle(self, event):
        if not self.wanted(event):
            return
        message_id = event.get("id")
        if message_id in self.seen:
            return
        self.seen = (self.seen + [message_id])[-1000:]
        account = (event.get("account") or {}).get("id")
        conv = event["conversation"]
        with self.conversation_lock((account, conv["id"])):
            self.answer(account, conv, event)

    def answer(self, account, conv, event):
        c = self.config
        content = (event.get("content") or "").strip()
        names = [a.get("data_url", "").rsplit("/", 1)[-1] for a in event.get("attachments") or []]
        if names:
            content = (content + "\n" if content else "") + "[attachments: " + ", ".join(names) + "]"
        if not content:
            return
        if normalize(content) in c.keywords:
            LOG.info("conversation %s: handoff on request", conv["id"])
            self.hand_off(account, conv["id"], c.handoff_message)
            return
        context_id = (conv.get("custom_attributes") or {}).get(c.context_attribute) \
            or self.contexts.get((account, conv["id"]))
        text = content if context_id or not c.instructions else c.instructions + "\n\n" + content
        sender = event.get("sender") or {}
        metadata = {"chatwoot": {"account": account, "conversation": conv["id"],
                                 "inbox": (event.get("inbox") or {}).get("id"),
                                 "contact": {k: sender.get(k) for k in ("name", "email") if sender.get(k)}}}
        try:
            reply, context_id = self.ask(text, context_id, metadata)
        except Exception as e:  # any failure goes to staff
            LOG.warning("conversation %s: agent failed: %s", conv["id"], e)
            self.hand_off(account, conv["id"], c.failure_message)
            return
        self.remember(account, conv, context_id)
        if not reply or (c.marker and reply.strip() == c.marker):
            LOG.info("conversation %s: handoff by the agent", conv["id"])
            self.hand_off(account, conv["id"], c.handoff_message)
            return
        self.say(account, conv["id"], reply)
        LOG.info("conversation %s: answered", conv["id"])


PANEL_PROMPTS = {
    "summarize": "Summarise this customer conversation for the staff member who handles it: "
                 "the customer's question, what was answered, and what is still open. Be brief.",
    "suggest": "Write the next reply to the customer in this conversation, in the customer's "
               "language. Return the reply text only; a staff member edits and sends it.",
    "ask": "Answer the staff member's question about this customer conversation.",
}
MAX_TRANSCRIPT = 20000


def transcript(conversation):
    """The chat of a conversation as plain lines, newest last, at most MAX_TRANSCRIPT characters."""
    lines = []
    for m in conversation.get("messages") or []:
        content = (m.get("content") or "").strip()
        if not content or m.get("message_type") not in (0, 1, "incoming", "outgoing"):
            continue
        who = "customer" if m.get("message_type") in (0, "incoming") else "staff"
        if m.get("private"):
            who = "private note"
        lines.append(f"{who}: {content}")
    text = "\n".join(lines)
    return text[-MAX_TRANSCRIPT:]


def panel_request(bot, body, email):
    """The answer of the agent to one panel action; never sent to the customer."""
    action = body.get("action")
    if action not in PANEL_PROMPTS:
        raise ValueError("action is summarize, suggest or ask")
    conversation = body.get("conversation") or {}
    question = (body.get("question") or "").strip()[:4000]
    if action == "ask" and not question:
        raise ValueError("question is empty")
    text = PANEL_PROMPTS[action]
    if question:
        text += "\n\nQuestion: " + question
    text += "\n\nConversation:\n" + (transcript(conversation) or "(no messages)")
    metadata = {"chatwoot": {"panel": action, "conversation": conversation.get("id"), "staff": email}}
    reply, _ = bot.ask(text, None, metadata)
    return reply


class PrivateNotes:
    """The only writer of the draft and triage paths: private notes, the labels of a
    conversation and its team. It refuses anything else: no message the customer sees, no
    status, no person as assignee."""

    def __init__(self, base_url, token_file):
        self.base_url = base_url
        self.token_file = token_file

    def get(self, path):
        return http_json(self.base_url + path, None, {"api_access_token": read_file(self.token_file)})

    def send(self, path, body):
        return http_json(self.base_url + path, body, {"api_access_token": read_file(self.token_file)})

    def post(self, path, body):
        if not path.endswith("/messages") or body.get("private") is not True:
            raise PermissionError("the draft path writes private notes only")
        return self.send(path, body)

    def label(self, path, labels):
        """Sets the conversation's whole label list: Chatwoot replaces it."""
        if not path.endswith("/labels") or not isinstance(labels, list) \
                or not all(isinstance(x, str) and LABEL.match(x) for x in labels):
            raise PermissionError("the triage path sets a list of labels only")
        return self.send(path, {"labels": labels})

    def team(self, path, team_id):
        if not path.endswith("/assignments") or type(team_id) is not int or team_id <= 0:
            raise PermissionError("the triage path assigns a team only")
        return self.send(path, {"team_id": team_id})


def plain(value, joiner, limit):
    """Text as one plain line: no control or format characters, no angle brackets, capped."""
    text = value if isinstance(value, str) else ""
    text = "".join(" " if unicodedata.category(ch).startswith(("C", "Z")) else ch for ch in text)
    return joiner.join(text.replace("<", " ").replace(">", " ").split())[:limit]


def clean_contact(sender):
    """Name and email of the message's sender as plain one-line text: no control or format
    characters, no angle brackets, capped. The sender is whatever the customer's mail says."""
    sender = sender if isinstance(sender, dict) else {}
    contact = {"name": plain(sender.get("name"), " ", 100), "email": plain(sender.get("email"), "", 254)}
    return {k: v for k, v in contact.items() if v}


def contact_line(contact):
    """'Customer: name <email>' of a cleaned contact, or empty."""
    name, email = contact.get("name", ""), contact.get("email", "")
    if not (name or email):
        return ""
    return "Customer: " + (f"{name} <{email}>" if name and email else name or email)


DRAFT_PROMPT = ("Write the next reply of the team to the customer in this conversation, in the "
                "customer's language. Return the reply text only; a staff member checks, edits and "
                "sends it.")


def waiting(messages):
    """True when the newest message between customer and staff is the customer's: a template of
    Chatwoot itself, such as the widget's question for an email address, answers nobody."""
    public = [m for m in messages if not m.get("private")
              and m.get("message_type") in (0, 1, "incoming", "outgoing")]
    return bool(public) and public[-1].get("message_type") in (0, "incoming")


class Drafts:
    """Drafts the next reply to a customer message as a private note, for staff to send; with
    triage on, sorts each new conversation first."""

    def __init__(self, bot, notes=None, facts=None):
        self.bot = bot
        c = bot.config
        self.config = c
        self.notes = notes or PrivateNotes(c.chatwoot_url, c.draft_token_file)
        self.locks = {}
        self.locks_lock = threading.Lock()
        self.seen = []
        # conversation -> (failures, not before): the sweep waits longer after each failure
        self.retry = {}
        self.triage = Triage(self, facts) if c.triage_enabled else None

    def wanted(self, event):
        """Incoming customer messages of the listed inboxes."""
        conv = event.get("conversation") or {}
        inbox = (event.get("inbox") or {}).get("name") or ""
        inboxes = self.config.draft_inboxes
        return (event.get("event") == "message_created"
                and event.get("message_type") == "incoming"
                and not event.get("private")
                and conv.get("id") is not None
                and ("*" in inboxes or inbox in inboxes))

    def lock(self, key):
        with self.locks_lock:
            return self.locks.setdefault(key, threading.Lock())

    def handle(self, event):
        if not self.wanted(event):
            return
        message_id = event.get("id")
        if message_id in self.seen:
            return
        self.seen = (self.seen + [message_id])[-1000:]
        account = (event.get("account") or {}).get("id")
        conv = event["conversation"]["id"]
        with self.lock((account, conv)):
            try:
                if self.process(account, conv, message_id, event.get("sender")):
                    self.retry.pop((account, conv), None)
            except Exception as e:  # the sweep tries again
                LOG.warning("conversation %s: not done: %s", conv, e)

    def unfinished(self, conv):
        """A conversation of Chatwoot's list without the triage label, or whose newest message
        is the customer's: the sweep looks at it."""
        if self.triage and self.config.triage_label not in (conv.get("labels") or []):
            return True
        last = conv.get("last_non_activity_message") or {}
        # after a template the customer may still wait: process() reads the messages
        return last.get("message_type") in (0, 3, "incoming", "template") and not last.get("private")

    def sweep(self, account, conv):
        """The sweep's turn on one conversation: skipped while a webhook works on it, and for a
        while after a failure (1, 2, 4 ... minutes, at most an hour)."""
        lock = self.lock((account, conv))
        if not lock.acquire(blocking=False):
            return
        try:
            failures, until = self.retry.get((account, conv), (0, 0.0))
            if time.time() < until:
                return
            try:
                done = self.process(account, conv)
            except Exception as e:  # tried again after the pause
                LOG.warning("conversation %s: sweep failed: %s", conv, e)
                done = False
            if done:
                self.retry.pop((account, conv), None)
            else:
                self.retry[(account, conv)] = (failures + 1, time.time() + min(60 * 2 ** failures, 3600))
        finally:
            lock.release()

    def drafted(self, messages, newest):
        """True when a draft follows the customer's newest message."""
        heading = self.config.draft_heading
        return any(m.get("private") and (m.get("content") or "").startswith(heading)
                   and isinstance(m.get("id"), int) and m["id"] > newest for m in messages)

    def process(self, account, conv, message_id=None, sender=None):
        """The triage of a conversation without the triage label, then a draft for the newest
        customer message unless it has one. message_id is the webhook's message, drafted only
        while it is the newest; without it (the sweep) a draft is written only while the
        customer waits. False when the agent failed."""
        base = f"/api/v1/accounts/{int(account)}/conversations/{int(conv)}"
        messages = self.notes.get(base + "/messages").get("payload") or []
        incoming = [m.get("id") for m in messages if m.get("message_type") in (0, "incoming") and not m.get("private")]
        newest = incoming[-1] if incoming else None
        # a customer who writes again gets one draft, for the newest message
        if message_id is not None and newest != message_id:
            LOG.info("conversation %s: newer message than %s, no draft", conv, message_id)
            return True
        # the sweep leaves a conversation alone once staff answered the customer
        if newest is None or (message_id is None and not waiting(messages)):
            return True
        wanted = not self.drafted(messages, newest)
        done = True
        if self.triage:
            conversation = self.notes.get(base)
            if self.triage.needed(conversation):
                contact = clean_contact(sender or (conversation.get("meta") or {}).get("sender"))
                done = self.triage.run(account, conv, conversation, messages, contact)
                # the draft reads the triage note, with the tenant facts
                messages = None
        if not wanted:
            return done
        return self.draft(account, conv, newest, sender, messages) and done

    def draft(self, account, conv, message_id, sender=None, messages=None):
        """Writes the draft; False when the agent failed."""
        path = f"/api/v1/accounts/{int(account)}/conversations/{int(conv)}/messages"
        if messages is None:
            messages = self.notes.get(path).get("payload") or []
        incoming = [m.get("id") for m in messages if m.get("message_type") in (0, "incoming") and not m.get("private")]
        if incoming and incoming[-1] != message_id:
            LOG.info("conversation %s: newer message than %s, no draft", conv, message_id)
            return True
        heading = self.config.draft_heading
        earlier = [m for m in messages if not (m.get("private") and (m.get("content") or "").startswith(heading))]
        # the sender lets the agent look the customer up (Chatwoot's contact, from the mail sender)
        contact = clean_contact(sender)
        customer = contact_line(contact)
        text = (self.config.draft_instructions or DRAFT_PROMPT) + \
            ("\n\n" + customer if customer else "") + "\n\nConversation:\n" + \
            (transcript({"messages": earlier}) or "(no messages)")
        metadata = {"chatwoot": {"draft": True, "account": account, "conversation": conv,
                                 "contact": contact}}
        try:
            reply, _ = self.bot.ask(text, None, metadata)
        except Exception as e:  # staff answer without a draft
            LOG.warning("conversation %s: no draft, agent failed: %s", conv, e)
            return False
        if not reply.strip():
            LOG.info("conversation %s: agent drafted nothing", conv)
            return True
        self.notes.post(path, {"content": heading + "\n\n" + reply.strip(),
                               "message_type": "outgoing", "private": True})
        LOG.info("conversation %s: draft written", conv)
        return True


TRIAGE_PROMPT = ("Sort this customer conversation for the team. Answer with exactly these two lines "
                 "and nothing else:\nCATEGORY: <one name of the list below>\nSUMMARY: <one or two "
                 "sentences in English for the staff member who takes over: what the customer asks or "
                 "reports, and what is still open>")
MAX_SUMMARY = 600


def parse_triage(answer, names):
    """The category and summary in the agent's answer; the category is None when the answer
    names none of names."""
    category, summary, next_summary = None, "", False
    for line in (answer or "").splitlines():
        line = line.replace("*", "").replace("`", "").strip()
        if next_summary and line:
            summary, next_summary = plain(line, " ", MAX_SUMMARY), False
            continue
        key, sep, value = line.partition(":")
        key = key.strip().lower()
        if not sep:
            continue
        if key == "category" and category is None:
            word = re.match(r"\s*([\w-]+)", value)
            if word and word.group(1).lower() in names:
                category = word.group(1).lower()
        elif key == "summary" and not summary:
            summary = plain(value, " ", MAX_SUMMARY)
            next_summary = not summary
    return category, summary


class Triage:
    """Sorts a new conversation: a category label, the category's team and a private note with
    a summary and the tenant facts. The triage label is set last: it marks the work done."""

    def __init__(self, drafts, facts=None):
        self.bot = drafts.bot
        self.config = drafts.config
        self.notes = drafts.notes
        self.facts = facts

    def needed(self, conversation):
        return self.config.triage_label not in (conversation.get("labels") or [])

    def ask(self, account, conv, messages, contact, facts):
        """(category or None, summary, failure) of the agent's answer."""
        c = self.config
        own = (c.draft_heading, c.triage_heading)
        earlier = [m for m in messages if not (m.get("private") and (m.get("content") or "").startswith(own))]
        kinds = "\n".join("- " + k["name"] + (": " + k["description"] if k["description"] else "") for k in c.categories)
        customer = contact_line(contact)
        text = (c.triage_instructions or TRIAGE_PROMPT) + "\n\nCategories:\n" + kinds + \
            ("\n\n" + customer if customer else "") + ("\n\n" + facts if facts else "") + \
            "\n\nConversation:\n" + (transcript({"messages": earlier}) or "(no messages)")
        metadata = {"chatwoot": {"triage": True, "account": account, "conversation": conv, "contact": contact}}
        try:
            answer, _ = self.bot.ask(text, None, metadata)
        except Exception as e:  # the fallback team takes it
            LOG.warning("conversation %s: no triage, agent failed: %s", conv, e)
            return None, "", "the agent did not answer"
        category, summary = parse_triage(answer, [k["name"] for k in c.categories])
        return category, summary, "" if category else "the agent named no category"

    def route(self, account, conv, conversation, team):
        """Gives the conversation to the team; a team or assignee it already has stays."""
        if not team:
            return "no team"
        meta = conversation.get("meta") or {}
        if meta.get("team"):
            return "team kept: " + plain((meta["team"] or {}).get("name"), " ", 60)
        if meta.get("assignee"):
            return "assignee kept"
        teams = self.notes.get(f"/api/v1/accounts/{int(account)}/teams")
        teams = teams if isinstance(teams, list) else teams.get("payload") or []
        ids = [t.get("id") for t in teams if isinstance(t, dict) and str(t.get("name", "")).lower() == team]
        if not ids or type(ids[0]) is not int:
            return f"no team {team} in Chatwoot"
        try:
            self.notes.team(f"/api/v1/accounts/{int(account)}/conversations/{int(conv)}/assignments", ids[0])
        except urllib.error.HTTPError as e:
            LOG.warning("conversation %s: team %s not set: %s", conv, team, e)
            return f"team {team} not set (HTTP {e.code})"
        return f"to team {team}"

    def run(self, account, conv, conversation, messages, contact):
        c = self.config
        facts = self.facts.collect(contact.get("email")) if self.facts else ""
        category, summary, failure = self.ask(account, conv, messages, contact, facts)
        category = category or c.triage_fallback
        kind = next(k for k in c.categories if k["name"] == category)
        route = self.route(account, conv, conversation, kind["team"])
        base = f"/api/v1/accounts/{int(account)}/conversations/{int(conv)}"
        head = f"{c.triage_heading} {category}" + (f" (fallback: {failure})" if failure else "") + ", " + route
        self.notes.post(base + "/messages", {
            "content": head + "\nSummary: " + (summary or "none") + ("\n\n" + facts if facts else ""),
            "message_type": "outgoing", "private": True})
        labels = [x for x in conversation.get("labels") or [] if isinstance(x, str) and LABEL.match(x)]
        labels += [x for x in (category, c.triage_label) if x not in labels]
        self.notes.label(base + "/labels", labels)
        LOG.info("conversation %s: triaged as %s, %s", conv, category, route)
        return not failure.startswith("the agent did not")


MAX_FACTS = 4000
FACT_NAMESPACES = 8
# one namespace's line, so the cap of MAX_FACTS keeps them all
MAX_NAMESPACE_FACTS = 400


def parse_time(value):
    """Seconds since the epoch of an RFC 3339 time, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def ago(then, now):
    seconds = max(0, now - then)
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 2 * 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} days ago"


def more(items, limit):
    """The first limit items joined, and how many are left out."""
    return ", ".join(items[:limit]) + (f" and {len(items) - limit} more" if len(items) > limit else "")


class TenantFacts:
    """Facts about the tenant of a contact's email, from the Kubernetes API of this cluster. The
    tenant comes from the contact's email alone, never from what the customer writes; no Secret
    and no ConfigMap is read."""

    def __init__(self, config, get=None):
        self.config = config
        self.get = get or self.kube
        self.cache = ([], 0.0)
        self.lock = threading.Lock()

    def kube(self, path):
        c = self.config
        context = ssl.create_default_context(cafile=c.kube_ca_file or None)
        req = urllib.request.Request(c.kube_url + path, headers={
            "Accept": "application/json", "Authorization": "Bearer " + read_file(c.kube_token_file)})
        with urllib.request.urlopen(req, timeout=10, context=context) as resp:
            return json.loads(resp.read())

    def namespaces(self):
        """Every namespace of the cluster, read at most every 5 minutes."""
        with self.lock:
            items, fetched = self.cache
            if time.time() - fetched > 300:
                items = self.get("/api/v1/namespaces").get("items") or []
                self.cache = (items, time.time())
            return items

    def tenants(self, email):
        """{tenant: its namespaces} of the namespaces whose annotation names this email."""
        c = self.config
        items = self.namespaces()
        found = {}
        for ns in items:
            meta = ns.get("metadata") or {}
            owners = str((meta.get("annotations") or {}).get(c.facts_annotation) or "")
            tenant = (meta.get("labels") or {}).get(c.facts_tenant_label)
            if tenant and email in {o.strip().lower() for o in owners.split(",")}:
                found[tenant] = []
        for ns in items:
            meta = ns.get("metadata") or {}
            tenant = (meta.get("labels") or {}).get(c.facts_tenant_label)
            if tenant in found:
                found[tenant].append(meta.get("name") or "")
        return {t: sorted(n) for t, n in found.items()}

    def collect(self, email):
        """The facts as text for the triage note and the draft, or empty without an email."""
        email = (email or "").strip().lower()
        if "@" not in email:
            return ""
        head = f"Tenant facts for {email} (read from the cluster by the bridge; the sender's address is not verified):"
        try:
            tenants = self.tenants(email)
        except Exception as e:  # facts help the triage, they never stop it
            LOG.warning("tenant facts: namespaces not read: %s", e)
            return head + "\nnot available: the cluster did not answer"
        if not tenants:
            return head + "\nno tenant has this email"
        if len(tenants) > 1:
            return head + "\nthe email belongs to several tenants, nothing read: " + more(sorted(tenants), 10)
        (tenant, names), = tenants.items()
        now = time.time()
        lines = [head, f"tenant {plain(tenant, '', 63)}, namespaces " + more(names, 20)]
        # backups first: the cap cuts from the end
        lines += self.backups(names, now)
        for ns in names[:FACT_NAMESPACES]:
            parts = []
            for read in (self.pods, self.cronjobs, self.claims, self.events):
                try:
                    parts += read(ns, now)
                except Exception as e:  # one part not readable, for example without the right to list it
                    LOG.info("tenant facts: %s of %s not read: %s", read.__name__, ns, e)
                    parts.append(read.__name__ + " not readable")
            line = f"- {ns}: " + "; ".join(parts)
            lines.append(line if len(line) <= MAX_NAMESPACE_FACTS else line[:MAX_NAMESPACE_FACTS - 4] + " ...")
        if len(names) > FACT_NAMESPACES:
            lines.append(f"- {len(names) - FACT_NAMESPACES} more namespaces not read")
        return "\n".join(lines)[:MAX_FACTS]

    def pods(self, ns, now):
        items = self.get(f"/api/v1/namespaces/{urllib.parse.quote(ns)}/pods").get("items") or []
        live = [p for p in items if (p.get("status") or {}).get("phase") != "Succeeded"]
        bad = []
        for p in live:
            status = p.get("status") or {}
            containers = status.get("containerStatuses") or []
            if status.get("phase") == "Running" and containers and all(x.get("ready") for x in containers):
                continue
            states = [x.get("state") or {} for x in containers]
            reasons = [(s.get("waiting") or {}).get("reason") or (s.get("terminated") or {}).get("reason") for s in states]
            reason = next((r for r in reasons if r), status.get("reason") or status.get("phase") or "unknown")
            restarts = sum(int(x.get("restartCount") or 0) for x in containers)
            name = plain((p.get("metadata") or {}).get("name"), "", 80)
            bad.append(f"{name} {plain(reason, ' ', 40)}" + (f" ({restarts} restarts)" if restarts else ""))
        out = [f"{len(live) - len(bad)} of {len(live)} pods ready"]
        if bad:
            out.append("not ready: " + more(bad, 5))
        return out

    def cronjobs(self, ns, now):
        items = self.get(f"/apis/batch/v1/namespaces/{urllib.parse.quote(ns)}/cronjobs").get("items") or []
        out = []
        for j in items[:5]:
            spec, status = j.get("spec") or {}, j.get("status") or {}
            last = parse_time(status.get("lastSuccessfulTime"))
            out.append(f"CronJob {plain((j.get('metadata') or {}).get('name'), '', 80)} "
                       f"({plain(spec.get('schedule'), ' ', 40)})" + (" suspended," if spec.get("suspend") else "")
                       + (f" last success {ago(last, now)}" if last else " no success yet"))
        if len(items) > 5:
            out.append(f"{len(items) - 5} more CronJobs")
        return out

    def claims(self, ns, now):
        items = self.get(f"/api/v1/namespaces/{urllib.parse.quote(ns)}/persistentvolumeclaims").get("items") or []
        unbound = [f"{plain((c.get('metadata') or {}).get('name'), '', 80)} {plain((c.get('status') or {}).get('phase'), '', 20)}"
                   for c in items if (c.get("status") or {}).get("phase") != "Bound"]
        return ["volume claims not bound: " + more(unbound, 5)] if unbound else []

    def events(self, ns, now):
        path = f"/api/v1/namespaces/{urllib.parse.quote(ns)}/events?fieldSelector=" + urllib.parse.quote("type=Warning")
        # the same warning about the same object counts once, with its newest time
        seen = {}
        for e in self.get(path).get("items") or []:
            if e.get("reason") in self.config.facts_ignore_reasons:
                continue
            when = parse_time(e.get("lastTimestamp")) or parse_time(e.get("eventTime")) \
                or parse_time((e.get("metadata") or {}).get("creationTimestamp"))
            if not when or now - when > self.config.facts_event_hours * 3600:
                continue
            obj = e.get("involvedObject") or {}
            text = (f"warning {plain(e.get('reason'), ' ', 40)} {plain(obj.get('kind'), '', 30)}/"
                    f"{plain(obj.get('name'), '', 80)}: {plain(e.get('message'), ' ', 160)}")
            last, count = seen.get(text, (0, 0))
            seen[text] = (max(last, when), count + int(e.get("count") or 1))
        recent = sorted(seen.items(), key=lambda x: -x[1][0])
        out = [f"{text} ({count}x, {ago(when, now)})" for text, (when, count) in recent[:4]]
        if len(recent) > 4:
            out.append(f"{len(recent) - 4} more warnings")
        return out

    def backups(self, names, now):
        """The Velero schedules that cover the tenant's namespaces, with their newest backup."""
        velero = self.config.facts_velero_namespace
        if not velero:
            return []
        base = f"/apis/velero.io/v1/namespaces/{urllib.parse.quote(velero)}"
        try:
            schedules = self.get(base + "/schedules").get("items") or []
        except Exception as e:  # no Velero, or no right to read it
            LOG.info("tenant facts: backup schedules not read: %s", e)
            return ["- backups not readable"]
        lines = []
        for s in schedules:
            spec = s.get("spec") or {}
            covered = set((spec.get("template") or {}).get("includedNamespaces") or []) & set(names)
            if not covered:
                continue
            name = (s.get("metadata") or {}).get("name") or ""
            line = f"- backup schedule {plain(name, '', 80)} ({plain(spec.get('schedule'), ' ', 40)})" \
                + (" paused" if spec.get("paused") else "")
            try:
                got = self.get(base + "/backups?labelSelector=" + urllib.parse.quote("velero.io/schedule-name=" + name))
                runs = [(parse_time((b.get("metadata") or {}).get("creationTimestamp")) or 0, b) for b in got.get("items") or []]
            except Exception as e:  # the schedule still shows
                LOG.info("tenant facts: backups of %s not read: %s", name, e)
                runs = None
            if runs is None:
                line += ": backups not readable"
            elif not runs:
                line += ": no backup yet"
            else:
                created, newest = max(runs, key=lambda r: r[0])
                status = newest.get("status") or {}
                done = parse_time(status.get("completionTimestamp")) or created
                line += f": newest backup {plain(status.get('phase') or 'unknown', '', 30)}, {ago(done, now)}"
            lines.append(line)
            if len(lines) == 8:
                break
        return lines or ["- no backup schedule covers these namespaces"]


class Sweeper:
    """Looks at the open conversations of the draft inboxes every SWEEP_SECONDS and does what a
    webhook missed: a restart, a lost delivery, an agent that was down."""

    def __init__(self, drafts):
        self.drafts = drafts
        self.config = drafts.config
        self.notes = drafts.notes

    def run_once(self):
        c = self.config
        account = c.account_id
        inboxes = self.notes.get(f"/api/v1/accounts/{account}/inboxes").get("payload") or []
        wanted = {i.get("id") for i in inboxes if "*" in c.draft_inboxes or i.get("name") in c.draft_inboxes}
        # updated_within lists every open conversation changed since then, unpaged
        got = self.notes.get(f"/api/v1/accounts/{account}/conversations?status=open&updated_within={c.sweep_max_age}")
        now = time.time()
        for conv in (got.get("data") or {}).get("payload") or []:
            if conv.get("inbox_id") in wanted and isinstance(conv.get("id"), int) \
                    and now - (conv.get("last_activity_at") or 0) <= c.sweep_max_age and self.drafts.unfinished(conv):
                self.drafts.sweep(account, conv["id"])

    def loop(self):
        while True:
            time.sleep(self.config.sweep_seconds)
            try:
                self.run_once()
            except Exception as e:  # the thread must live on for the next round
                LOG.warning("sweep failed: %s", e)


def b64url(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# DER prefix of a SHA-256 DigestInfo (PKCS#1 v1.5 signatures)
SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def rsa_verify(n, e, message, signature):
    """RS256 (RSASSA-PKCS1-v1_5 with SHA-256) with the standard library alone."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    em = pow(int.from_bytes(signature, "big"), e, n).to_bytes(k, "big")
    digest = SHA256_PREFIX + hashlib.sha256(message).digest()
    expected = b"\x00\x01" + b"\xff" * (k - len(digest) - 3) + b"\x00" + digest
    return hmac.compare_digest(em, expected)


class StaffTokens:
    """Checks a staff member's OIDC access token: signature, issuer, expiry, client, group."""

    def __init__(self, issuer, clients, groups, groups_claim="groups", fetch=None):
        self.issuer = issuer.rstrip("/")
        self.clients = {c for c in clients if c}
        self.groups = {g for g in groups if g}
        self.groups_claim = groups_claim
        self.fetch = fetch or (lambda url: http_json(url, timeout=10))
        self.keys = {}
        self.fetched = 0.0
        self.lock = threading.Lock()

    def key(self, kid):
        with self.lock:
            age = time.time() - self.fetched
            # every 10 minutes, and for an unknown key at most every 30 seconds
            if not self.fetched or age > 600 or (kid not in self.keys and age > 30):
                jwks_uri = self.fetch(self.issuer + "/.well-known/openid-configuration")["jwks_uri"]
                self.keys = {k["kid"]: k for k in self.fetch(jwks_uri).get("keys", [])
                             if k.get("kty") == "RSA" and k.get("use", "sig") == "sig"}
                self.fetched = time.time()
            return self.keys.get(kid)

    def check(self, token, now=None):
        """The claims of a valid token; ValueError otherwise."""
        try:
            head64, body64, sig64 = token.split(".")
            head, claims = json.loads(b64url(head64)), json.loads(b64url(body64))
        except (ValueError, TypeError):
            raise ValueError("not a JWT")
        if head.get("alg") != "RS256":
            raise ValueError("algorithm is not RS256")
        jwk = self.key(head.get("kid"))
        if not jwk or not rsa_verify(int.from_bytes(b64url(jwk["n"]), "big"), int.from_bytes(b64url(jwk["e"]), "big"),
                                     (head64 + "." + body64).encode(), b64url(sig64)):
            raise ValueError("bad signature")
        now = now or time.time()
        if claims.get("iss") != self.issuer:
            raise ValueError("other issuer")
        if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - 60:
            raise ValueError("expired")
        if self.clients and claims.get("azp") not in self.clients:
            raise ValueError("other client")
        groups = claims.get(self.groups_claim) or []
        if self.groups and not self.groups & {str(g).lstrip("/") for g in groups}:
            raise ValueError("not in an allowed group")
        return claims


def forward_panel(url, body, token):
    """Hands a panel request to a bridge elsewhere, with the staff member's own token."""
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=330) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def make_panel_handler(bot, page, forward_url=""):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body=b"", content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "frame-ancestors 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/healthz":
                return self.send(200)
            if self.path.split("?")[0] in ("/agent-panel", "/agent-panel/"):
                return self.send(200, page, "text/html; charset=utf-8")
            self.send(404)

        def do_POST(self):
            if self.path != "/agent-panel/api":
                return self.send(404)
            # oauth2-proxy sets it after the staff login; only the gate reaches this port
            email = self.headers.get("X-Forwarded-Email", "")
            if not email:
                return self.send(401)
            origin = self.headers.get("Origin")
            if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("X-Forwarded-Host", self.headers.get("Host")):
                return self.send(403)
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self.send(413 if length > MAX_BODY else 400)
            if forward_url:
                # the bridge next to the agent checks the staff member's token itself
                token = self.headers.get("X-Forwarded-Access-Token", "")
                if not token:
                    return self.send(401)
                try:
                    status, out = forward_panel(forward_url, self.rfile.read(length), token)
                except (urllib.error.URLError, OSError) as e:
                    LOG.warning("panel %s: bridge unreachable: %s", email, e)
                    return self.send(502, json.dumps({"error": "the agent did not answer"}).encode())
                return self.send(status, out)
            try:
                body = json.loads(self.rfile.read(length))
                reply = panel_request(bot, body, email)
            except ValueError as e:
                return self.send(400, json.dumps({"error": str(e)}).encode())
            except Exception as e:  # the agent failed
                LOG.warning("panel %s: agent failed: %s", email, e)
                return self.send(502, json.dumps({"error": "the agent did not answer"}).encode())
            LOG.info("panel %s: %s", email, body.get("action"))
            self.send(200, json.dumps({"text": reply}).encode())

    return Handler


def make_handler(bot, pool, staff=None, drafts=None):
    """Webhooks of the bot and, with drafts, of the draft path; with staff (StaffTokens) also
    panel requests from elsewhere."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body=b""):
            self.send_response(status)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(200 if self.path == "/healthz" else 404)

        def panel(self, raw):
            auth = self.headers.get("Authorization", "")
            try:
                claims = staff.check(auth[len("Bearer "):] if auth.startswith("Bearer ") else "")
            except ValueError as e:
                LOG.warning("panel refused: %s", e)
                return self.reply(401)
            email = claims.get("email") or claims.get("preferred_username") or claims.get("sub", "")
            try:
                body = json.loads(raw)
                text = panel_request(bot, body, email)
            except ValueError as e:
                return self.reply(400, json.dumps({"error": str(e)}).encode())
            except Exception as e:  # the agent failed
                LOG.warning("panel %s: agent failed: %s", email, e)
                return self.reply(502, json.dumps({"error": "the agent did not answer"}).encode())
            LOG.info("panel %s: %s", email, body.get("action"))
            self.reply(200, json.dumps({"text": text}).encode())

        def do_POST(self):
            served = {"/webhook": bot.config.bot_enabled, "/agent-panel/api": staff is not None,
                      "/draft": drafts is not None}
            if not served.get(self.path):
                return self.reply(404)
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self.reply(413 if length > MAX_BODY else 400)
            body = self.rfile.read(length)
            if self.path == "/agent-panel/api":
                return self.panel(body)
            # each webhook has its own secret: the bot's cannot sign a draft request
            secret_file = bot.config.draft_secret_file if self.path == "/draft" else bot.config.webhook_secret_file
            if not verify(read_file(secret_file), self.headers.get("X-Chatwoot-Timestamp"),
                          self.headers.get("X-Chatwoot-Signature"), body):
                LOG.warning("%s refused: bad signature", self.path)
                return self.reply(401)
            try:
                event = json.loads(body)
            except ValueError:
                return self.reply(400)
            # Chatwoot waits a few seconds only: answer at once, work afterwards
            pool.submit(drafts.handle if self.path == "/draft" else bot.handle, event)
            self.reply(200)

    return Handler


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # as process 1 Python ignores SIGTERM: stop at once when the pod stops
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    env = os.environ
    forward_url = env.get("PANEL_FORWARD_URL", "")
    # forwarding the panel only: no agent of its own
    bot = None if forward_url and not env.get("A2A_URL") else Bot(Config())
    if env.get("PANEL_PORT"):
        with open(env["PANEL_PAGE"], "rb") as f:
            page = f.read()
        # 127.0.0.1: next to oauth2-proxy in its pod, so only a logged-in request arrives
        panel = http.server.ThreadingHTTPServer((env.get("PANEL_HOST", "127.0.0.1"), int(env["PANEL_PORT"])),
                                                make_panel_handler(bot, page, forward_url))
        threading.Thread(target=panel.serve_forever, daemon=True).start()
        LOG.info("agent panel listening on %s%s", env["PANEL_PORT"], " for " + forward_url if forward_url else "")
    staff = None
    if env.get("STAFF_ISSUER"):
        staff = StaffTokens(env["STAFF_ISSUER"], env.get("STAFF_CLIENTS", "").split(","),
                            env.get("STAFF_GROUPS", "").split(","), env.get("STAFF_GROUPS_CLAIM", "groups"))
    facts = TenantFacts(bot.config) if bot and bot.config.facts_annotation else None
    drafts = Drafts(bot, facts=facts) if bot and bot.config.drafts_enabled else None
    sweep = bool(drafts and bot.config.sweep_seconds > 0)
    if sweep:
        threading.Thread(target=Sweeper(drafts).loop, daemon=True).start()
    if bot and (bot.config.bot_enabled or staff or drafts):
        pool = ThreadPoolExecutor(max_workers=8)
        server = http.server.ThreadingHTTPServer(("0.0.0.0", bot.config.port), make_handler(bot, pool, staff, drafts))
        LOG.info("agent bridge listening on %s, agent %s, auth %s, bot %s, panel %s, drafts %s, triage %s, "
                 "facts %s, sweep %s", bot.config.port, bot.config.a2a_url, bot.config.auth, bot.config.bot_enabled,
                 staff is not None, drafts is not None, bool(drafts and drafts.triage), facts is not None, sweep)
        server.serve_forever()
    else:
        threading.Event().wait()


if __name__ == "__main__":
    main()
