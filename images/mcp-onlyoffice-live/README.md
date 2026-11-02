# mcp-onlyoffice-live

Image of an MCP server that lets an agent edit an office document live, as itself, in the
same ONLYOFFICE co-editing session as the people who have it open in Nextcloud. They see the
agent's name, its cursor and every edit as it happens.

## How it works

- The server opens the file through Nextcloud Direct Editing as the calling account, in a
  headless Chromium (Playwright), and so joins the document's co-editing session.
- Edits run through the **agent-bridge** plugin (`plugin/`): an invisible system plugin that
  the Document Server serves from its `sdkjs-plugins` folder. The server calls the plugin API
  (`callCommand` with the Document Builder API, `executeMethod`) inside the plugin's frame.
  In a person's editor the plugin loads and stays idle.
- The server holds no Document Server secret. It forwards the caller's `Authorization`
  header (a bearer token Nextcloud `user_oidc` accepts, or Basic with an app password) to
  Nextcloud; Nextcloud decides who the caller is and what it may open.

## Tools

| Tool | Does |
|---|---|
| `open` | Opens a file live as the caller; returns the editor type |
| `create` | Creates an empty `.docx`, `.xlsx` or `.pptx` from the ONLYOFFICE template and opens it |
| `read` | Current content, including edits other people made a moment ago |
| `insert` | Text at the end, start, cursor, or after or before a paragraph; a cell; a new slide. `typing: true` writes word by word |
| `replace` | First or every match; a tracked change while track changes is on |
| `comment` | A comment under the caller's name on a text, a cell or a slide |
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
| `track_changes` | Switches track changes on or off |
| `save` | Sends pending edits to the Document Server at once |
| `view` | A screenshot of the caller's editor window |
| `close` | Sends the pending edits and leaves the session |
| `sessions` | The caller's open sessions |
| `file_status` | Whether the file is open in an editor or locked (Nextcloud files_lock), before any direct write |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `NEXTCLOUD_URL` | `http://nextcloud:8080` | Nextcloud base URL the server calls |
| `NEXTCLOUD_PUBLIC_URL` | empty | Origin the browser opens editor pages on, when `NEXTCLOUD_URL` is internal; empty: the link Nextcloud returns |
| `NEXTCLOUD_USER`, `NEXTCLOUD_PASSWORD` | empty | Account for callers that send no `Authorization` |
| `PORT` | `13081` | Listen port; MCP on `/` and `/mcp`, `GET /healthz` |
| `MAX_SESSIONS` | `4` | Open documents across all callers; each costs 300 to 600 MiB |
| `MAX_SESSIONS_PER_USER` | `2` | Open documents per account |
| `IDLE_TIMEOUT_SECONDS` | `900` | A session unused this long is closed |
| `OPEN_TIMEOUT_SECONDS` | `120` | Time the editor and the plugin get to load |
| `TYPING_DELAY_MS` | `120` | Pause between words with `typing: true` |
| `SCREENSHOTS` | `true` | Offers the `view` tool |

## Build

GitHub Actions workflow `images.yaml` tests and builds the image on every pull request and
pushes `ghcr.io/hoolia/mcp-onlyoffice-live:<version of package.json>` from `main` when that
version does not exist yet. A change to the image raises the version in `package.json`.

## Test

```bash
cd images/mcp-onlyoffice-live
npm ci --ignore-scripts
npm test
```
