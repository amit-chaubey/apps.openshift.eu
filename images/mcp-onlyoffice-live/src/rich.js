'use strict';

// Document Builder snippets for formatting, tables, lists, images, charts and cells. Units at
// the tool surface: points and millimetres; the editor API takes half points, twips and EMU.

const guard = body => `function () {
  try {
    ${body}
  } catch (e) { return { error: String(e && e.message || e) }; }
}`;

const helpers = `
  var s = Asc.scope;
  function rgb(h) { var n = parseInt(String(h).replace('#', ''), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  function color(h) { var c = rgb(h); return Api.RGB(c[0], c[1], c[2]); }
  function has(o, k) { return o && o[k] !== undefined && o[k] !== null; }
  function textProps(t, p) {
    if (has(p, 'bold')) t.SetBold(p.bold);
    if (has(p, 'italic')) t.SetItalic(p.italic);
    if (has(p, 'underline')) t.SetUnderline(p.underline);
    if (has(p, 'strikeout')) t.SetStrikeout(p.strikeout);
    if (has(p, 'doubleStrikeout')) t.SetDoubleStrikeout(p.doubleStrikeout);
    if (has(p, 'smallCaps')) t.SetSmallCaps(p.smallCaps);
    if (has(p, 'caps')) t.SetCaps(p.caps);
    if (has(p, 'font')) t.SetFontFamily(p.font);
    if (has(p, 'size')) t.SetFontSize(Math.round(p.size * 2));
    if (has(p, 'color')) t.SetColor(color(p.color));
    if (has(p, 'highlight')) t.SetHighlight(p.highlight);
    if (has(p, 'vertAlign')) t.SetVertAlign(p.vertAlign);
  }
  function paraProps(q, p) {
    if (has(p, 'align')) q.SetJc(p.align);
    if (has(p, 'spacingBefore')) q.SetSpacingBefore(Math.round(p.spacingBefore * 20));
    if (has(p, 'spacingAfter')) q.SetSpacingAfter(Math.round(p.spacingAfter * 20));
    if (has(p, 'lineSpacing')) q.SetSpacingLine(Math.round(p.lineSpacing * 240), 'auto');
    if (has(p, 'indentLeft')) q.SetIndLeft(Math.round(p.indentLeft * 20));
    if (has(p, 'indentRight')) q.SetIndRight(Math.round(p.indentRight * 20));
    if (has(p, 'firstLine')) q.SetIndFirstLine(Math.round(p.firstLine * 20));
    if (has(p, 'keepNext')) q.SetKeepNext(p.keepNext);
    if (has(p, 'keepLines')) q.SetKeepLines(p.keepLines);
    if (has(p, 'pageBreakBefore')) q.SetPageBreakBefore(p.pageBreakBefore);
    if (has(p, 'shading')) q.SetShd('clear', color(p.shading));
  }
  function setCell(cell, text) {
    var c = cell.GetContent(), para = c.GetElement(0);
    para.RemoveAllElements();
    para.AddText(text === undefined || text === null ? '' : String(text));
    return para;
  }
`;

const wordPlace = `
  function place(d, el) {
    if (s.where === 'end' || !s.where) { d.Push(el); return true; }
    if (s.where === 'start') { d.AddElement(0, el); return true; }
    if (s.where === 'cursor') { d.InsertContent([el]); return true; }
    var found = d.Search(s.anchor, true);
    if (!found || !found.length) return false;
    var para = found[0].GetParagraph(0), parent = para.GetParent();
    parent.AddElement(para.GetPosInParent() + (s.where === 'before' ? 0 : 1), el);
    return true;
  }
  function headerRow(t, cols) {
    for (var c = 0; c < cols; c++) {
      var cell = t.GetCell(0, c);
      if (s.headerColor) cell.SetBackgroundColor(color(s.headerColor));
      var p = cell.GetContent().GetElement(0);
      p.SetBold(true);
      if (s.headerTextColor) p.SetColor(color(s.headerTextColor));
    }
  }
`;

const word = {
  format: guard(`${helpers}
    var d = Api.GetDocument(), targets = [], paras = [];
    if (has(s, 'paragraph')) {
      var e = d.GetElement(s.paragraph);
      if (!e || e.GetClassType() !== 'paragraph') return { error: 'element ' + s.paragraph + ' is not a paragraph' };
      targets = [e]; paras = [e];
    } else {
      var r = d.Search(s.search, s.matchCase) || [];
      targets = s.all ? r : r.slice(0, 1);
      for (var i = 0; i < targets.length; i++) paras = paras.concat(targets[i].GetAllParagraphs());
    }
    if (!targets.length) return { formatted: 0, error: 'text not found' };
    for (var j = 0; j < targets.length; j++) textProps(targets[j], s.text || {});
    var style = s.style ? d.GetStyle(s.style) : null;
    if (s.style && !style) return { formatted: 0, error: 'style ' + s.style + ' not found' };
    for (var k = 0; k < paras.length; k++) {
      if (style) paras[k].SetStyle(style);
      paraProps(paras[k], s.para || {});
    }
    return { formatted: targets.length };`),
  table: guard(`${helpers}${wordPlace}
    var d = Api.GetDocument(), rows = s.rows, cols = 0;
    for (var i = 0; i < rows.length; i++) cols = Math.max(cols, rows[i].length);
    var t = Api.CreateTable(rows.length, cols);
    t.SetWidth('percent', 100);
    if (s.style) { var st = d.GetStyle(s.style); if (st) t.SetStyle(st); }
    if (s.borders !== false) {
      var b = rgb((s.borders && s.borders.color) || '000000');
      t.SetTableBorderAll('single', Math.round(((s.borders && s.borders.size) || 0.5) * 8), 0, b[0], b[1], b[2]);
    }
    for (var r = 0; r < rows.length; r++) for (var c = 0; c < rows[r].length; c++) setCell(t.GetCell(r, c), rows[r][c]);
    if (s.header) headerRow(t, cols);
    if (!place(d, t)) return { inserted: false, error: 'anchor text not found' };
    return { inserted: true, rows: rows.length, columns: cols, table: d.GetAllTables().length - 1 };`),
  editTable: guard(`${helpers}
    var d = Api.GetDocument(), t = d.GetAllTables()[s.table], done = 0;
    if (!t) return { error: 'no table ' + s.table };
    function cell(o) {
      var c = t.GetCell(o.row, o.column === undefined ? 0 : o.column);
      if (!c) throw new Error('no cell at row ' + o.row + ' column ' + o.column);
      return c;
    }
    for (var i = 0; i < s.ops.length; i++) {
      var o = s.ops[i], x;
      if (o.op === 'set') { x = setCell(cell(o), o.text); textProps(x, o.textFormat || {}); paraProps(x, o.paragraphFormat || {}); }
      else if (o.op === 'format') {
        x = cell(o);
        if (o.fill) x.SetBackgroundColor(color(o.fill));
        if (o.verticalAlign) x.SetVerticalAlign(o.verticalAlign);
        var ps = x.GetContent().GetAllParagraphs();
        for (var j = 0; j < ps.length; j++) { textProps(ps[j], o.textFormat || {}); paraProps(ps[j], o.paragraphFormat || {}); }
      }
      else if (o.op === 'addRows') t.AddRows(cell(o), o.count || 1, !!o.before);
      else if (o.op === 'addColumns') t.AddColumns(cell(o), o.count || 1, !!o.before);
      else if (o.op === 'removeRow') t.RemoveRow(cell(o));
      else if (o.op === 'removeColumn') t.RemoveColumn(cell(o));
      else if (o.op === 'merge') {
        var cs = [];
        for (var r = o.row; r <= o.toRow; r++) for (var c = o.column; c <= o.toColumn; c++) cs.push(t.GetCell(r, c));
        if (!t.MergeCells(cs)) throw new Error('these cells cannot be merged');
      }
      else if (o.op === 'columnWidth') t.SetColumnWidth(o.column, Math.round(o.width * 20));
      else if (o.op === 'rowHeight') t.GetRow(o.row).SetHeight('atLeast', Math.round(o.height * 20));
      else if (o.op === 'borders') { var b = rgb(o.color || '000000'); t.SetTableBorderAll('single', Math.round((o.size || 0.5) * 8), 0, b[0], b[1], b[2]); }
      else if (o.op === 'delete') { t.Delete(); done++; break; }
      else throw new Error('unknown op ' + o.op);
      done++;
    }
    return { applied: done };`),
  list: guard(`${helpers}${wordPlace}
    var d = Api.GetDocument(), n = d.CreateNumbering(s.numbered ? 'numbered' : 'bullet'), lvl = n.GetLevel(0);
    var first = null, prev = null;
    for (var i = 0; i < s.items.length; i++) {
      var p = Api.CreateParagraph();
      p.AddText(s.items[i]);
      p.SetNumbering(lvl);
      if (!prev) { if (!place(d, p)) return { inserted: 0, error: 'anchor text not found' }; first = p; }
      else prev.InsertParagraph(p, 'after', true);
      prev = p;
    }
    return { inserted: s.items.length };`),
  image: guard(`${helpers}${wordPlace}
    var d = Api.GetDocument(), p = Api.CreateParagraph();
    p.AddDrawing(Api.CreateImage(s.src, s.width * 36000, s.height * 36000));
    if (s.align) p.SetJc(s.align);
    if (!place(d, p)) return { inserted: false, error: 'anchor text not found' };
    return { inserted: true };`),
  chart: guard(`${helpers}${wordPlace}
    var d = Api.GetDocument(), p = Api.CreateParagraph();
    p.AddDrawing(Api.CreateChart(s.type, s.series, s.seriesNames, s.categories, s.width * 36000, s.height * 36000, s.styleIndex || 2));
    if (!place(d, p)) return { inserted: false, error: 'anchor text not found' };
    return { inserted: true };`),
};

const cellSheet = `
  var ws = s.sheet ? Api.GetSheet(s.sheet) : Api.GetActiveSheet();
  if (!ws) return { error: 'no sheet ' + s.sheet };
`;

const cell = {
  write: guard(`${helpers}${cellSheet}
    var start = ws.GetRange(s.start), row0 = start.GetRow() - 1, col0 = start.GetCol() - 1, n = 0;
    for (var r = 0; r < s.rows.length; r++) for (var c = 0; c < s.rows[r].length; c++) {
      var v = s.rows[r][c];
      if (v === null || v === undefined) continue;
      ws.GetRangeByNumber(row0 + r, col0 + c).SetValue(v); n++;
    }
    return { written: n, sheet: ws.GetName() };`),
  format: guard(`${helpers}${cellSheet}
    var r = ws.GetRange(s.range), p = s.format || {};
    if (has(p, 'bold')) r.SetBold(p.bold);
    if (has(p, 'italic')) r.SetItalic(p.italic);
    if (has(p, 'underline')) r.SetUnderline(p.underline);
    if (has(p, 'strikeout')) r.SetStrikeout(p.strikeout);
    if (has(p, 'font')) r.SetFontName(p.font);
    if (has(p, 'size')) r.SetFontSize(p.size);
    if (has(p, 'color')) r.SetFontColor(color(p.color));
    if (has(p, 'fill')) r.SetFillColor(color(p.fill));
    if (has(p, 'align')) r.SetAlignHorizontal(p.align);
    if (has(p, 'verticalAlign')) r.SetAlignVertical(p.verticalAlign);
    if (has(p, 'wrap')) r.SetWrap(p.wrap);
    if (has(p, 'numberFormat')) r.SetNumberFormat(p.numberFormat);
    if (has(p, 'columnWidth')) r.SetColumnWidth(p.columnWidth);
    if (has(p, 'rowHeight')) r.SetRowHeight(p.rowHeight);
    if (p.merge === true) r.Merge(false);
    if (p.merge === false) r.UnMerge();
    if (p.borders) {
      var edges = p.borders.edges || ['Top', 'Bottom', 'Left', 'Right', 'InsideHorizontal', 'InsideVertical'];
      for (var i = 0; i < edges.length; i++) r.SetBorders(edges[i], p.borders.style || 'Thin', color(p.borders.color || '000000'));
    }
    return { formatted: r.GetAddress(false, false) };`),
  table: guard(`${helpers}${cellSheet}
    var start = ws.GetRange(s.anchor || 'A1'), row0 = start.GetRow() - 1, col0 = start.GetCol() - 1, cols = 0;
    for (var r = 0; r < s.rows.length; r++) {
      cols = Math.max(cols, s.rows[r].length);
      for (var c = 0; c < s.rows[r].length; c++) ws.GetRangeByNumber(row0 + r, col0 + c).SetValue(s.rows[r][c]);
    }
    var range = ws.GetRange(ws.GetRangeByNumber(row0, col0), ws.GetRangeByNumber(row0 + s.rows.length - 1, col0 + cols - 1));
    if (s.borders !== false) {
      var edges = ['Top', 'Bottom', 'Left', 'Right', 'InsideHorizontal', 'InsideVertical'];
      for (var i = 0; i < edges.length; i++) range.SetBorders(edges[i], 'Thin', color((s.borders && s.borders.color) || '000000'));
    }
    if (s.header) {
      var head = ws.GetRange(ws.GetRangeByNumber(row0, col0), ws.GetRangeByNumber(row0, col0 + cols - 1));
      head.SetBold(true);
      if (s.headerColor) head.SetFillColor(color(s.headerColor));
      if (s.headerTextColor) head.SetFontColor(color(s.headerTextColor));
    }
    return { inserted: true, range: range.GetAddress(false, false) };`),
  image: guard(`${helpers}${cellSheet}
    var a = ws.GetRange(s.anchor || 'A1');
    ws.AddImage(s.src, s.width * 36000, s.height * 36000, a.GetCol() - 1, 0, a.GetRow() - 1, 0);
    return { inserted: true };`),
  chart: guard(`${helpers}${cellSheet}
    var a = ws.GetRange(s.anchor || 'A1');
    ws.AddChart(s.dataRange, !!s.seriesInRows, s.type, s.styleIndex || 2, s.width * 36000, s.height * 36000, a.GetCol() - 1, 0, a.GetRow() - 1, 0);
    return { inserted: true };`),
};

// The plugin method ChangeComment leaves a spreadsheet comment as it was
cell.commentChange = guard(`${helpers}
  var sheets = Api.GetSheets(), found = null;
  for (var i = 0; i < sheets.length && !found; i++) {
    var list = sheets[i].GetComments() || [];
    for (var j = 0; j < list.length; j++) if (list[j].GetId() === s.id) found = list[j];
  }
  if (!found) {
    var book = Api.GetComments() || [];
    for (var k = 0; k < book.length; k++) if (book[k].GetId() === s.id) found = book[k];
  }
  if (!found) return { error: 'no comment ' + s.id };
  if (has(s, 'reply')) found.AddReply(s.reply, s.author);
  if (has(s, 'resolved')) found.SetSolved(s.resolved);
  return { changed: true };`);

const slidePick = `
  var pres = Api.GetPresentation(), count = pres.GetSlidesCount();
  var slide = pres.GetSlideByIndex((s.slide || count) - 1);
  if (!slide) return { error: 'no slide ' + s.slide };
`;

const slide = {
  format: guard(`${helpers}
    var pres = Api.GetPresentation(), n = 0;
    for (var i = 0; i < pres.GetSlidesCount(); i++) {
      if (s.slide && s.slide !== i + 1) continue;
      var r = pres.GetSlideByIndex(i).Search(s.search, s.matchCase) || [];
      for (var j = 0; j < r.length; j++) {
        if (!s.all && n > 0) break;
        textProps(r[j], s.text || {});
        var ps = r[j].GetAllParagraphs();
        for (var k = 0; k < ps.length; k++) paraProps(ps[k], s.para || {});
        n++;
      }
    }
    return n ? { formatted: n } : { formatted: 0, error: 'text not found' };`),
  table: guard(`${helpers}${slidePick}
    var rows = s.rows, cols = 0;
    for (var i = 0; i < rows.length; i++) cols = Math.max(cols, rows[i].length);
    var t = Api.CreateTable(cols, rows.length);
    t.SetPosition(s.x === undefined ? 600000 : s.x * 36000, s.y === undefined ? 1200000 : s.y * 36000);
    for (var r = 0; r < rows.length; r++) {
      var row = t.GetRow(r);
      for (var c = 0; c < rows[r].length; c++) {
        var p = row.GetCell(c).GetContent().GetElement(0);
        p.AddText(String(rows[r][c] === undefined || rows[r][c] === null ? '' : rows[r][c]));
        if (s.header && r === 0) p.SetBold(true);
      }
    }
    slide.AddObject(t);
    return { inserted: true, slide: slide.GetSlideIndex() + 1, rows: rows.length, columns: cols };`),
  list: guard(`${helpers}${slidePick}
    var shape = Api.CreateShape('rect', (s.width || 200) * 36000, (s.height || 100) * 36000, Api.CreateNoFill(), Api.CreateStroke(0, Api.CreateNoFill()));
    shape.SetPosition(s.x === undefined ? 600000 : s.x * 36000, s.y === undefined ? 1200000 : s.y * 36000);
    var c = shape.GetDocContent();
    c.RemoveAllElements();
    for (var i = 0; i < s.items.length; i++) {
      var p = Api.CreateParagraph();
      p.AddText(s.items[i]);
      p.SetBullet(s.numbered ? Api.CreateNumbering('ArabicPeriod', i + 1) : Api.CreateBullet('\\u2022'));
      c.Push(p);
    }
    slide.AddObject(shape);
    return { inserted: s.items.length, slide: slide.GetSlideIndex() + 1 };`),
  image: guard(`${helpers}${slidePick}
    var d = Api.CreateImage(s.src, s.width * 36000, s.height * 36000);
    d.SetPosition(s.x === undefined ? 600000 : s.x * 36000, s.y === undefined ? 1200000 : s.y * 36000);
    slide.AddObject(d);
    return { inserted: true, slide: slide.GetSlideIndex() + 1 };`),
  chart: guard(`${helpers}${slidePick}
    var d = Api.CreateChart(s.type, s.series, s.seriesNames, s.categories, s.width * 36000, s.height * 36000, s.styleIndex || 2);
    d.SetPosition(s.x === undefined ? 600000 : s.x * 36000, s.y === undefined ? 1200000 : s.y * 36000);
    slide.AddObject(d);
    return { inserted: true, slide: slide.GetSlideIndex() + 1 };`),
};

// A caller's script as a function: its data arrives as the variable data, a throw as error
function scriptSource(script) {
  return `function () {
    var data = Asc.scope.data;
    try {
      var run = function () {
${script}
      };
      var value = run();
      return value === undefined ? null : JSON.parse(JSON.stringify(value));
    } catch (e) { return { error: String(e && e.message || e) }; }
  }`;
}

// The whole comment, for the editor's own call: in a presentation the plugin method
// ChangeComment never answers and the Office API comment object cannot change anything
function slideCommentChange([id, data]) {
  const A = window.Asc;
  const entry = x => {
    const c = new A.asc_CCommentDataSlide();
    c.asc_putText(x.Text || '');
    c.asc_putTime(x.Time || '');
    c.asc_putUserName(x.UserName || '');
    if (x.UserId) c.asc_putUserId(x.UserId);
    if (x.QuoteText) c.asc_putQuoteText(x.QuoteText);
    c.asc_putSolved(!!x.Solved);
    return c;
  };
  const whole = entry(data);
  for (const r of data.Replies || []) whole.asc_addReply(entry(r));
  A.editor.asc_changeComment(id, whole);
  return true;
}

const byEditor = { word, cell, slide };

function richFor(type, name) {
  const fn = byEditor[type] && byEditor[type][name];
  if (!fn) {
    const label = { word: 'text documents', cell: 'spreadsheets', slide: 'presentations' }[type] || type;
    throw Object.assign(new Error(`this tool does not work in ${label}`), { status: 400 });
  }
  return fn;
}

// One comment as the plugin method GetAllComments returns it, in the shape of the tool
function commentView(c) {
  const d = c.Data || {};
  const entry = x => ({
    author: x.UserName || '', text: x.Text || '',
    time: x.Time ? new Date(Number(x.Time)).toISOString() : null,
  });
  return {
    id: c.Id, ...entry(d), quote: d.QuoteText || '', resolved: !!d.Solved,
    replies: (d.Replies || []).map(entry),
  };
}

module.exports = { word, cell, slide, richFor, scriptSource, commentView, slideCommentChange };
