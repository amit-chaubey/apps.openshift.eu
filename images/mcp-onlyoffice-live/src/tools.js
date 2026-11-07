'use strict';

const { z } = require('zod');
const { forEditor, typingChunks } = require('./commands');
const { registerRich } = require('./rich-tools');

const pathArg = z.string().min(1).describe('Path of the file in your own Nextcloud files, for example Documents/Plan.docx');

function text(value) {
  return { content: [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value, null, 2) }] };
}

function failure(err) {
  return { isError: true, content: [{ type: 'text', text: `${err.status ? err.status + ' ' : ''}${err.message}` }] };
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

// Registers the tools on one MCP server instance; credential: the caller's Authorization
function register(server, { sessions, nextcloud, config, credential }) {
  const tool = (name, spec, handler) => server.registerTool(name, spec, async args => {
    try { return await handler(args || {}); } catch (err) { return failure(err); }
  });
  const limitText = t => {
    if (t.length > config.maxTextLength) throw Object.assign(new Error(`text longer than ${config.maxTextLength} characters`), { status: 413 });
    return t;
  };

  registerRich(tool, { sessions, config, credential, text, limitText });

  tool('open', {
    title: 'Open a document live',
    description: 'Opens an office document from your Nextcloud files in ONLYOFFICE as yourself and joins its live co-editing session. '
      + 'People who have the document open see your name, your cursor and every edit as you make it. Call this before the other tools.',
    inputSchema: { path: pathArg },
  }, async ({ path }) => text(await sessions.open(credential, path)));

  tool('create', {
    title: 'Create a document and open it live',
    description: 'Creates a new empty document (.docx, .xlsx or .pptx) at the path from the ONLYOFFICE template and opens it live.',
    inputSchema: { path: pathArg },
  }, async ({ path }) => text(await sessions.open(credential, path, { create: true })));

  tool('sessions', {
    title: 'List my live sessions',
    description: 'Lists the documents you have open live through this server.',
    inputSchema: {},
  }, async () => text({ sessions: sessions.list((await nextcloud.identity(credential)).id) }));

  tool('read', {
    title: 'Read the live document',
    description: 'Returns the current content of an open document, including edits other people made a moment ago: '
      + 'paragraphs for a text document, the used cells of the active sheet, the texts of every slide.',
    inputSchema: { path: pathArg },
  }, async ({ path }) => {
    const s = await sessions.get(credential, path);
    return text(await sessions.command(s, forEditor(s.editor).read, {}, false));
  });

  tool('insert', {
    title: 'Insert text live',
    description: 'Inserts text into an open document. Text document: where is end, start, cursor (your cursor), after or before '
      + '(the paragraph that contains anchor). Spreadsheet: anchor is a cell such as B4, or where end appends a row. '
      + 'Presentation: adds a slide with the text. typing true writes word by word so people watch it being typed.',
    inputSchema: {
      path: pathArg,
      text: z.string().min(1).describe('Text to insert; a newline starts a new paragraph or row'),
      where: z.enum(['end', 'start', 'cursor', 'after', 'before']).default('end').describe('Where to insert'),
      anchor: z.string().optional().describe('Text of the paragraph to insert after or before, or a cell address'),
      typing: z.boolean().default(false).describe('Write word by word, visible as typing'),
    },
  }, async ({ path, text: t, where = 'end', anchor, typing = false }) => {
    limitText(t);
    const s = await sessions.get(credential, path);
    if ((where === 'after' || where === 'before') && !anchor && s.editor === 'word') throw Object.assign(new Error('anchor is required with after or before'), { status: 400 });
    if (typing && s.editor === 'word' && where === 'end') {
      await sessions.command(s, forEditor('word').typingStart, {});
      let newParagraph = false, n = 0;
      for (const chunk of typingChunks(t)) {
        if (chunk === '\n') { newParagraph = true; continue; }
        await sessions.command(s, forEditor('word').typingChunk, { text: chunk, newParagraph });
        newParagraph = false; n++;
        await sleep(config.typingDelayMs);
      }
      return text({ inserted: true, typed: true, chunks: n });
    }
    if (typing && where === 'cursor') {
      let n = 0;
      for (const chunk of typingChunks(t)) {
        await sessions.method(s, 'PasteText', [chunk === '\n' ? '\r' : chunk]);
        n++;
        await sleep(config.typingDelayMs);
      }
      return text({ inserted: true, typed: true, chunks: n });
    }
    return text(await sessions.command(s, forEditor(s.editor).insert, { text: t, where, anchor: anchor || '' }));
  });

  tool('replace', {
    title: 'Replace text live',
    description: 'Replaces text in an open document, the first match or every match. With track changes on, '
      + 'the replacement shows as a tracked change people can accept or reject.',
    inputSchema: {
      path: pathArg,
      search: z.string().min(1).describe('Text to find'),
      replacement: z.string().describe('Text to put in its place'),
      all: z.boolean().default(true).describe('Every match (true) or the first (false)'),
      matchCase: z.boolean().default(true),
    },
  }, async ({ path, search, replacement, all = true, matchCase = true }) => {
    limitText(replacement);
    const s = await sessions.get(credential, path);
    if (s.editor === 'word' && !all) {
      const found = await sessions.command(s, forEditor('word').selectFirst, { search, matchCase }, false);
      if (!found) return text({ replaced: 0 });
      await sessions.method(s, 'PasteText', [replacement]);
      return text({ replaced: 1, matches: found });
    }
    if (s.editor !== 'word') {
      return text(await sessions.command(s, forEditor(s.editor).replace, { search, replacement, all, matchCase }));
    }
    let before = null;
    if (s.editor === 'word') before = await sessions.command(s, forEditor('word').count, { search, matchCase }, false);
    await sessions.method(s, 'SearchAndReplace', [{ searchString: search, replaceString: replacement, matchCase }]);
    return text({ replaced: before === null ? 'all' : before });
  });

  tool('comment', {
    title: 'Comment live',
    description: 'Adds a comment, under your name, to the first occurrence of a text (text document), to a cell (spreadsheet, search is the cell address) '
      + 'or to the current slide (presentation, search is what the comment is about).',
    inputSchema: {
      path: pathArg,
      search: z.string().min(1).describe('Text to comment on, or a cell address'),
      comment: z.string().min(1).describe('The comment'),
    },
  }, async ({ path, search, comment }) => {
    limitText(comment);
    const s = await sessions.get(credential, path);
    if (s.editor === 'word') {
      // through the editor's own comment action, which takes the co-editing lock
      const found = await sessions.command(s, forEditor('word').selectFirst, { search, matchCase: true }, false);
      if (!found) return text({ added: false, error: 'text not found' });
      const id = await sessions.method(s, 'AddComment', [{ Text: comment, UserName: s.displayName, QuoteText: search, Time: String(Date.now()) }]);
      return text({ added: !!id, commentId: id || null, matches: found });
    }
    if (s.editor === 'slide') {
      const id = await sessions.method(s, 'AddComment', [{ Text: comment, UserName: s.displayName, QuoteText: search, Time: String(Date.now()) }]);
      return text({ added: !!id, commentId: id || null });
    }
    return text(await sessions.command(s, forEditor(s.editor).comment, { search, comment, author: s.displayName }));
  });

  tool('track_changes', {
    title: 'Switch track changes',
    description: 'Switches track changes on or off in an open text document; with it on, every later edit is a tracked change.',
    inputSchema: { path: pathArg, enabled: z.boolean() },
  }, async ({ path, enabled }) => {
    const s = await sessions.get(credential, path);
    if (s.editor !== 'word') throw Object.assign(new Error('track changes exist in text documents only'), { status: 400 });
    return text(await sessions.command(s, forEditor('word').trackChanges, { enabled }));
  });

  tool('save', {
    title: 'Save now',
    description: 'Sends your pending edits to the Document Server at once. The file in Nextcloud is updated when the last '
      + 'participant leaves the document, or at once when Nextcloud keeps intermediate versions (force save).',
    inputSchema: { path: pathArg },
  }, async ({ path }) => {
    const s = await sessions.get(credential, path);
    const done = await sessions.editorEval(s, () => {
      const api = window.Asc && window.Asc.editor;
      if (api && typeof api.asc_Save === 'function') { api.asc_Save(); return true; }
      return false;
    });
    return text({ saveRequested: done });
  });

  if (config.screenshots) {
    tool('view', {
      title: 'See the document',
      description: 'Returns a screenshot of your editor window: the document as you see it, with other participants\' cursors.',
      inputSchema: { path: pathArg },
    }, async ({ path }) => {
      const s = await sessions.get(credential, path);
      const png = await s.page.screenshot({ type: 'png' });
      return { content: [{ type: 'image', data: png.toString('base64'), mimeType: 'image/png' }] };
    });
  }

  tool('close', {
    title: 'Leave the document',
    description: 'Sends your pending edits and leaves the live session of a document. Nextcloud gets the file when the last participant leaves.',
    inputSchema: { path: pathArg },
  }, async ({ path }) => text(await sessions.close(credential, path)));

  tool('file_status', {
    title: 'Is the file open in an editor',
    description: 'Tells whether a file is open in a live ONLYOFFICE editing session or locked. Call it before you write the file '
      + 'by any other way (a WebDAV upload, an OfficeAgent export): while the file is open in the editor, its next save overwrites '
      + 'such a write. Edit an open file through this server instead.',
    inputSchema: { path: pathArg },
  }, async ({ path }) => {
    const me = await nextcloud.identity(credential);
    const p = require('./nextcloud').normalizePath(path);
    const st = await nextcloud.fileState(credential, me.id, p);
    const liveHere = sessions.list(me.id).some(x => x.path === p);
    const openInEditor = st.locked && st.lockOwnerType === 1;
    return text({
      path: p, ...st, liveSessionHere: liveHere,
      safeToOverwrite: !st.locked && !liveHere,
      advice: st.locked || liveHere
        ? `Do not write this file directly: it is ${openInEditor ? 'open in ' + (st.lockOwnerEditor || 'an editor') : 'locked by ' + (st.lockOwnerDisplayName || st.lockOwner || 'someone')}. Edit it with the live tools.`
        : 'No editor holds the file; a direct write is safe now.',
    });
  });
}

module.exports = { register };
