# mcp-officeagent

## Purpose

Runs the Office documents MCP server [ilia-sokolov/OfficeAgent.NET](https://github.com/ilia-sokolov/OfficeAgent.NET) in your project for its agents: create, read and edit Word (`.docx`), PowerPoint (`.pptx`) and Excel (`.xlsx`) files without an office suite or a browser. The server edits the Office Open XML package directly, so headings, styles, tables, images, shapes, charts, comments and Word tracked changes come out as native Office objects that Word, PowerPoint, LibreOffice and ONLYOFFICE open as usual.

An agent works through typed edit plans: it inspects a document, gets stable anchors and node paths, previews a plan and applies it. Every apply returns a receipt with SHA-256 hashes of the plan and of the document bytes.

Documents reach the server through two connections:

- `session` (`officeagent.session.enabled`): in memory. The agent puts a file in with `import_document_content` (for example the bytes it read from Nextcloud or a chat attachment), edits it by id and takes the result out with `export_document_content`. Session documents are gone when the pod restarts.
- `documents` (`documents.enabled`): a folder on a PersistentVolumeClaim, mounted at `/documents`. `create_document` writes new files there and the registration tools open files by path. Files stay across restarts.

The server listens on streamable HTTP at `/` on port `port` and answers `/healthz`. It asks callers for no credential of its own: either an MCP gateway in front of it authenticates the callers, or the token check (`auth.enabled`) does. Its Service carries the port `appProtocol: agentgateway.dev/mcp` and the annotations `agentgateway.dev/mcp-path: /` and `agentgateway.dev/mcp-target-name`, so an MCP gateway in front of it authenticates the callers and publishes the server; the NetworkPolicy admits the own project and `networkPolicy.ingressNamespaces` only, and lets the server reach DNS and nothing else. The Deployment and Service carry `mcp.openshift.eu/server: officeagent`, `mcp.openshift.eu/bundle: officeagent` and `mcp.openshift.eu/category: documents`.

Every caller of one release shares its connections. Install one release per project and keep it behind the gateway or the token check.

### Token check

With `auth.enabled` the token check of the library chart `mcp-auth` (an oauth2-proxy
sidecar) takes the Service port and the server listens on `127.0.0.1:auth.upstreamPort`
only, so no caller reaches it past the sidecar. The sidecar:

- lets `GET /healthz` through without a token, for the probes, and answers
  `/oauth2/ping` itself;
- refuses every other request without a bearer JWT that `auth.issuerUrl` signed for one of
  `auth.audiences` (401, no login redirect);
- refuses a token whose groups claim (`auth.groupsClaim`) names none of
  `auth.allowedGroups` (403), when the list is set;
- passes the caller's `Authorization` header to the server unchanged and streams
  responses as they come;
- serves `/.well-known/oauth-protected-resource` without a token: the issuer as
  authorization server and `auth.resourceUrl` as resource (RFC 9728), so an agent finds out
  that its own token fits;
- refuses every request but the health paths (401) while `auth.issuerUrl` is empty, so a
  switched-on check never fails open.

A gateway or agent that passes the caller's own token (for example through a Route) then
reaches the server safely from outside the project. The NetworkPolicy lets the pod reach
the issuer's port; the Route or gateway namespace still needs ingress
(`networkPolicy.ingressNamespaces`, or a project policy that admits the router). The
library's README describes every value of the block.

### Tools

| Tool | What it does |
|---|---|
| `describe_capabilities` | Formats, plan verbs per format, change modes, size limits and the connections the caller may use |
| `list_connections` | Connections and whether they can create documents |
| `create_document` | New `.docx`, `.pptx` or `.xlsx` in a connection, optionally with a first plan |
| `inspect_document` | Outline, paragraphs, nodes (tables, images, shapes, slides, comments, revisions, charts), styles; worksheets and cells for Excel |
| `find_in_document` | Text or regex search that returns anchors |
| `preview_plan` | Validates a plan and reports the changes, writes nothing |
| `apply_plan` | Applies a plan and saves the document |
| `compare_documents` | Differences between two Word documents, as tracked changes |
| `import_document_content` | Puts a document (base64) into the session connection |
| `export_document_content` | Returns a session document as base64 |
| `register_document`, `remove_document`, `open_document`, `edit_document` | Address a file of the `documents` connection by path (`officeagent.allowRegistration`) |
| `discover_template`, `preview_template_batch`, `populate_template_batch` | Fill Word content controls or PowerPoint shape names in a template, in batches |
| `preview_document_merge`, `merge_documents` | Assemble several Word documents into one |

`create_document_content`, `inspect_document_content` and `edit_document_content` carry the whole file in each call and are offered only with `officeagent.allowInlineContent`.

### What a plan can do

| Area | Word | PowerPoint | Excel |
|---|---|---|---|
| Text and formatting | change, insert and remove text; fonts, colours, alignment, paragraph and table styles, define styles | text in placeholders, shapes, tables and notes; fonts, colours, alignment | cell values and formulas |
| Tables | insert, rows, columns, merge, fill | insert, rows, columns | append table rows |
| Images, shapes, charts | inline images, background image, chart data | images, text-box shapes (position, size, fill, outline), native charts, media | - |
| Review | comments (add, reply, resolve, remove), tracked insertions, deletions and formatting changes, accept or reject revisions | comments (add, resolve) | cell notes |
| Layout | sections, page size, margins, breaks, headers, footers, footnotes | slides from layouts (insert, move, duplicate, remove), sections, transitions, animations, footers and slide numbers | inspect worksheets, tables and ranges |

PDF export and page images are not part of this server. Use the Office suite your project already runs (for example the ONLYOFFICE Document Server conversion API) on the exported file.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install office grncloud/mcp-officeagent
```

- `team`, `tenant` (default: empty): Install into `<tenant>-<team>` instead of the release namespace.
- `enabled` (default: true): Switch of the server inside a bundle chart.
- `nameOverride` (default: empty): Name of the objects and of the server on the gateway (tools `<name>_*`); empty: `mcp.server`.
- `image.repository`, `image.tag` (default: `ghcr.io/ilia-sokolov/officeagent-mcp`, `1.2.0` by digest): Server image published by the upstream project.
- `officeagent.session.enabled` (default: true): The in-memory `session` connection with `import_document_content` and `export_document_content`. `officeagent.session.maximumTotalBytes` (default: 100 MiB) caps what it holds at once.
- `officeagent.allowCreation` (default: true): Offer `create_document`, the template tools and `merge_documents`.
- `officeagent.allowRegistration` (default: true): Offer the tools that open a file of the `documents` connection by path.
- `officeagent.allowInlineContent` (default: false): Offer the tools that carry the whole document as base64 in every call.
- `officeagent.requirePlanSnapshot` (default: false): Refuse a plan without the snapshot eTag of its inspect, so a retried call cannot apply the same plan twice.
- `officeagent.strictToolSchemas` (default: true): List every tool parameter as required, as OpenAI strict function calling needs; `false` for clients that ask the user for every required input.
- `officeagent.calculateFormulas` (default: false): Calculate workbook formulas on save.
- `documents.enabled` (default: true): The `documents` connection on `/documents`. `documents.connectionId` (default: `documents`) is the id agents address it by.
- `documents.allowedExtensions` (default: `.docx`, `.pptx`, `.xlsx` and the image types `.png`, `.jpg`, `.jpeg`, `.gif`, `.svg`): Files the connection serves; an image in it can be inserted by id.
- `documents.defaultChangeMode` (default: `Direct`): Mode of an operation that states none. A deck refuses `Tracked`, so `Direct` serves both; a Word edit asks for `"mode": "Tracked"` itself.
- `documents.maximumBytes` (default: 100 MiB): Size cap per document.
- `documents.persistence.enabled` (default: true): A PersistentVolumeClaim of `documents.persistence.size` (default `1Gi`) in `documents.persistence.storageClass` (default: the cluster default); `false` uses an emptyDir. `documents.persistence.existingClaim` uses a claim you have. The chart's claim is kept on uninstall; `documents.persistence.annotations` (default: none) adds annotations to it.
- `port` (default: 13080): Container and Service port.
- `runAsUser` (default: empty): User ID of the server. OpenShift assigns one; on Kubernetes without that assignment set `1654`, the image's app user.
- `mcp.appProtocol` (default: `agentgateway.dev/mcp`): Service port appProtocol for gateway discovery.
- `mcp.labelDomain`, `mcp.server`, `mcp.bundle`, `mcp.category` (default: `mcp.openshift.eu`, `officeagent`, `officeagent`, `documents`): Catalog labels.
- `service.labels`, `service.annotations` (default: empty): Extra labels and annotations of the Service, for example the gateway's exposure label.
- `resources`: Requests and limits of the server.
- `networkPolicy.ingressNamespaces` (default: empty): Namespace selectors that may call the server besides its own project; the gateway's namespace belongs here.
- `auth.enabled` (default: false): The token check in front of the server, see Token check. Off, the server asks callers for no credential.
- `auth.issuerUrl` (default: empty): OpenID Connect issuer of the tokens, exactly as their `iss` claim. Empty with `auth.enabled`: every request but the health paths is refused. A platform that runs many releases sets it once, for example as a constant value of the product or the default of a Template.
- `auth.audiences` (default: empty, meaning the release namespace): A token's `aud` must name one of these. Each entry is a Helm template, so a parent chart can derive it, for example `"{{ .Release.Namespace }}"`.
- `auth.allowedGroups` (default: empty): A token's groups claim must name one of these; empty: any group. Each entry is a Helm template. `auth.groupsClaim` (default: `groups`) names the claim.
- `auth.resourceUrl` (default: empty, meaning the Service inside the cluster): Address callers use, named in the protected resource metadata; set it to the public URL when a Route publishes the server.
- `auth.upstreamPort` (default: 13081): Localhost port of the server behind the sidecar.
- `auth.skipAuthRoutes` (default: empty), `auth.upstreamTimeout` (default: `3600s`), `auth.flushInterval` (default: `100ms`), `auth.extraArgs` (default: empty): Further requests without a token, the longest wait for the server's response headers, the flush interval of a streamed response and further oauth2-proxy flags.
- `auth.existingSecret`, `auth.cookieSecret`, `auth.clientSecret` (default: empty): The secrets oauth2-proxy requires, generated once into `<name>-auth` and kept; a GitOps tool that renders without cluster access passes them.
- `auth.image.repository`, `auth.image.tag` (default: `quay.io/oauth2-proxy/oauth2-proxy`, `v7.15.5` by digest): Sidecar image.
- `auth.resources`: Requests and limits of the sidecar.

One replica with the `Recreate` strategy: session documents live in the memory of one process and the claim is ReadWriteOnce.

## Delivery instructions

1. Install the chart in the project of the agents. The defaults need no secret and no further value.
2. Publish the server through the project's MCP gateway: add the gateway's namespace to `networkPolicy.ingressNamespaces` and its exposure label to `service.labels`. To reach it from outside the cluster instead, set `auth.enabled`, `auth.issuerUrl`, `auth.audiences` and `auth.resourceUrl`, and put a Route with TLS in front of the Service; check that a request without a token gets 401.
3. Tell agents where documents come from. An agent that reads a file elsewhere (Nextcloud, a chat attachment) imports it into the `session` connection, edits it and writes the exported bytes back where it came from, so that store keeps the file's version history.

Prompt injection: a document can contain text that steers the agent that reads it. Treat documents from outside the project as untrusted input.
