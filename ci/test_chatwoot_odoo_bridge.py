"""Tests of charts/chatwoot/files/odoo_bridge.py against a fake Odoo JSON-2 API and a fake Chatwoot.

python3 -m unittest ci/test_chatwoot_odoo_bridge.py
"""
import hashlib
import hmac
import http.client
import http.server
import importlib.util
import json
import logging
import pathlib
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse

# the module lives in the chart: a __pycache__ there would be packaged with it
sys.dont_write_bytecode = True
ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("odoo_bridge", ROOT / "charts/chatwoot/files/odoo_bridge.py")
odoo_bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(odoo_bridge)

ODOO_KEY = "odoo-key-" + "k" * 40
TOKEN = "bridge-" + "t" * 41
EVENT_SECRET = "event-" + "e" * 42
INBOX_SECRET = "inbox-" + "i" * 42
SUPPORT, SALES, WEBSITE = 1, 2, 3


def like(value, pattern):
    """SQL ILIKE with backslash escapes, as PostgreSQL runs Odoo's =ilike."""
    rx, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            rx += re.escape(pattern[i + 1])
            i += 2
            continue
        rx += ".*" if c == "%" else "." if c == "_" else re.escape(c)
        i += 1
    return re.fullmatch(rx, value or "", re.IGNORECASE | re.DOTALL) is not None


def match(row, domain):
    for field, op, value in domain:
        have = row.get(field)
        if isinstance(have, list) and have and isinstance(have[0], int) and op != "in":
            have = have[0]
        if op == "=" and have != value:
            return False
        if op == "=ilike" and not like(have, value):
            return False
        if op == "in" and not set(have if isinstance(have, list) else [have]) & set(value):
            return False
    return True


class Fake(http.server.BaseHTTPRequestHandler):
    """Odoo JSON-2 and the Chatwoot Application API, as far as the bridge uses them."""

    state = None

    def log_message(self, *args):
        pass

    def _json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        return json.loads(raw) if raw else None

    def do_GET(self):
        self._chatwoot("GET", None)

    def do_POST(self):
        body = self._body()
        if self.path.startswith("/json/2/"):
            return self._odoo(body)
        self._chatwoot("POST", body)

    # ------------------------------------------------------------ Odoo

    def _odoo(self, kw):
        s = self.state
        if self.headers.get("Authorization") != "bearer " + ODOO_KEY or self.headers.get("X-Odoo-Database") != "odoo":
            return self._json(401, {"name": "werkzeug.exceptions.Unauthorized"})
        _, _, _, model, method = self.path.split("/")
        s["odoo_calls"].append((model, method, kw))
        if s.get("odoo_redirect"):
            self.send_response(302)
            self.send_header("Location", self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if method == "message_post" and s.get("odoo_fail", 0) > 0:
            s["odoo_fail"] -= 1
            return self._json(500, {"name": "builtins.RuntimeError"})
        if s.get("odoo_delay"):
            time.sleep(s["odoo_delay"])
        table = s["odoo"].setdefault(model, {})
        if method == "read":
            rows = []
            for rid in kw["ids"]:
                if rid not in table:
                    return self._json(404, {"name": "odoo.exceptions.MissingError"})
                rows.append({"id": rid, **{f: table[rid].get(f, False) for f in kw["fields"]}})
            return self._json(200, rows)
        if method == "search_read":
            rows = [{"id": rid, **{f: r.get(f, False) for f in kw["fields"]}}
                    for rid, r in sorted(table.items()) if match(r, kw["domain"])]
            return self._json(200, rows[: kw.get("limit") or None])
        if method == "write":
            for rid in kw["ids"]:
                table[rid].update(kw["vals"])
            return self._json(200, True)
        if model == "res.partner" and method == "find_or_create":
            email = re.search(r"<([^>]+)>", kw["email"])
            email = email.group(1) if email else kw["email"]
            for pid, row in table.items():
                if row.get("email") == email:
                    return self._json(200, [pid])
            pid = max(table, default=500) + 1
            table[pid] = {"email": email, "name": kw["email"]}
            return self._json(200, [pid])
        if method == "message_post":
            messages = s["odoo"].setdefault("mail.message", {})
            if any(m.get("x_chatwoot_message_id") == kw.get("x_chatwoot_message_id") for m in messages.values()):
                return self._json(422, {"name": "psycopg2.errors.UniqueViolation"})
            mid = max(messages, default=100) + 1
            messages[mid] = {"model": model, "res_id": kw["ids"][0], **{k: v for k, v in kw.items() if k != "ids"}}
            return self._json(200, mid)
        return self._json(404, {"name": "unknown method"})

    # ------------------------------------------------------------ Chatwoot

    def _chatwoot(self, method, body):
        s = self.state
        url = urllib.parse.urlsplit(self.path)
        path, query = url.path, urllib.parse.parse_qs(url.query)
        prefix = "/api/v1/accounts/1"
        if not path.startswith(prefix) or self.headers.get("api_access_token") != TOKEN:
            return self._json(401, {})
        path = path[len(prefix):]
        s["chatwoot_calls"].append((method, path, body))
        cw = s["chatwoot"]
        if s.get("chatwoot_404") and path.startswith("/contacts"):
            return self._json(404, {})
        if method == "GET" and path == "/inboxes":
            return self._json(200, {"payload": [{"id": SUPPORT, "name": "Support"}, {"id": SALES, "name": "Sales"},
                                                {"id": WEBSITE, "name": "Website"}]})
        if method == "GET" and path == "/contacts/search":
            # as Chatwoot: substring on name, email and identifier, 15 per page
            q = query["q"][0].lower()
            found = [c for c in cw["contacts"].values()
                     if any(q in (c.get(k) or "").lower() for k in ("name", "email", "identifier"))]
            return self._json(200, {"payload": found[:15]})
        if method == "POST" and path == "/contacts/filter":
            if s.get("filter_miss", 0) > 0:
                s["filter_miss"] -= 1
                return self._json(200, {"payload": []})
            (cond,) = body["payload"]
            assert cond["filter_operator"] == "equal_to", cond
            value = cond["values"][0].lower()
            found = [c for c in cw["contacts"].values() if (c.get(cond["attribute_key"]) or "").lower() == value]
            return self._json(200, {"payload": found[:15]})
        if method == "POST" and path == "/contacts":
            # as Chatwoot: Devise.email_regexp, and email and identifier unique in an account
            if body.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+", body["email"]):
                return self._json(422, {"message": "Email is invalid"})
            for c in cw["contacts"].values():
                for k in ("email", "identifier"):
                    if body.get(k) and (c.get(k) or "").lower() == body[k].lower():
                        return self._json(422, {"message": f"{k} has already been taken"})
            cid = len(cw["contacts"]) + 1
            cw["contacts"][cid] = {"id": cid, **body}
            return self._json(200, {"payload": {"contact": cw["contacts"][cid], "contact_inbox": {}}})
        m = re.fullmatch(r"/contacts/(\d+)/conversations", path)
        if m:
            convs = [self._conv(c) for c in cw["conversations"].values() if c["contact_id"] == int(m.group(1))]
            return self._json(200, {"payload": convs})
        if method == "POST" and path == "/conversations":
            cid = len(cw["conversations"]) + 11
            cw["conversations"][cid] = {"id": cid, "inbox_id": body["inbox_id"], "contact_id": body["contact_id"],
                                        "status": body.get("status", "open"), "custom_attributes": body["custom_attributes"],
                                        "additional_attributes": body["additional_attributes"]}
            cw["messages"][cid] = []
            return self._json(200, self._conv(cw["conversations"][cid]))
        m = re.fullmatch(r"/conversations/(\d+)(/.*)?", path)
        if not m or int(m.group(1)) not in cw["conversations"]:
            return self._json(404, {})
        conv, rest = cw["conversations"][int(m.group(1))], m.group(2) or ""
        if method == "GET" and rest == "":
            return self._json(200, self._conv(conv))
        if method == "GET" and rest == "/messages":
            msgs = cw["messages"][conv["id"]]
            if "before" in query:
                msgs = [x for x in msgs if x["id"] < int(query["before"][0])]
            page = list(msgs[-20:])
            if s.get("list_delay"):
                # a snapshot that is stale by the time the caller acts on it
                time.sleep(s["list_delay"])
            return self._json(200, {"payload": page})
        if method == "POST" and rest == "/messages":
            mid = s["next_message"]
            s["next_message"] += 1
            kind = {"incoming": 0, "outgoing": 1}[body["message_type"]]
            sender = {"id": 7, "type": "user"} if kind else {"id": conv["contact_id"], "type": "contact"}
            cw["messages"][conv["id"]].append({"id": mid, "content": body["content"], "message_type": kind,
                                               "private": body.get("private", False), "source_id": body.get("source_id"),
                                               "sender": sender})
            return self._json(200, {"id": mid})
        if method == "POST" and rest == "/toggle_status":
            conv["status"] = body["status"]
            return self._json(200, {"payload": {"current_status": body["status"]}})
        return self._json(404, {})

    def _conv(self, conv):
        contact = self.state["chatwoot"]["contacts"][conv["contact_id"]]
        return {**conv, "meta": {"sender": {"id": contact["id"], "email": contact.get("email")}}}


def sign(secret, body, ts=None):
    ts = str(int(time.time()) if ts is None else ts)
    return ts, "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()


class BridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        files = {"key": ODOO_KEY, "token": TOKEN, "event": EVENT_SECRET, "inbox": INBOX_SECRET}
        cls.paths = {}
        for name, value in files.items():
            path = pathlib.Path(cls.tmp.name) / name
            path.write_text(value + "\n")
            cls.paths[name] = str(path)

    @classmethod
    def tearDownClass(cls):
        cls.fake.shutdown()
        cls.fake.server_close()
        cls.tmp.cleanup()
        logging.disable(logging.NOTSET)

    def setUp(self):
        Fake.state = self.state = {
            "odoo_calls": [], "chatwoot_calls": [], "next_message": 500,
            "odoo": {
                "helpdesk.ticket": {7: {"name": "Cannot log in", "partner_id": [42, "Ada"], "partner_email": "ada@example.org",
                                        "partner_name": "Ada", "stage_id": [1, "New"], "team_id": [3, "Customer Care"],
                                        "x_chatwoot_url": False}},
                "crm.lead": {9: {"name": "Quote", "partner_id": False, "email_from": "bob@example.org", "contact_name": "Bob",
                                 "partner_name": False, "x_chatwoot_url": False}},
                "mail.message": {
                    70: {"model": "helpdesk.ticket", "res_id": 7, "email_from": "Ada <ada@example.org>", "attachment_ids": [],
                         "body": "<p>Hello,<br>I cannot log in.</p><blockquote data-o-mail-quote=\"1\">old</blockquote>"},
                    71: {"model": "helpdesk.ticket", "res_id": 7, "email_from": "carl@example.org", "attachment_ids": [5],
                         "body": "<div>Same here</div>"},
                },
                "res.users": {2: {"login": "staff@grn.cloud", "email": "staff@grn.cloud", "partner_id": [3, "Staff"]}},
                "helpdesk.stage": {1: {"name": "New", "fold": False, "team_ids": [3], "sequence": 1},
                                   4: {"name": "Cancelled", "fold": True, "team_ids": [3], "sequence": 5},
                                   3: {"name": "Solved", "fold": True, "team_ids": [3], "sequence": 3}},
            },
            "chatwoot": {"contacts": {}, "conversations": {}, "messages": {}},
        }
        base = f"http://127.0.0.1:{self.fake.server_address[1]}"
        env = {
            "CHATWOOT_URL": base, "PUBLIC_URL": "https://support.example.org", "CHATWOOT_TOKEN_FILE": self.paths["token"],
            "INBOX_SECRET_FILE": self.paths["inbox"], "TICKET_INBOX": "Support", "LEAD_INBOX": "Sales",
            "ODOO_URL": base, "ODOO_ALLOW_HTTP": "1", "ODOO_DATABASE": "odoo", "ODOO_API_KEY_FILE": self.paths["key"],
            "EVENT_SECRET_FILE": self.paths["event"], "WEBHOOK_PORT": "0", "EVENT_PORT": "0",
        }
        self.env = env
        self.bridge = odoo_bridge.Bridge(odoo_bridge.Config(env))

    def tearDown(self):
        self.bridge.stop(budget=0)
        self.bridge.pool.shutdown(wait=True)

    # ------------------------------------------------------------ helpers

    def event(self, name, model="helpdesk.ticket", rid=7, **extra):
        return self.bridge.handle_event({"event": name, "model": model, "id": rid, "db": "odoo",
                                         "idempotency_key": f"{name}:{model}:{rid}", **extra})

    def conversations(self):
        return self.state["chatwoot"]["conversations"]

    def chat(self, cid):
        return self.state["chatwoot"]["messages"][cid]

    def staff_reply(self, cid, content="We reset your password.", private=False, sender=None, **extra):
        mid = self.state["next_message"]
        self.state["next_message"] += 1
        sender = sender or {"id": 7, "type": "user"}
        self.chat(cid).append({"id": mid, "content": content, "message_type": 1, "private": private,
                               "source_id": None, "sender": sender, **extra})
        return {"event": "message_created", "id": mid, "message_type": "outgoing", "private": private,
                "content": content, "conversation": {"id": cid},
                "sender": {**sender, "email": "staff@grn.cloud", "name": "Staff"}}

    def wait_idle(self, timeout=5):
        end = time.time() + timeout
        while self.bridge.pending() and time.time() < end:
            time.sleep(0.02)
        self.assertEqual(self.bridge.pending(), 0)

    def posts(self):
        return [m for m in self.state["odoo"]["mail.message"].values() if m.get("message_type") == "comment"]

    # ------------------------------------------------------------ Odoo to Chatwoot

    def test_ticket_created_makes_one_linked_conversation(self):
        self.assertEqual(self.event("ticket.created"), "done")
        self.assertEqual(self.event("ticket.created"), "done")
        self.assertEqual(len(self.conversations()), 1)
        conv = next(iter(self.conversations().values()))
        self.assertEqual(conv["inbox_id"], SUPPORT)
        self.assertEqual(conv["custom_attributes"]["odoo_ref"], "helpdesk.ticket:7")
        self.assertEqual(conv["custom_attributes"]["odoo_url"].rsplit("/odoo/", 1)[1], "helpdesk.ticket/7")
        self.assertEqual(self.state["odoo"]["helpdesk.ticket"][7]["x_chatwoot_url"],
                         f"https://support.example.org/app/accounts/1/conversations/{conv['id']}")
        contact = self.state["chatwoot"]["contacts"][conv["contact_id"]]
        self.assertEqual((contact["email"], contact["identifier"], contact["name"]),
                         ("ada@example.org", "odoo:res.partner:42", "Ada"))

    def test_contact_is_found_among_similar_ones(self):
        # 16 identifiers that contain odoo:res.partner:42 push the exact one off the search page
        contacts = self.state["chatwoot"]["contacts"]
        for i in range(16):
            contacts[i + 1] = {"id": i + 1, "name": "x", "identifier": f"odoo:res.partner:42{i}"}
        contacts[17] = {"id": 17, "name": "Ada", "email": "old@example.org", "identifier": "odoo:res.partner:42"}
        self.assertEqual(self.event("ticket.created"), "done")
        self.assertEqual(len(contacts), 17)
        self.assertEqual(next(iter(self.conversations().values()))["contact_id"], 17)

    def test_contact_created_meanwhile_is_used(self):
        self.state["chatwoot"]["contacts"][1] = {"id": 1, "name": "Ada", "email": "ada@example.org"}
        self.state["filter_miss"] = 2
        self.assertEqual(self.event("ticket.created"), "done")
        self.assertEqual(next(iter(self.conversations().values()))["contact_id"], 1)

    def test_link_is_found_again_only_in_the_record_inbox(self):
        self.event("ticket.created")
        (cid,) = self.conversations()
        self.conversations()[cid]["inbox_id"] = WEBSITE
        self.state["odoo"]["helpdesk.ticket"][7]["x_chatwoot_url"] = False
        self.event("ticket.created")
        self.assertEqual(len(self.conversations()), 2)
        self.assertFalse(self.state["odoo"]["helpdesk.ticket"][7]["x_chatwoot_url"].endswith(f"/{cid}"))

    def test_lost_link_finds_the_existing_conversation(self):
        self.event("ticket.created")
        self.state["odoo"]["helpdesk.ticket"][7]["x_chatwoot_url"] = False
        self.event("ticket.created")
        self.assertEqual(len(self.conversations()), 1)
        self.assertTrue(self.state["odoo"]["helpdesk.ticket"][7]["x_chatwoot_url"])

    def test_customer_mail_is_mirrored_once_without_quote(self):
        self.event("ticket.created")
        self.assertEqual(self.event("message.created", message_id=70), "done")
        self.assertEqual(self.event("message.created", message_id=70), "done")
        cid = next(iter(self.conversations()))
        msgs = self.chat(cid)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["message_type"], 0)
        self.assertEqual(msgs[0]["source_id"], "odoo:mail.message:70")
        self.assertEqual(msgs[0]["content"], "Hello,\nI cannot log in.")

    def test_mail_before_ticket_event_creates_the_conversation(self):
        self.event("message.created", message_id=70)
        self.event("ticket.created")
        self.assertEqual(len(self.conversations()), 1)
        self.assertEqual(len(self.chat(next(iter(self.conversations())))), 1)

    def test_mail_from_another_sender_names_it_and_its_attachments(self):
        self.event("message.created", message_id=71)
        text = self.chat(next(iter(self.conversations())))[0]["content"]
        self.assertTrue(text.startswith("From: carl@example.org\n\nSame here"))
        self.assertIn("1 attachment(s) in Odoo", text)

    def test_message_of_another_record_is_not_mirrored(self):
        self.state["odoo"]["helpdesk.ticket"][8] = dict(self.state["odoo"]["helpdesk.ticket"][7])
        self.event("message.created", rid=8, message_id=70)
        self.assertEqual(self.chat(next(iter(self.conversations()))), [])

    def test_ticket_closed_resolves_and_never_creates(self):
        self.assertEqual(self.event("ticket.closed"), "done")
        self.assertEqual(self.conversations(), {})
        self.event("ticket.created")
        self.event("ticket.closed")
        self.assertEqual(next(iter(self.conversations().values()))["status"], "resolved")
        toggles = [c for c in self.state["chatwoot_calls"] if c[1].endswith("/toggle_status")]
        self.event("ticket.closed")
        self.assertEqual(len([c for c in self.state["chatwoot_calls"] if c[1].endswith("/toggle_status")]), len(toggles))

    def test_lead_goes_to_sales_and_is_off_without_inbox(self):
        self.event("lead.created", model="crm.lead", rid=9)
        conv = next(iter(self.conversations().values()))
        self.assertEqual(conv["inbox_id"], SALES)
        self.assertEqual(self.state["chatwoot"]["contacts"][conv["contact_id"]]["name"], "Bob")
        self.env["LEAD_INBOX"] = ""
        bridge = odoo_bridge.Bridge(odoo_bridge.Config(self.env))
        self.state["odoo"]["crm.lead"][10] = dict(self.state["odoo"]["crm.lead"][9])
        self.assertEqual(bridge.handle_event({"event": "lead.created", "model": "crm.lead", "id": 10}), "ignored")
        bridge.pool.shutdown()

    def test_unknown_model_or_bad_ids_are_ignored(self):
        self.assertEqual(self.bridge.handle_event({"event": "ticket.created", "model": "res.partner", "id": 1}), "ignored")
        self.assertEqual(self.bridge.handle_event({"event": "ticket.created", "model": "helpdesk.ticket", "id": True}), "ignored")
        self.assertEqual(self.event("message.created", message_id="70"), "ignored")
        self.assertEqual(self.state["chatwoot_calls"], [])

    # ------------------------------------------------------------ Chatwoot to Odoo

    def test_staff_reply_is_posted_once_on_the_ticket(self):
        self.event("ticket.created")
        cid = next(iter(self.conversations()))
        hook = self.staff_reply(cid, "Hi Ada,\nwe reset it.\n\nRegards")
        self.assertEqual(self.bridge.handle_webhook(hook), "done")
        self.assertEqual(self.bridge.handle_webhook(hook), "done")
        posts = self.posts()
        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertEqual((post["model"], post["res_id"]), ("helpdesk.ticket", 7))
        self.assertEqual(post["subtype_xmlid"], "mail.mt_comment")
        self.assertEqual(post["x_chatwoot_message_id"], f"support.example.org/1/{hook['id']}")
        self.assertEqual(post["author_id"], 3)
        self.assertEqual(post["partner_ids"], [42])
        self.assertEqual(post["body"], "<p>Hi Ada,<br>we reset it.</p><p>Regards</p>")
        self.assertIs(post["body_is_html"], True)

    def test_reply_text_is_escaped(self):
        self.event("ticket.created")
        self.bridge.handle_webhook(self.staff_reply(next(iter(self.conversations())), "<script>x</script> & y"))
        self.assertEqual(self.posts()[0]["body"], "<p>&lt;script&gt;x&lt;/script&gt; &amp; y</p>")

    def test_what_is_never_sent_to_the_customer(self):
        self.event("ticket.created")
        cid = next(iter(self.conversations()))
        note = self.staff_reply(cid, "AI draft: ...", private=True)
        self.assertEqual(self.bridge.handle_webhook(note), "ignored")
        bot = self.staff_reply(cid, "bot", sender={"id": 1, "type": "agent_bot"})
        self.assertEqual(self.bridge.handle_webhook(bot), "ignored")
        forged = self.staff_reply(cid, "forged")
        forged["sender"]["id"] = 99
        self.assertEqual(self.bridge.handle_webhook(forged), "ignored")
        # the webhook says public, the API says private: the API wins
        flipped = self.staff_reply(cid, "flipped", private=True)
        flipped["private"] = False
        self.assertEqual(self.bridge.handle_webhook(flipped), "ignored")
        incoming = {"event": "message_created", "id": 1, "message_type": "incoming", "conversation": {"id": cid}}
        self.assertEqual(self.bridge.handle_webhook(incoming), "ignored")
        self.assertEqual(self.posts(), [])

    def test_conversation_outside_the_bridge_is_ignored(self):
        self.event("ticket.created")
        cid = next(iter(self.conversations()))
        self.conversations()[cid]["inbox_id"] = WEBSITE
        self.assertEqual(self.bridge.handle_webhook(self.staff_reply(cid)), "ignored")
        self.conversations()[cid]["inbox_id"] = SUPPORT
        self.conversations()[cid]["custom_attributes"] = {}
        self.assertEqual(self.bridge.handle_webhook(self.staff_reply(cid)), "ignored")
        self.assertEqual(self.posts(), [])

    def test_reply_with_attachment_warns_staff_in_a_private_note(self):
        self.event("ticket.created")
        cid = next(iter(self.conversations()))
        self.bridge.handle_webhook(self.staff_reply(cid, "See file", attachments=[{"id": 1}]))
        self.assertEqual(len(self.posts()), 1)
        note = self.chat(cid)[-1]
        self.assertTrue(note["private"])
        self.assertIn("were not sent", note["content"])

    def test_unknown_staff_posts_as_the_bridge_user(self):
        self.event("ticket.created")
        hook = self.staff_reply(next(iter(self.conversations())))
        hook["sender"]["email"] = "nobody@grn.cloud"
        self.bridge.handle_webhook(hook)
        self.assertNotIn("author_id", self.posts()[0])

    def test_resolved_conversation_solves_the_ticket(self):
        self.event("ticket.created")
        cid = next(iter(self.conversations()))
        self.conversations()[cid]["status"] = "resolved"
        hook = {"event": "conversation_status_changed", "id": cid, "status": "resolved"}
        self.assertEqual(self.bridge.handle_webhook(hook), "done")
        self.assertEqual(self.state["odoo"]["helpdesk.ticket"][7]["stage_id"], 3)
        self.state["odoo"]["helpdesk.ticket"][7]["stage_id"] = [4, "Cancelled"]
        writes = len([c for c in self.state["odoo_calls"] if c[1] == "write"])
        self.assertEqual(self.bridge.handle_webhook(hook), "done")
        self.assertEqual(len([c for c in self.state["odoo_calls"] if c[1] == "write"]), writes)

    def test_resolve_webhook_without_resolved_conversation_is_ignored(self):
        self.event("ticket.created")
        cid = next(iter(self.conversations()))
        hook = {"event": "conversation_status_changed", "id": cid, "status": "resolved"}
        self.assertEqual(self.bridge.handle_webhook(hook), "ignored")
        self.assertEqual(self.state["odoo"]["helpdesk.ticket"][7]["stage_id"], [1, "New"])

    def test_reply_to_a_lead_without_partner_goes_to_its_address(self):
        self.event("lead.created", model="crm.lead", rid=9)
        cid = next(iter(self.conversations()))
        self.assertEqual(self.bridge.handle_webhook(self.staff_reply(cid)), "done")
        bob = [pid for pid, r in self.state["odoo"]["res.partner"].items() if r["email"] == "bob@example.org"]
        self.assertEqual(self.posts()[0]["partner_ids"], bob)

    def test_reply_without_any_customer_address_is_not_sent(self):
        self.state["odoo"]["crm.lead"][10] = {"name": "x", "partner_id": False, "email_from": False,
                                              "contact_name": "Nobody", "x_chatwoot_url": False}
        self.event("lead.created", model="crm.lead", rid=10)
        cid = next(iter(self.conversations()))
        self.assertEqual(self.bridge.handle_webhook(self.staff_reply(cid)), "ignored")
        self.assertEqual(self.posts(), [])
        self.assertIn("no customer address", self.chat(cid)[-1]["content"])
        self.assertTrue(self.chat(cid)[-1]["private"])

    def test_contact_of_a_record_without_partner_is_found_again(self):
        self.state["odoo"]["crm.lead"][10] = {"name": "x", "partner_id": False, "email_from": False,
                                              "contact_name": "Nobody", "x_chatwoot_url": False}
        self.event("lead.created", model="crm.lead", rid=10)
        self.state["odoo"]["crm.lead"][10]["x_chatwoot_url"] = False
        self.event("lead.created", model="crm.lead", rid=10)
        self.assertEqual(len(self.state["chatwoot"]["contacts"]), 1)
        self.assertEqual(len(self.conversations()), 1)

    def test_edited_odoo_ref_cannot_reach_another_record(self):
        self.state["odoo"]["helpdesk.ticket"][8] = dict(self.state["odoo"]["helpdesk.ticket"][7], partner_id=[43, "Eve"])
        self.event("ticket.created")
        self.event("ticket.created", rid=8)
        mine, other = sorted(self.conversations())
        # an agent points the conversation of ticket 7 at ticket 8
        self.conversations()[mine]["custom_attributes"]["odoo_ref"] = "helpdesk.ticket:8"
        self.assertEqual(self.bridge.handle_webhook(self.staff_reply(mine)), "ignored")
        self.assertEqual(self.posts(), [])
        self.conversations()[mine]["status"] = "resolved"
        hook = {"event": "conversation_status_changed", "id": mine, "status": "resolved"}
        self.assertEqual(self.bridge.handle_webhook(hook), "ignored")
        self.assertEqual(self.state["odoo"]["helpdesk.ticket"][8]["stage_id"], [1, "New"])

    def test_staff_address_is_matched_exactly(self):
        self.state["odoo"]["res.users"] = {4: {"login": "axb@grn.cloud", "email": "axb@grn.cloud", "partner_id": [9, "X"]}}
        self.event("ticket.created")
        hook = self.staff_reply(next(iter(self.conversations())))
        hook["sender"]["email"] = "a_b@grn.cloud"
        self.bridge.handle_webhook(hook)
        self.assertNotIn("author_id", self.posts()[0])

    def test_failed_reply_is_retried_then_posted_once(self):
        self.bridge.retries = (0.05, 0.05, 0.05)
        self.event("ticket.created")
        self.state["odoo_fail"] = 2
        self.assertTrue(self.bridge.accept_webhook(self.staff_reply(next(iter(self.conversations())))))
        self.wait_idle()
        self.assertEqual(len(self.posts()), 1)

    def test_reply_given_up_tells_staff(self):
        self.bridge.retries = (0.05,)
        self.event("ticket.created")
        cid = next(iter(self.conversations()))
        self.state["odoo_fail"] = 10
        self.bridge.accept_webhook(self.staff_reply(cid))
        self.wait_idle()
        self.assertEqual(self.posts(), [])
        self.assertIn("did not reach Odoo", self.chat(cid)[-1]["content"])
        self.assertIn('chatwoot_odoo_bridge_webhooks_total{result="given_up"} 1', self.bridge.metrics.render())

    def test_stop_tells_staff_about_a_waiting_retry(self):
        self.bridge.retries = (30,)
        self.event("ticket.created")
        cid = next(iter(self.conversations()))
        self.state["odoo_fail"] = 1
        self.bridge.accept_webhook(self.staff_reply(cid))
        end = time.time() + 5
        while not self.bridge._timers and time.time() < end:
            time.sleep(0.02)
        self.assertEqual(self.bridge.stop(), 1)
        self.assertEqual(self.posts(), [])
        self.assertTrue(self.chat(cid)[-1]["private"])
        self.assertIn("restarted before this reply reached Odoo", self.chat(cid)[-1]["content"])
        self.assertEqual(self.bridge.pending(), 0)

    def test_stop_tells_staff_about_queued_replies(self):
        self.event("ticket.created")
        cid = next(iter(self.conversations()))
        self.state["odoo_delay"] = 0.05
        hooks = [self.staff_reply(cid) for _ in range(6)]
        for hook in hooks:
            self.bridge.accept_webhook(hook)
        dropped = self.bridge.stop()
        notes = [m for m in self.chat(cid) if m["private"] and "restarted" in m["content"]]
        self.assertGreaterEqual(dropped, 1)
        self.assertEqual(len(notes), dropped)
        self.assertEqual(len(self.posts()) + dropped, 6)
        self.assertEqual(self.bridge.pending(), 0)

    def test_stop_with_a_slow_odoo_tells_staff_first(self):
        self.event("ticket.created")
        cid = next(iter(self.conversations()))
        self.bridge.retries = (30,)
        self.state["odoo_fail"] = 1
        self.bridge.accept_webhook(self.staff_reply(cid))
        end = time.time() + 5
        while not self.bridge._timers and time.time() < end:
            time.sleep(0.02)
        # Odoo hangs: events fill the workers and the queue behind them
        self.state["odoo_delay"] = 2
        event = {"event": "ticket.created", "model": "helpdesk.ticket", "id": 7}
        for _ in range(10):
            self.bridge.run("events", self.bridge.handle_event, event, 0.01)
        started = time.monotonic()
        self.assertEqual(self.bridge.stop(budget=1), 1)
        self.assertLess(time.monotonic() - started, 3)
        self.state["odoo_delay"] = 0
        self.assertIn("restarted before this reply reached Odoo", self.chat(cid)[-1]["content"])

    def test_attempt_already_dropped_by_stop_is_not_dropped_again(self):
        payload = {"event": "message_created", "id": 1, "conversation": {"id": 1}}
        with self.bridge._pending_lock:
            self.bridge._pending += 1
        self.bridge._stopping = True
        self.bridge._drop(payload)  # stop() took it from the queue
        self.bridge._attempt(payload, 0, object())  # the worker started it a moment later
        self.assertEqual(self.bridge.pending(), 0)
        self.assertEqual(len(self.bridge._dropped), 1)

    def test_mailed_in_lead_with_a_display_name(self):
        lead = self.state["odoo"]["crm.lead"][9]
        lead["email_from"] = '"Bob Smith" <bob@example.org>'
        self.assertEqual(self.event("lead.created", model="crm.lead", rid=9), "done")
        conv = next(iter(self.conversations().values()))
        self.assertEqual(self.state["chatwoot"]["contacts"][conv["contact_id"]]["email"], "bob@example.org")
        self.bridge.handle_webhook(self.staff_reply(conv["id"]))
        (call,) = [kw for model, method, kw in self.state["odoo_calls"] if method == "find_or_create"]
        self.assertEqual(call["email"], '"Bob" <bob@example.org>')
        self.assertEqual(len(self.posts()), 1)

    def test_alert_counters_exist_before_the_first_failure(self):
        text = self.bridge.metrics.render()
        for line in ('webhooks_total{result="given_up"} 0', 'events_total{result="error"} 0'):
            self.assertIn("chatwoot_odoo_bridge_" + line, text)

    def test_events_beyond_the_bound_answer_busy(self):
        self.addCleanup(setattr, odoo_bridge, "MAX_EVENTS", odoo_bridge.MAX_EVENTS)
        odoo_bridge.MAX_EVENTS = 1
        self.state["odoo_delay"] = 0.3
        event = {"event": "ticket.created", "model": "helpdesk.ticket", "id": 7}
        self.assertEqual(self.bridge.run("events", self.bridge.handle_event, event, 0.05), (503, "in progress"))
        self.assertEqual(self.bridge.run("events", self.bridge.handle_event, event, 0.05), (503, "busy"))

    def test_customer_name_cannot_break_the_address(self):
        self.state["odoo"]["crm.lead"][9]["contact_name"] = 'Bob \\'
        self.event("lead.created", model="crm.lead", rid=9)
        self.bridge.handle_webhook(self.staff_reply(next(iter(self.conversations()))))
        (call,) = [kw for model, method, kw in self.state["odoo_calls"] if method == "find_or_create"]
        self.assertEqual(call["email"], '"Bob" <bob@example.org>')

    def test_chatwoot_404_is_retried_not_dropped(self):
        self.state["chatwoot_404"] = True
        with self.assertRaises(odoo_bridge.HttpError):
            self.event("ticket.created")

    def test_redirect_is_not_followed_with_the_key(self):
        self.state["odoo_redirect"] = True
        with self.assertRaises(odoo_bridge.HttpError) as caught:
            self.event("ticket.created")
        self.assertEqual(caught.exception.status, 302)
        self.assertEqual(len(self.state["odoo_calls"]), 1)

    def test_concurrent_redelivery_mirrors_a_mail_once(self):
        self.event("ticket.created")
        # every redelivery checks the messages at once, then writes
        self.state["list_delay"] = 0.1
        threads = [threading.Thread(target=self.event, args=("message.created",), kwargs={"message_id": 70})
                   for _ in range(3)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(self.chat(next(iter(self.conversations())))), 1)

    # ------------------------------------------------------------ HTTP

    def serve(self, **cfg):
        for key, value in cfg.items():
            setattr(self.bridge.cfg, key, value)
        servers = odoo_bridge.serve(self.bridge)
        self.addCleanup(lambda: [(s.shutdown(), s.server_close()) for s in servers])
        return {"webhook": servers[0].server_address[1], "event": servers[1].server_address[1]}

    def post(self, port, path, body, headers):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", path, body, {"Content-Type": "application/json", **headers})
        resp = conn.getresponse()
        return resp.status, resp.read().decode().strip()

    def signed_event(self, body, secret=EVENT_SECRET, ts=None):
        ts, sig = sign(secret, body, ts)
        return {"X-Grncloud-Timestamp": ts, "X-Grncloud-Signature": sig, "X-Grncloud-Event": "ticket.created"}

    def test_event_port_checks_the_signature(self):
        ports = self.serve()
        body = json.dumps({"event": "ticket.created", "model": "helpdesk.ticket", "id": 7}).encode()
        self.assertEqual(self.post(ports["event"], "/odoo/events", body, self.signed_event(body, "x" * 40))[0], 401)
        stale = self.signed_event(body, ts=int(time.time()) - 400)
        self.assertEqual(self.post(ports["event"], "/odoo/events", body, stale)[0], 401)
        self.assertEqual(self.post(ports["event"], "/chatwoot/webhook", body, self.signed_event(body))[0], 404)
        self.assertEqual(self.conversations(), {})
        self.assertEqual(self.post(ports["event"], "/odoo/events", body, self.signed_event(body)), (200, "done"))
        self.assertEqual(len(self.conversations()), 1)

    def test_webhook_port_checks_the_signature(self):
        ports = self.serve()
        self.event("ticket.created")
        body = json.dumps(self.staff_reply(next(iter(self.conversations())))).encode()
        ts, sig = sign(INBOX_SECRET, body)
        bad = {"X-Chatwoot-Timestamp": ts, "X-Chatwoot-Signature": "sha256=" + "0" * 64}
        self.assertEqual(self.post(ports["webhook"], "/chatwoot/webhook", body, bad)[0], 401)
        self.assertEqual(self.post(ports["webhook"], "/odoo/events", body, bad)[0], 404)
        good = {"X-Chatwoot-Timestamp": ts, "X-Chatwoot-Signature": sig}
        self.assertEqual(self.post(ports["webhook"], "/chatwoot/webhook", body, good), (200, "accepted"))
        self.wait_idle()
        self.assertEqual(len(self.posts()), 1)

    def test_slow_event_answers_503_and_finishes(self):
        ports = self.serve(event_deadline=0.2)
        self.state["odoo_delay"] = 0.3
        body = json.dumps({"event": "ticket.created", "model": "helpdesk.ticket", "id": 7}).encode()
        self.assertEqual(self.post(ports["event"], "/odoo/events", body, self.signed_event(body))[0], 503)
        self.bridge.pool.shutdown(wait=True)
        self.assertEqual(len(self.conversations()), 1)

    def test_deleted_record_ends_the_retries(self):
        ports = self.serve()
        body = json.dumps({"event": "ticket.created", "model": "helpdesk.ticket", "id": 404}).encode()
        self.assertEqual(self.post(ports["event"], "/odoo/events", body, self.signed_event(body)), (200, "gone"))

    def test_unknown_odoo_route_is_retried_not_dropped(self):
        with self.assertRaises(odoo_bridge.HttpError) as caught:
            self.bridge.odoo.call("helpdesk.ticket", "no_such_method", ids=[7])
        self.assertEqual(caught.exception.status, 404)

    def test_odoo_failure_is_a_502_for_a_retry(self):
        ports = self.serve()
        with open(self.paths["key"], "w") as f:
            f.write("wrong")
        self.addCleanup(lambda: pathlib.Path(self.paths["key"]).write_text(ODOO_KEY))
        body = json.dumps({"event": "ticket.created", "model": "helpdesk.ticket", "id": 7}).encode()
        self.assertEqual(self.post(ports["event"], "/odoo/events", body, self.signed_event(body)), (502, "HttpError"))

    def test_metrics_count_results(self):
        ports = self.serve()
        body = json.dumps({"event": "ticket.created", "model": "helpdesk.ticket", "id": 7}).encode()
        self.post(ports["event"], "/odoo/events", body, self.signed_event(body))
        conn = http.client.HTTPConnection("127.0.0.1", ports["webhook"], timeout=5)
        conn.request("GET", "/metrics")
        text = conn.getresponse().read().decode()
        self.assertIn('chatwoot_odoo_bridge_events_total{result="done"} 1', text)
        self.assertIn('chatwoot_odoo_bridge_conversations_created_total{model="helpdesk.ticket"} 1', text)


class PureTest(unittest.TestCase):
    def test_verify(self):
        body = b'{"a":1}'
        ts, sig = sign("s" * 40, body)
        self.assertTrue(odoo_bridge.verify("s" * 40, ts, body, sig))
        self.assertFalse(odoo_bridge.verify("s" * 40, ts, body + b" ", sig))
        self.assertFalse(odoo_bridge.verify("s" * 40, "+" + ts, body, sig))
        self.assertFalse(odoo_bridge.verify("s" * 40, "١٢", body, sig))
        self.assertFalse(odoo_bridge.verify("s" * 40, ts, body, "sha256=é"))
        self.assertFalse(odoo_bridge.verify("", ts, body, sig))
        self.assertFalse(odoo_bridge.verify("s" * 40, None, body, sig))

    def test_html_to_text(self):
        html = ("<div>Hi&nbsp;there<br/>line two</div><ul><li>a</li><li>b</li></ul>"
                "<style>p{}</style><p>end</p><div data-o-mail-quote-container=\"1\"></div>"
                "<blockquote data-o-mail-quote=\"1\"><p>quoted <b>old</b></p></blockquote><p>after</p>")
        self.assertEqual(odoo_bridge.html_to_text(html), "Hi there\nline two\n\n- a\n- b\n\nend\n\nafter")

    def test_text_to_html(self):
        self.assertEqual(odoo_bridge.text_to_html("a\nb\n\n\nc & d"), "<p>a<br>b</p><p>c &amp; d</p>")

    def test_config_refuses_plain_http(self):
        env = {"PUBLIC_URL": "https://x", "CHATWOOT_TOKEN_FILE": "t", "INBOX_SECRET_FILE": "i", "ODOO_URL": "http://odoo",
               "ODOO_API_KEY_FILE": "k", "EVENT_SECRET_FILE": "e"}
        with self.assertRaises(ValueError):
            odoo_bridge.Config(env)


if __name__ == "__main__":
    unittest.main()
