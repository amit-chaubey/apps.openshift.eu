# mcp-onlyoffice-live

## Purpose

Runs an MCP server in your project that lets an agent edit an office document live, as
itself, next to the people who have it open. The agent opens a file from its own Nextcloud
files in ONLYOFFICE and joins the document's co-editing session; everyone in it sees the
agent's name, its cursor and each edit as it happens, and every edit carries the agent's
name in the document history, comments and tracked changes.

How it works:

- The server opens the file through Nextcloud Direct Editing as the calling account, in a
  headless Chromium, so it is one more participant in the session.
- It edits through the ONLYOFFICE plugin **agent-bridge**, which the Document Server serves
  from its `sdkjs-plugins` folder. The `nextcloud` chart of this repository mounts it; for
  another Document Server, copy `images/mcp-onlyoffice-live/plugin/` of the chart repository
  into `sdkjs-plugins/agent-bridge/`. The plugin is invisible and stays idle in a person's
  editor.
- The server holds no Document Server secret and no Nextcloud password of its own. It
  forwards the caller's `Authorization` header to Nextcloud: an OpenID Connect bearer token
  that Nextcloud's `user_oidc` accepts, or Basic with the caller's app password. Nextcloud
  decides who the caller is and which files it may open.

### Network of the browser

The browser of a session reaches only the origins of its editor page. A script of `run`
runs in the editor, and the editor refuses its network requests.

### Tools

An MCP gateway prefixes each tool with the server name, `office-live_<tool>`.

| Tool | What it does |
|---|---|
| `open` | Opens a file live as the caller and returns the editor type: text, spreadsheet or presentation |
| `create` | Creates an empty `.docx`, `.xlsx` or `.pptx` from the ONLYOFFICE template and opens it live |
| `read` | Current content, including what other people typed a moment ago: paragraphs, the used cells of the active sheet, the texts of every slide |
| `insert` | Text at the end, the start, the agent's cursor, or after or before a paragraph; into a cell; as a new slide. `typing: true` writes word by word, so people watch it being typed |
| `replace` | The first or every match; a tracked change while track changes is on |
| `comment` | A comment under the agent's name on a text, a cell or a slide |
| `format_text` | Bold, italic, underline, strikeout, font, size, colour, highlight, superscript, a named style such as Heading 1, alignment, spacing, indents and shading, on found text or a whole paragraph; text documents and presentations |
| `insert_table` | A filled table with a header row and borders; in a spreadsheet a bordered block of cells |
| `edit_table` | Cell text and format, rows and columns added or removed, merged cells, widths, heights and borders of a table in a text document |
| `insert_list` | A bulleted or numbered list; on a slide as a text box |
| `insert_image` | An image from an https URL or a data URI |
| `insert_chart` | A chart from given series, or from a cell range in a spreadsheet |
| `write_cells` | Values and formulas into a spreadsheet |
| `format_cells` | Font, colours, alignment, wrap, number format, borders, merge, column width and row height of a range |
| `comments` | Every comment with author, time, quoted text, resolved state and replies |
| `comment_reply` | A reply under the agent's name |
| `comment_resolve` | Marks a comment resolved, or opens it again |
| `comment_delete` | Deletes a comment and its replies |
| `run` | A script of the ONLYOFFICE Office API in the agent's editor, for everything else: headers and footers, footnotes, content controls, shapes, conditional formatting, sheets. Value `scripts: false` removes this tool |
| `track_changes` | Switches track changes on or off for the document |
| `save` | Sends the agent's pending edits to the Document Server at once |
| `view` | A screenshot of the agent's editor window, with the other participants' cursors |
| `close` | Sends the pending edits and leaves the session; Nextcloud gets the file when the last participant leaves |
| `sessions` | The caller's open documents |
| `file_status` | Whether a file is open in an editor or locked, to call before writing it any other way |

### Lost updates

A file that is open in ONLYOFFICE is overwritten by the editor's next save. A write that
bypasses the editor, such as a WebDAV upload of a file an agent edited with another tool,
is lost. With the Nextcloud app `files_lock`, the ONLYOFFICE app locks a file while it is
open, and such a write fails with `423 Locked`. `file_status` reports that lock. Edit an open
file with this server instead.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install office-live grncloud/mcp-onlyoffice-live --set nextcloud.url=https://cloud.example.com
```

- `team`, `tenant` (default: empty): Install into `<tenant>-<team>` instead of the release namespace.
- `enabled` (default: true): Switch of the server inside another chart.
- `nameOverride` (default: empty): Name of the objects and of the server on the gateway; empty: `mcp.server`.
- `image.repository`, `image.tag` (default: `ghcr.io/hoolia/mcp-onlyoffice-live`, `0.1.2` by digest): Server image, built from `images/mcp-onlyoffice-live` of the chart repository.
- `nextcloud.url` (default: `http://nextcloud:8080`): Nextcloud the server opens documents in. The default is the Service of the `nextcloud` chart in the same project.
- `nextcloud.publicUrl` (default: empty): Origin the server's browser opens editor pages on when `nextcloud.url` is an internal address, for example `https://cloud.example.com`. Nextcloud builds the editor link from the host it is called on, and the Document Server page needs the public one. `nextcloud.publicUrlFrom` (`{name, key}` of a ConfigMap) reads it from the project instead; the `nextcloud` chart sets it.
- `nextcloud.editorId` (default: `onlyoffice`): Direct Editing editor of the ONLYOFFICE app.
- `credentials.mode` (default: `passthrough`): `passthrough` forwards each caller's `Authorization`. `shared` adds an account for callers that send none, from `credentials.existingSecret`, a Secret with the keys `user` and `password` (an app password).
- `sessions.max` (default: 4), `sessions.maxPerUser` (default: 2): Documents open at once in total and per account. Each takes 300 to 600 MiB.
- `sessions.idleTimeoutSeconds` (default: 900): A session unused this long is closed.
- `sessions.openTimeoutSeconds` (default: 120): Time the editor and the plugin get to load.
- `typingDelayMs` (default: 120): Pause between words with `typing: true`.
- `screenshots` (default: true): Offer the `view` tool.
- `port` (default: 13081): Container and Service port.
- `runAsUser` (default: empty): User ID of the server. OpenShift assigns one; on Kubernetes without that assignment set `1001`.
- `mcp.appProtocol`, `mcp.labelDomain`, `mcp.server`, `mcp.bundle`, `mcp.category` (default: `agentgateway.dev/mcp`, `mcp.openshift.eu`, `office-live`, `office-live`, `documents`): Gateway discovery and catalog labels.
- `service.labels`, `service.annotations` (default: empty): Extra labels and annotations of the Service, for example the gateway's exposure label.
- `route.enabled` (default: false): A Route on `/mcp`, for a gateway in another cluster that passes the caller's token; `route.host` and `route.annotations` (cert-manager issuer, a 300 s timeout).
- `resources`: Requests and limits; 512Mi requested, 3Gi limit.
- `networkPolicy.ingressNamespaces` (default: empty): Namespace selectors that may call the server besides its own project. Egress: DNS, the pods of the project, and TCP `networkPolicy.egress.webPorts` (80 and 443) anywhere, for the public addresses of Nextcloud and the Document Server.

One replica with the `Recreate` strategy: the sessions are browser contexts in the memory of
one process.

## Delivery instructions

1. Make sure the Document Server carries the plugin: with the `nextcloud` chart it does
   (`onlyoffice.agentBridge.enabled`, on by default). Open `https://<document server>/plugins.json`;
   it lists `sdkjs-plugins/agent-bridge/config.json`.
2. Give every agent its own Nextcloud account, so people see who edits. With OpenID Connect
   bearer tokens (`user_oidc` with bearer validation) the agent's own token is enough and
   Nextcloud creates the account on the first call. Otherwise create one app password per
   agent and let the gateway send it as the agent's credential.
3. Publish the server through the project's MCP gateway: add the gateway's namespace to
   `networkPolicy.ingressNamespaces` and its exposure label to `service.labels`. The
   gateway must pass the caller's own credential to the server.
4. Enable the Nextcloud app `files_lock`, so a direct write to an open file fails instead of
   being lost.

Prompt injection: a document can contain text that steers the agent that reads it. Treat
documents from outside the project as untrusted input.
