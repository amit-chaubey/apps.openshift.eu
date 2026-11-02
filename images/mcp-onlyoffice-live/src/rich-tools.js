'use strict';

const { z } = require('zod');
const { richFor, scriptSource, commentView, slideCommentChange } = require('./rich');

const pathArg = z.string().min(1).describe('Path of the file in your own Nextcloud files, for example Documents/Plan.docx');
const hex = z.string().regex(/^#?[0-9a-fA-F]{6}$/).describe('Colour as RRGGBB hex');
const cellValue = z.union([z.string(), z.number(), z.boolean(), z.null()]);
const rowsArg = z.array(z.array(cellValue)).min(1).describe('Rows of cell values; the first row is the header when header is true');
const whereArg = z.enum(['end', 'start', 'cursor', 'after', 'before']).default('end').describe('Text document: where to insert');
const anchorArg = z.string().optional().describe('Text document: text of the paragraph to insert after or before. Spreadsheet: top left cell');
const slideArg = z.number().int().min(1).optional().describe('Presentation: slide number; default the last slide');
const sheetArg = z.string().optional().describe('Spreadsheet: sheet name; default the active sheet');

const textFormat = z.object({
  bold: z.boolean(), italic: z.boolean(), underline: z.boolean(), strikeout: z.boolean(),
  doubleStrikeout: z.boolean(), smallCaps: z.boolean(), caps: z.boolean(),
  font: z.string().describe('Font family'),
  size: z.number().positive().describe('Font size in points'),
  color: hex,
  highlight: z.enum(['black', 'blue', 'cyan', 'green', 'magenta', 'red', 'yellow', 'white', 'darkBlue', 'darkCyan', 'darkGreen', 'darkMagenta', 'darkRed', 'darkYellow', 'darkGray', 'lightGray', 'none']),
  vertAlign: z.enum(['baseline', 'subscript', 'superscript']),
}).partial();

const paragraphFormat = z.object({
  align: z.enum(['left', 'center', 'right', 'both']).describe('both: justified'),
  spacingBefore: z.number().min(0).describe('Points'),
  spacingAfter: z.number().min(0).describe('Points'),
  lineSpacing: z.number().positive().describe('Multiple of single line spacing, for example 1.5'),
  indentLeft: z.number().describe('Points'),
  indentRight: z.number().describe('Points'),
  firstLine: z.number().describe('First line indent in points; negative: hanging'),
  keepNext: z.boolean(), keepLines: z.boolean(), pageBreakBefore: z.boolean(),
  shading: hex,
}).partial();

const bad = message => Object.assign(new Error(message), { status: 400 });

function registerRich(tool, { sessions, config, credential, text, limitText }) {
  const open = path => sessions.get(credential, path);
  const needAnchor = (s, where, anchor) => {
    if (s.editor === 'word' && (where === 'after' || where === 'before') && !anchor) throw bad('anchor is required with after or before');
  };
  const checked = result => {
    if (result && result.error) throw Object.assign(new Error(result.error), { status: 422 });
    return result;
  };
  const size = v => JSON.stringify(v).length;

  tool('format_text', {
    title: 'Format text live',
    description: 'Formats text in an open text document or presentation: bold, italic, underline, strikeout, font, size, colour, '
      + 'highlight, superscript, a named style such as Heading 1, alignment, spacing, indents and shading. Select the text with search, '
      + 'or a whole paragraph of a text document with its element index from read.',
    inputSchema: {
      path: pathArg,
      search: z.string().min(1).optional().describe('Text to format'),
      paragraph: z.number().int().min(0).optional().describe('Text document: element index from read, instead of search'),
      all: z.boolean().default(true).describe('Every match (true) or the first (false)'),
      matchCase: z.boolean().default(true),
      text: textFormat.optional().describe('Character formatting'),
      para: paragraphFormat.optional().describe('Paragraph formatting of the paragraphs that hold the text'),
      style: z.string().optional().describe('Text document: paragraph style name, for example Heading 1'),
      slide: z.number().int().min(1).optional().describe('Presentation: limit to this slide'),
    },
  }, async ({ path, search, paragraph, all = true, matchCase = true, text: t, para, style, slide }) => {
    if (search === undefined && paragraph === undefined) throw bad('give search or paragraph');
    const s = await open(path);
    return text(checked(await sessions.command(s, richFor(s.editor, 'format'), { search, paragraph, all, matchCase, text: t, para, style, slide })));
  });

  tool('insert_table', {
    title: 'Insert a table live',
    description: 'Inserts a filled table. Text document: at where and anchor. Spreadsheet: a bordered block of cells from anchor. '
      + 'Presentation: on a slide.',
    inputSchema: {
      path: pathArg, rows: rowsArg, where: whereArg, anchor: anchorArg, slide: slideArg, sheet: sheetArg,
      header: z.boolean().default(true).describe('Make the first row a bold header'),
      headerColor: hex.optional().describe('Background of the header row'),
      headerTextColor: hex.optional().describe('Text colour of the header row'),
      style: z.string().optional().describe('Text document: table style name'),
      borders: z.union([z.literal(false), z.object({ size: z.number().positive().describe('Points').optional(), color: hex.optional() })]).optional()
        .describe('false: no borders; default thin black'),
    },
  }, async ({ path, rows, where = 'end', anchor, slide, sheet, header = true, headerColor, headerTextColor, style, borders }) => {
    limitText(JSON.stringify(rows));
    const s = await open(path);
    needAnchor(s, where, anchor);
    return text(checked(await sessions.command(s, richFor(s.editor, 'table'), { rows, where, anchor, slide, sheet, header, headerColor, headerTextColor, style, borders })));
  });

  tool('edit_table', {
    title: 'Edit a table live',
    description: 'Changes a table of an open text document with a list of operations, applied in order. op is one of: set (row, column, text), '
      + 'format (row, column, fill, verticalAlign, textFormat, paragraphFormat), addRows and addColumns (row, column, count, before), removeRow (row), '
      + 'removeColumn (column), merge (row, column, toRow, toColumn), columnWidth (column, width in points), rowHeight (row, height in points), '
      + 'borders (size in points, color), delete. Rows, columns and tables count from 0; read shows the tables.',
    inputSchema: {
      path: pathArg,
      table: z.number().int().min(0).default(0).describe('Table number in the document'),
      ops: z.array(z.object({
        op: z.enum(['set', 'format', 'addRows', 'addColumns', 'removeRow', 'removeColumn', 'merge', 'columnWidth', 'rowHeight', 'borders', 'delete']),
        row: z.number().int().min(0).optional(), column: z.number().int().min(0).optional(),
        toRow: z.number().int().min(0).optional(), toColumn: z.number().int().min(0).optional(),
        text: z.string().optional(), count: z.number().int().min(1).optional(), before: z.boolean().optional(),
        width: z.number().positive().optional(), height: z.number().positive().optional(),
        size: z.number().positive().optional(), color: hex.optional(), fill: hex.optional(),
        verticalAlign: z.enum(['top', 'center', 'bottom']).optional(),
        textFormat: textFormat.optional(), paragraphFormat: paragraphFormat.optional(),
      })).min(1),
    },
  }, async ({ path, table = 0, ops }) => {
    limitText(JSON.stringify(ops));
    const s = await open(path);
    return text(checked(await sessions.command(s, richFor(s.editor, 'editTable'), { table, ops })));
  });

  tool('insert_list', {
    title: 'Insert a list live',
    description: 'Inserts a bulleted or numbered list into an open text document, or as a text box on a slide.',
    inputSchema: {
      path: pathArg,
      items: z.array(z.string().min(1)).min(1),
      numbered: z.boolean().default(false),
      where: whereArg, anchor: anchorArg, slide: slideArg,
    },
  }, async ({ path, items, numbered = false, where = 'end', anchor, slide }) => {
    limitText(items.join('\n'));
    const s = await open(path);
    needAnchor(s, where, anchor);
    return text(checked(await sessions.command(s, richFor(s.editor, 'list'), { items, numbered, where, anchor, slide })));
  });

  const placeArgs = {
    width: z.number().positive().default(120).describe('Width in millimetres'),
    height: z.number().positive().default(80).describe('Height in millimetres'),
    where: whereArg, anchor: anchorArg, slide: slideArg, sheet: sheetArg,
    x: z.number().optional().describe('Presentation: left position in millimetres'),
    y: z.number().optional().describe('Presentation: top position in millimetres'),
  };

  tool('insert_image', {
    title: 'Insert an image live',
    description: 'Inserts an image from a public https URL or a base64 data URI into an open document.',
    inputSchema: {
      path: pathArg,
      src: z.string().regex(/^(https?:\/\/|data:image\/)/).describe('https URL or data:image URI'),
      align: z.enum(['left', 'center', 'right']).optional().describe('Text document: paragraph alignment'),
      ...placeArgs,
    },
  }, async ({ path, src, align, width = 120, height = 80, where = 'end', anchor, slide, sheet, x, y }) => {
    const s = await open(path);
    needAnchor(s, where, anchor);
    return text(checked(await sessions.command(s, richFor(s.editor, 'image'), { src, align, width, height, where, anchor, slide, sheet, x, y })));
  });

  tool('insert_chart', {
    title: 'Insert a chart live',
    description: 'Inserts a chart. Text document or presentation: give series, seriesNames and categories. Spreadsheet: give dataRange, '
      + 'and the chart reads the cells. type is for example bar, barStacked, horizontalBar, lineNormal, pie, doughnut, area, scatter.',
    inputSchema: {
      path: pathArg,
      type: z.string().default('bar'),
      series: z.array(z.array(z.number())).optional().describe('One array of values per series'),
      seriesNames: z.array(z.string()).optional(),
      categories: z.array(z.string()).optional(),
      dataRange: z.string().optional().describe("Spreadsheet: cells with the data, for example 'Sheet1'!$A$1:$D$5"),
      seriesInRows: z.boolean().default(false).describe('Spreadsheet: a series per row instead of per column'),
      styleIndex: z.number().int().min(1).max(48).optional(),
      ...placeArgs,
    },
  }, async ({ path, type = 'bar', series, seriesNames, categories, dataRange, seriesInRows = false, styleIndex, width = 120, height = 80, where = 'end', anchor, slide, sheet, x, y }) => {
    const s = await open(path);
    needAnchor(s, where, anchor);
    if (s.editor === 'cell' ? !dataRange : !series) throw bad(s.editor === 'cell' ? 'dataRange is required in a spreadsheet' : 'series is required');
    return text(checked(await sessions.command(s, richFor(s.editor, 'chart'), { type, series, seriesNames, categories, dataRange, seriesInRows, styleIndex, width, height, where, anchor, slide, sheet, x, y })));
  });

  tool('write_cells', {
    title: 'Write cells live',
    description: 'Writes values or formulas (text starting with =) into a spreadsheet, from a start cell; null leaves a cell as it is.',
    inputSchema: { path: pathArg, start: z.string().min(1).describe('Top left cell, for example B2'), rows: rowsArg, sheet: sheetArg },
  }, async ({ path, start, rows, sheet }) => {
    limitText(JSON.stringify(rows));
    const s = await open(path);
    return text(checked(await sessions.command(s, richFor(s.editor, 'write'), { start, rows, sheet })));
  });

  tool('format_cells', {
    title: 'Format cells live',
    description: 'Formats a range of a spreadsheet: font, colours, alignment, wrap, number format, borders, merge, column width and row height.',
    inputSchema: {
      path: pathArg,
      range: z.string().min(1).describe('Range, for example A1:D1'),
      sheet: sheetArg,
      bold: z.boolean().optional(), italic: z.boolean().optional(),
      underline: z.enum(['none', 'single', 'singleAccounting', 'double', 'doubleAccounting']).optional(),
      strikeout: z.boolean().optional(),
      font: z.string().optional(), size: z.number().positive().optional().describe('Points'),
      color: hex.optional(), fill: hex.optional(),
      align: z.enum(['left', 'center', 'right', 'justify']).optional(),
      verticalAlign: z.enum(['top', 'center', 'bottom', 'justify', 'distributed']).optional(),
      wrap: z.boolean().optional(),
      numberFormat: z.string().optional().describe('For example #,##0.00 or 0%'),
      columnWidth: z.number().positive().optional().describe('Characters'),
      rowHeight: z.number().positive().optional().describe('Points'),
      merge: z.boolean().optional().describe('true: merge the range; false: split it'),
      borders: z.object({
        edges: z.array(z.enum(['Top', 'Bottom', 'Left', 'Right', 'InsideHorizontal', 'InsideVertical', 'DiagonalUp', 'DiagonalDown'])).optional(),
        style: z.enum(['None', 'Double', 'Hair', 'DashDotDot', 'DashDot', 'Dotted', 'Dashed', 'Thin', 'MediumDashDotDot', 'SlantDashDot', 'MediumDashDot', 'MediumDashed', 'Medium', 'Thick']).optional(),
        color: hex.optional(),
      }).optional(),
    },
  }, async ({ path, range, sheet, ...format }) => {
    const s = await open(path);
    return text(checked(await sessions.command(s, richFor(s.editor, 'format'), { range, sheet, format })));
  });

  const allComments = async s => (await sessions.method(s, 'GetAllComments', [])) || [];
  const findComment = async (s, id) => {
    const found = (await allComments(s)).find(c => c.Id === id);
    if (!found) throw Object.assign(new Error(`no comment ${id}; list them with comments`), { status: 404 });
    return found;
  };

  const changeComment = (s, id, data) => (s.editor === 'slide'
    ? sessions.editorEval(s, slideCommentChange, [id, data])
    : sessions.method(s, 'ChangeComment', [id, data]));

  tool('comments', {
    title: 'Read the comments',
    description: 'Lists every comment of an open document with its author, time, quoted text, resolved state and replies, including what other people wrote a moment ago.',
    inputSchema: { path: pathArg, includeResolved: z.boolean().default(true) },
  }, async ({ path, includeResolved = true }) => {
    const s = await open(path);
    const list = (await allComments(s)).map(commentView).filter(c => includeResolved || !c.resolved);
    return text({ comments: list });
  });

  tool('comment_reply', {
    title: 'Reply to a comment',
    description: 'Adds a reply, under your name, to a comment. People who have the document open see it at once.',
    inputSchema: { path: pathArg, id: z.string().min(1).describe('Comment id from comments'), reply: z.string().min(1) },
  }, async ({ path, id, reply }) => {
    limitText(reply);
    const s = await open(path);
    const c = await findComment(s, id);
    if (s.editor === 'cell') {
      checked(await sessions.command(s, richFor('cell', 'commentChange'), { id, reply, author: s.displayName }));
      return text({ replied: true, id, replies: (c.Data.Replies || []).length + 1 });
    }
    const data = { ...c.Data, Replies: [...(c.Data.Replies || []), { UserName: s.displayName, Text: reply, Time: String(Date.now()), Solved: false }] };
    await changeComment(s, id, data);
    return text({ replied: true, id, replies: data.Replies.length });
  });

  tool('comment_resolve', {
    title: 'Resolve a comment',
    description: 'Marks a comment as resolved, or opens it again with resolved false.',
    inputSchema: { path: pathArg, id: z.string().min(1), resolved: z.boolean().default(true) },
  }, async ({ path, id, resolved = true }) => {
    const s = await open(path);
    const c = await findComment(s, id);
    if (s.editor === 'cell') checked(await sessions.command(s, richFor('cell', 'commentChange'), { id, resolved }));
    else await changeComment(s, id, { ...c.Data, Solved: resolved });
    return text({ id, resolved });
  });

  tool('comment_delete', {
    title: 'Delete a comment',
    description: 'Deletes a comment and its replies.',
    inputSchema: { path: pathArg, id: z.string().min(1) },
  }, async ({ path, id }) => {
    const s = await open(path);
    await findComment(s, id);
    await sessions.method(s, 'RemoveComments', [[id]]);
    return text({ deleted: id });
  });

  if (config.scripts) {
    tool('run', {
      title: 'Run a document script',
      description: 'Runs a script of the ONLYOFFICE Office API (Api.GetDocument, Api.GetActiveSheet, Api.GetPresentation and the rest) in your editor, for anything '
        + 'the other tools do not cover: headers and footers, footnotes, content controls, shapes, conditional formatting, sheets, slide layouts. '
        + 'Write a function body: it reads the variable data and ends with return of a JSON value. A thrown error comes back as the error of the tool. '
        + 'The editor refuses network requests from a script.',
      inputSchema: {
        path: pathArg,
        script: z.string().min(1).describe('Body of a JavaScript function'),
        data: z.unknown().optional().describe('JSON value the script reads as data'),
      },
    }, async ({ path, script, data }) => {
      limitText(script);
      if (data !== undefined) limitText(JSON.stringify(data));
      try { new Function(script); } catch (e) { throw bad(`the script does not parse: ${e.message}`); }
      const s = await open(path);
      const result = await sessions.command(s, scriptSource(script), { data: data === undefined ? null : data });
      if (size(result ?? null) > config.maxTextLength) throw Object.assign(new Error(`the result is longer than ${config.maxTextLength} characters`), { status: 413 });
      return text({ result: checked(result) });
    });
  }
}

module.exports = { registerRich };
