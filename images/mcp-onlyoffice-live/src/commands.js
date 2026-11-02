'use strict';

// Document Builder snippets that run inside the editor through the agent-bridge plugin
// (Asc.plugin.callCommand). Each is a function expression; its data arrives as Asc.scope.

const word = {
  read: `function () {
    var d = Api.GetDocument(), out = [], n = d.GetElementsCount(), tables = 0;
    for (var i = 0; i < n; i++) {
      var e = d.GetElement(i), t = e.GetClassType ? e.GetClassType() : '';
      var text = '';
      try { text = e.GetText ? e.GetText() : (e.GetRange ? e.GetRange().GetText() : ''); } catch (x) { text = ''; }
      var item = { index: i, type: t, text: text };
      if (t === 'table') {
        item.table = tables++; item.rows = [];
        for (var r = 0; r < e.GetRowsCount(); r++) {
          var row = e.GetRow(r), cells = [];
          for (var c = 0; c < row.GetCellsCount(); c++) cells.push(row.GetCell(c).GetContent().GetElement(0).GetText().replace(/\\s+$/, ''));
          item.rows.push(cells);
        }
        delete item.text;
      }
      out.push(item);
    }
    return { elements: out, trackChanges: d.IsTrackRevisions ? d.IsTrackRevisions() : null };
  }`,
  insert: `function () {
    var d = Api.GetDocument(), s = Asc.scope, lines = s.text.split('\\n'), paras = [];
    for (var i = 0; i < lines.length; i++) { var p = Api.CreateParagraph(); p.AddText(lines[i]); paras.push(p); }
    if (s.where === 'end') { for (var j = 0; j < paras.length; j++) d.Push(paras[j]); return { inserted: paras.length }; }
    if (s.where === 'start') { for (var k = paras.length - 1; k >= 0; k--) d.AddElement(0, paras[k]); return { inserted: paras.length }; }
    if (s.where === 'cursor') { d.InsertContent(paras, lines.length === 1); return { inserted: paras.length }; }
    var found = d.Search(s.anchor, true);
    if (!found || found.length === 0) return { inserted: 0, error: 'anchor text not found' };
    var anchor = found[0].GetParagraph(0);
    for (var m = paras.length - 1; m >= 0; m--) anchor.InsertParagraph(paras[m], s.where === 'before' ? 'before' : 'after', true);
    return { inserted: paras.length };
  }`,
  // an empty paragraph at the end, which the typing chunks then fill
  typingStart: `function () { var d = Api.GetDocument(); d.Push(Api.CreateParagraph()); return true; }`,
  typingChunk: `function () {
    var d = Api.GetDocument(), p = d.GetElement(d.GetElementsCount() - 1);
    if (Asc.scope.newParagraph) { p = Api.CreateParagraph(); d.Push(p); }
    p.AddText(Asc.scope.text); return true;
  }`,
  selectFirst: `function () {
    var r = Api.GetDocument().Search(Asc.scope.search, Asc.scope.matchCase);
    if (!r || r.length === 0) return 0; r[0].Select(); return r.length;
  }`,
  count: `function () { var r = Api.GetDocument().Search(Asc.scope.search, Asc.scope.matchCase); return r ? r.length : 0; }`,
  trackChanges: `function () {
    var d = Api.GetDocument(); d.SetTrackRevisions(Asc.scope.enabled); return { trackChanges: d.IsTrackRevisions() };
  }`,
};

const cell = {
  read: `function () {
    var ws = Api.GetActiveSheet(), r = ws.GetUsedRange();
    return { sheet: ws.GetName(), range: r.GetAddress(false, false), values: r.GetValue() };
  }`,
  insert: `function () {
    var ws = Api.GetActiveSheet(), s = Asc.scope, addr = s.anchor;
    if (!addr || s.where === 'end') {
      var used = ws.GetUsedRange(), rows = used.GetRowCount ? used.GetRowCount() : 0;
      var first = used.GetRow ? used.GetRow() : 1;
      addr = 'A' + (first + rows);
    }
    var lines = s.text.split('\\n'), start = ws.GetRange(addr);
    for (var i = 0; i < lines.length; i++) start.GetRows(i + 1).GetCells(1, 1).SetValue(lines[i]);
    return { inserted: lines.length, cell: addr };
  }`,
  replace: `function () {
    var ws = Api.GetActiveSheet(), used = ws.GetUsedRange(), s = Asc.scope, n = 0;
    used.ForEach(function (c) { var v = c.GetValue(); if (typeof v === 'string' && v.indexOf(s.search) !== -1) n++; });
    used.Replace({ What: s.search, Replacement: s.replacement, LookAt: 'xlPart', SearchOrder: 'xlByRows',
      SearchDirection: 'xlNext', MatchCase: s.matchCase, ReplaceAll: s.all });
    return { replaced: s.all ? n : Math.min(n, 1) };
  }`,
  comment: `function () {
    var ws = Api.GetActiveSheet(), r = ws.GetRange(Asc.scope.search);
    r.AddComment(Asc.scope.comment, Asc.scope.author); return { added: true };
  }`,
};

const slide = {
  read: `function () {
    var p = Api.GetPresentation(), out = [];
    for (var i = 0; i < p.GetSlidesCount(); i++) {
      var sl = p.GetSlideByIndex(i), shapes = sl.GetAllShapes(), texts = [];
      for (var j = 0; j < shapes.length; j++) {
        var c = shapes[j].GetDocContent && shapes[j].GetDocContent();
        if (!c) continue;
        for (var k = 0; k < c.GetElementsCount(); k++) { var e = c.GetElement(k); if (e.GetText) texts.push(e.GetText()); }
      }
      out.push({ slide: i + 1, texts: texts });
    }
    return { slides: out };
  }`,
  insert: `function () {
    var p = Api.GetPresentation(), s = Asc.scope;
    var sl = Api.CreateSlide(); p.AddSlide(sl);
    var shape = Api.CreateShape('rect', 8000000, 4000000, Api.CreateNoFill(), Api.CreateStroke(0, Api.CreateNoFill()));
    shape.SetPosition(600000, 1200000);
    var c = shape.GetDocContent(); c.RemoveAllElements();
    var lines = s.text.split('\\n');
    for (var i = 0; i < lines.length; i++) { var para = Api.CreateParagraph(); para.AddText(lines[i]); c.Push(para); }
    sl.AddObject(shape);
    return { inserted: lines.length, slide: p.GetSlidesCount() };
  }`,
};

// a slide paragraph that contains the text gets the new text as one run
slide.replace = `function () {
  var p = Api.GetPresentation(), s = Asc.scope, n = 0;
  for (var i = 0; i < p.GetSlidesCount(); i++) {
    var shapes = p.GetSlideByIndex(i).GetAllShapes();
    for (var j = 0; j < shapes.length; j++) {
      var c = shapes[j].GetDocContent && shapes[j].GetDocContent();
      if (!c) continue;
      for (var k = 0; k < c.GetElementsCount(); k++) {
        var e = c.GetElement(k); if (!e.GetText) continue;
        var t = e.GetText().replace(/[\\r\\n]+$/, '');
        if (t.indexOf(s.search) === -1 || (!s.all && n > 0)) continue;
        var nt = s.all ? t.split(s.search).join(s.replacement) : t.replace(s.search, s.replacement);
        n += s.all ? t.split(s.search).length - 1 : 1;
        e.RemoveAllElements(); e.AddText(nt);
      }
    }
  }
  return { replaced: n };
}`;

const byEditor = { word, cell, slide };

function forEditor(type) {
  const set = byEditor[type];
  if (!set) throw new Error(`unsupported editor type ${type}`);
  return set;
}

// Chunks of text for typing: words with their following space, newlines as their own item
function typingChunks(text, maxChunk = 24) {
  const out = [];
  for (const piece of text.split(/(\n)/)) {
    if (piece === '\n') { out.push('\n'); continue; }
    const words = piece.match(/\S+\s*|\s+/g) || [];
    for (const w of words) {
      for (let i = 0; i < w.length; i += maxChunk) out.push(w.slice(i, i + maxChunk));
    }
  }
  return out;
}

module.exports = { forEditor, typingChunks, word, cell, slide };
