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

Standard library only; prints no credential.
"""
import base64
import hashlib
import hmac
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
    """The only writer of the draft path: posts private notes and refuses anything else."""

    def __init__(self, base_url, token_file):
        self.base_url = base_url
        self.token_file = token_file

    def get(self, path):
        return http_json(self.base_url + path, None, {"api_access_token": read_file(self.token_file)})

    def post(self, path, body):
        if not path.endswith("/messages") or body.get("private") is not True:
            raise PermissionError("the draft path writes private notes only")
        return http_json(self.base_url + path, body, {"api_access_token": read_file(self.token_file)})


def contact_line(sender):
    """'Customer: name <email>' of the message's sender, one line, or empty."""
    sender = sender or {}
    name = " ".join(str(sender.get("name") or "").split())[:100]
    email = "".join(str(sender.get("email") or "").split())[:254]
    if not (name or email):
        return ""
    return "Customer: " + (f"{name} <{email}>" if name and email else name or email)


DRAFT_PROMPT = ("Write the next reply of the team to the customer in this conversation, in the "
                "customer's language. Return the reply text only; a staff member checks, edits and "
                "sends it.")


class Drafts:
    """Drafts the next reply to a customer message as a private note, for staff to send."""

    def __init__(self, bot, notes=None):
        self.bot = bot
        c = bot.config
        self.config = c
        self.notes = notes or PrivateNotes(c.chatwoot_url, c.draft_token_file)
        self.locks = {}
        self.locks_lock = threading.Lock()
        self.seen = []

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
            self.draft(account, conv, message_id, event.get("sender"))

    def draft(self, account, conv, message_id, sender=None):
        path = f"/api/v1/accounts/{account}/conversations/{conv}/messages"
        messages = self.notes.get(path).get("payload") or []
        incoming = [m.get("id") for m in messages if m.get("message_type") in (0, "incoming") and not m.get("private")]
        # a customer who writes again gets one draft, for the newest message
        if incoming and incoming[-1] != message_id:
            LOG.info("conversation %s: newer message than %s, no draft", conv, message_id)
            return
        heading = self.config.draft_heading
        earlier = [m for m in messages if not (m.get("private") and (m.get("content") or "").startswith(heading))]
        # the sender lets the agent look the customer up (Chatwoot's contact, from the mail sender)
        customer = contact_line(sender)
        text = (self.config.draft_instructions or DRAFT_PROMPT) + \
            ("\n\n" + customer if customer else "") + "\n\nConversation:\n" + \
            (transcript({"messages": earlier}) or "(no messages)")
        contact = {k: (sender or {}).get(k) for k in ("name", "email") if (sender or {}).get(k)}
        metadata = {"chatwoot": {"draft": True, "account": account, "conversation": conv,
                                 "contact": contact}}
        try:
            reply, _ = self.bot.ask(text, None, metadata)
        except Exception as e:  # staff answer without a draft
            LOG.warning("conversation %s: no draft, agent failed: %s", conv, e)
            return
        if not reply.strip():
            LOG.info("conversation %s: agent drafted nothing", conv)
            return
        self.notes.post(path, {"content": heading + "\n\n" + reply.strip(),
                               "message_type": "outgoing", "private": True})
        LOG.info("conversation %s: draft written", conv)


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
    drafts = Drafts(bot) if bot and bot.config.drafts_enabled else None
    if bot and (bot.config.bot_enabled or staff or drafts):
        pool = ThreadPoolExecutor(max_workers=8)
        server = http.server.ThreadingHTTPServer(("0.0.0.0", bot.config.port), make_handler(bot, pool, staff, drafts))
        LOG.info("agent bridge listening on %s, agent %s, auth %s, bot %s, panel %s, drafts %s", bot.config.port,
                 bot.config.a2a_url, bot.config.auth, bot.config.bot_enabled, staff is not None, drafts is not None)
        server.serve_forever()
    else:
        threading.Event().wait()


if __name__ == "__main__":
    main()
