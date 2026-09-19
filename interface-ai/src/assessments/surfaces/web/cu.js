// Injected into every frame. Pure DOM, no dependencies. Exposes window.__cu.
// Responsibilities: accessibility-style snapshot with refs, locator-candidate description,
// custom strategy resolution (label / table cell / text), in-DOM redaction, human-action capture.
(() => {
  if (window.__cu) return;
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const MASK_ATTR = "data-cu-redacted";
  const REF_ATTR = "data-cu-ref";
  const PII_RES = [
    /\b\d{3}-\d{2}-\d{4}\b/, /\(\d{3}\)\s?\d{3}-\d{4}/, /\b[\w.+-]+@[\w-]+\.[\w.-]+\b/,
  ];

  function isVisible(el) {
    if (!(el instanceof Element)) return false;
    if (el.tagName === "INPUT" && el.type === "hidden") return false;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  const BUTTON_INPUTS = new Set(["submit", "button", "reset", "image"]);
  function role(el) {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit;
    const tag = el.tagName;
    if (tag === "A" && el.hasAttribute("href")) return "link";
    if (tag === "BUTTON") return "button";
    if (tag === "SELECT") return el.multiple || el.size > 1 ? "listbox" : "combobox";
    if (tag === "TEXTAREA") return "textbox";
    if (tag === "INPUT") {
      const t = (el.type || "text").toLowerCase();
      if (BUTTON_INPUTS.has(t)) return "button";
      if (t === "checkbox") return "checkbox";
      if (t === "radio") return "radio";
      if (t === "password") return "textbox";
      return "textbox";
    }
    if (/^H[1-6]$/.test(tag)) return "heading";
    if (tag === "TD" || tag === "TH") return "cell";
    return null;
  }

  function isInteractive(el) {
    const r = role(el);
    return ["link", "button", "textbox", "combobox", "listbox", "checkbox", "radio"].includes(r) ||
      el.hasAttribute("onclick");
  }

  function cellOf(el) { return el.closest("td,th"); }

  // Text of the label cell immediately before the element's cell in the same row
  // (legacy "label | control" table layouts have no <label for>).
  function adjacentCellLabel(el) {
    const cell = cellOf(el);
    if (!cell) return null;
    let prev = cell.previousElementSibling;
    while (prev && !norm(prev.innerText)) prev = prev.previousElementSibling;
    if (!prev) return null;
    const text = norm(prev.innerText);
    return text && text.length <= 40 && !/\d{3,}/.test(text) ? text : null;
  }

  function labelOf(el) {
    if (el.getAttribute("aria-label")) return norm(el.getAttribute("aria-label"));
    const by = el.getAttribute("aria-labelledby");
    if (by) {
      const t = by.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean)
        .map((n) => norm(n.innerText)).join(" ");
      if (t) return t;
    }
    if (el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab) return norm(lab.innerText);
    }
    const wrap = el.closest("label");
    if (wrap) return norm(wrap.innerText);
    return adjacentCellLabel(el);
  }

  function accName(el) {
    const r = role(el);
    if (el.tagName === "INPUT" && BUTTON_INPUTS.has((el.type || "").toLowerCase())) {
      return norm(el.value || el.alt || el.title || "Submit");
    }
    if (r === "textbox" || r === "combobox" || r === "listbox" || r === "checkbox" || r === "radio") {
      return labelOf(el) || norm(el.placeholder || el.title || "");
    }
    if (el.getAttribute("aria-label")) return norm(el.getAttribute("aria-label"));
    const t = norm(el.innerText);
    if (t) return t;
    const img = el.querySelector("img[alt]");
    return img ? norm(img.alt) : norm(el.title || "");
  }

  function ownText(el) {
    if (el.tagName === "INPUT") return norm(el.value);
    return norm(el.innerText);
  }

  // ------------------------------------------------------------------ redaction
  let redactLabels = [];
  function applyRedaction(labels, values) {
    redactLabels = (labels || []).map((l) => norm(l).toLowerCase());
    const secretValues = (values || []).filter((v) => v && v.length >= 3);
    for (const el of document.querySelectorAll(`[${MASK_ATTR}]`)) el.removeAttribute(MASK_ATTR);
    // Values next to sensitive labels.
    for (const cell of document.querySelectorAll("td,th,label,span,b")) {
      const t = norm(cell.innerText).toLowerCase();
      if (!redactLabels.includes(t)) continue;
      const host = cellOf(cell) || cell;
      const next = host.nextElementSibling;
      if (next) next.setAttribute(MASK_ATTR, "label");
    }
    for (const inp of document.querySelectorAll("input[type=password]")) inp.setAttribute(MASK_ATTR, "secret");
    // Pattern PII in leaf-ish elements.
    for (const el of document.querySelectorAll("td,span,p,div,font,b,li")) {
      if (el.children.length > 2) continue;
      const t = el.innerText || "";
      if (PII_RES.some((re) => re.test(t))) el.setAttribute(MASK_ATTR, "pattern");
    }
    // Known sensitive values (PII inputs, secrets) wherever they appear, including typed fields.
    if (secretValues.length) {
      for (const el of document.querySelectorAll("td,span,p,div,font,b,li,a,label")) {
        if (el.children.length > 2) continue;
        const t = el.innerText || "";
        if (secretValues.some((v) => t.includes(v))) el.setAttribute(MASK_ATTR, "value");
      }
      for (const inp of document.querySelectorAll("input,textarea")) {
        if (secretValues.some((v) => (inp.value || "").includes(v))) inp.setAttribute(MASK_ATTR, "value");
      }
    }
    let style = document.getElementById("__cu_redact_style");
    if (!style) {
      style = document.createElement("style");
      style.id = "__cu_redact_style";
      (document.head || document.documentElement).appendChild(style);
    }
    style.textContent = `html.__cu-masking [${MASK_ATTR}]{background:#222!important;color:#222!important;` +
      `-webkit-text-fill-color:#222!important}`;
  }
  function setMasking(on) { document.documentElement.classList.toggle("__cu-masking", !!on); }
  function masked(el) { return !!el.closest(`[${MASK_ATTR}]`); }

  // ------------------------------------------------------------------ snapshot
  // Produces compact lines: headings, text, table rows, and interactive elements with [ref].
  function snapshot(refStart, labels, values) {
    applyRedaction(labels, values);
    for (const el of document.querySelectorAll(`[${REF_ATTR}]`)) el.removeAttribute(REF_ATTR);
    let n = refStart;
    const lines = [];
    const elements = [];
    const seenRows = new Set();
    const body = document.body;
    if (!body) return { lines, elements, next: n, title: document.title };

    const walker = document.createTreeWalker(body, NodeFilter.SHOW_ELEMENT);
    let node = walker.currentNode;
    while (node) {
      const el = node;
      if (!isVisible(el) && el !== body) { node = skipSubtree(walker); continue; }
      if (el.tagName === "TR" && !seenRows.has(el)) {
        seenRows.add(el);
        const cells = [...el.cells].map((c) => (masked(c) ? "█████" : norm(c.innerText)));
        const hasControls = el.querySelector("input,select,textarea,button,a[href]");
        if (!hasControls && cells.some(Boolean)) lines.push("| " + cells.join(" | ") + " |");
      }
      if (isInteractive(el)) {
        const ref = `e${n++}`;
        el.setAttribute(REF_ATTR, ref);
        const r = role(el);
        const info = { ref, role: r, name: accName(el) };
        let line = `- ${r} "${info.name}" [${ref}]`;
        if (r === "textbox") {
          const v = el.type === "password" ? (el.value ? "••••" : "") : el.value;
          line += ` value="${masked(el) ? "█████" : v}"`;
        }
        if (r === "combobox") {
          const opts = [...el.options].map((o) => norm(o.text));
          line += ` selected="${norm(el.options[el.selectedIndex]?.text)}" options=${JSON.stringify(opts)}`;
        }
        if (el.disabled) line += " (disabled)";
        lines.push(line);
        elements.push(info);
      } else if (/^H[1-6]$/.test(el.tagName) || isEmphasis(el)) {
        const t = norm(el.innerText);
        if (t) lines.push(`# ${t}`);
      } else if (["P", "LI", "LABEL"].includes(el.tagName) || (el.tagName === "DIV" && el.children.length === 0)) {
        const t = masked(el) ? "█████" : norm(el.innerText);
        if (t && !el.closest("tr")) lines.push(t);
      }
      node = walker.nextNode();
    }
    return { lines: dedupe(lines), elements, next: n, title: document.title };
  }
  function isEmphasis(el) {
    if (el.tagName !== "B" && el.tagName !== "FONT") return false;
    if (el.closest("tr td:not(:only-child)")) return false;
    const size = parseFloat(getComputedStyle(el).fontSize);
    return size >= 15 || (el.tagName === "FONT" && el.getAttribute("size") >= 3);
  }
  function skipSubtree(walker) {
    let n = walker.currentNode;
    while (n) {
      if (walker.nextSibling()) return walker.currentNode;
      n = walker.parentNode();
    }
    return null;
  }
  function dedupe(lines) {
    const out = [];
    for (const l of lines) if (out[out.length - 1] !== l) out.push(l);
    return out;
  }

  // ------------------------------------------------------------------ locator candidates
  function cssPath(el) {
    const parts = [];
    while (el && el.nodeType === 1 && el !== document.body) {
      let i = 1, sib = el;
      while ((sib = sib.previousElementSibling)) if (sib.tagName === el.tagName) i++;
      parts.unshift(`${el.tagName.toLowerCase()}:nth-of-type(${i})`);
      el = el.parentElement;
    }
    return "body > " + parts.join(" > ");
  }

  function tableCellOf(el) {
    const cell = el.tagName === "TD" || el.tagName === "TH" ? el : cellOf(el);
    if (!cell) return null;
    const row = cell.parentElement;
    const table = cell.closest("table");
    if (!row || !table || table.rows.length < 2) return null;
    const header = table.rows[0];
    if (header === row) return null;
    const col = norm(header.cells[cell.cellIndex]?.innerText);
    if (!col) return null;
    // Row key: the cell with the most letters (a description like "Share Savings"), not data.
    let best = null, bestScore = 0;
    for (const c of row.cells) {
      if (c === cell) continue;
      const t = norm(c.innerText);
      const score = (t.match(/[A-Za-z]/g) || []).length;
      if (score > bestScore) { best = t; bestScore = score; }
    }
    return best ? { row_key: best, column: col } : null;
  }

  function describe(ref) {
    const el = document.querySelector(`[${REF_ATTR}="${ref}"]`);
    if (!el) return null;
    return describeEl(el);
  }
  function describeEl(el) {
    const interactive = isInteractive(el);
    const r = role(el);
    const out = { tag: el.tagName.toLowerCase(), role: r, interactive, css: cssPath(el) };
    if (interactive) out.name = accName(el);
    const label = labelOf(el) || (!interactive ? adjacentCellLabel(el) : null);
    if (label) out.label = label;
    if (el.name && ["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName)) out.field_name = el.name;
    const tc = !interactive ? tableCellOf(el) : null;
    if (tc) out.table_cell = tc;
    if (interactive && (r === "link" || r === "button")) out.text = ownText(el);
    if (el.tagName === "A" && el.getAttribute("href")) out.href = el.getAttribute("href");
    out.text_value = masked(el) ? null : ownText(el);
    return out;
  }

  // ------------------------------------------------------------------ custom strategy resolution
  function byLabel(label) {
    const want = norm(label);
    const hits = new Set();
    for (const el of document.querySelectorAll("input,select,textarea")) {
      if (isVisible(el) && labelOf(el) === want) hits.add(el);
    }
    // Value cells next to a label cell (read-only key/value tables).
    for (const cell of document.querySelectorAll("td,th")) {
      if (norm(cell.innerText) !== want) continue;
      let next = cell.nextElementSibling;
      if (!next) continue;
      const ctl = next.querySelector("input,select,textarea");
      if (ctl) hits.add(ctl); else if (isVisible(next)) hits.add(next);
    }
    return [...hits];
  }
  function byTableCell(rowKey, column) {
    const hits = [];
    for (const table of document.querySelectorAll("table")) {
      if (table.rows.length < 2) continue;
      const header = [...table.rows[0].cells].map((c) => norm(c.innerText));
      const idx = header.indexOf(norm(column));
      if (idx < 0) continue;
      for (const row of [...table.rows].slice(1)) {
        if ([...row.cells].some((c, i) => i !== idx && norm(c.innerText) === norm(rowKey))) {
          if (row.cells[idx]) hits.push(row.cells[idx]);
        }
      }
    }
    return hits;
  }
  function byText(text, element) {
    const want = norm(text);
    const sel = element === "link" ? "a[href]" :
      element === "button" ? "button,input[type=submit],input[type=button],input[type=reset],[role=button]" :
      "a[href],button,input[type=submit],input[type=button],td,th,span,b,font,p,label,li,div";
    const hits = [...document.querySelectorAll(sel)].filter((el) => isVisible(el) && ownText(el) === want);
    // Keep innermost matches only.
    return hits.filter((el) => !hits.some((o) => o !== el && el.contains(o)));
  }

  function mark(els, token) {
    for (const el of document.querySelectorAll("[data-cu-match]")) el.removeAttribute("data-cu-match");
    els.forEach((el) => el.setAttribute("data-cu-match", token));
    return els.length;
  }
  function resolve(strategy, token) {
    let els = [];
    if (strategy.kind === "label") els = byLabel(strategy.label);
    else if (strategy.kind === "table_cell") els = byTableCell(strategy.row_key, strategy.column);
    else if (strategy.kind === "text") els = byText(strategy.text, strategy.element);
    else if (strategy.kind === "href") {
      els = [...document.querySelectorAll("a[href]")].filter((a) => a.getAttribute("href") === strategy.href);
    }
    return mark(els.filter(isVisible), token);
  }

  function pageText() {
    const t = document.body ? document.body.innerText : "";
    return t || "";
  }

  // ------------------------------------------------------------------ human action capture
  // Reports every user event; the Python side keeps it only while a person holds control.
  // Values are never sent, only lengths.
  function report(kind, el, extra) {
    if (typeof window.__cuHumanEvent !== "function") return;
    try { window.__cuHumanEvent({ kind, element: describeEl(el), ...extra }); } catch (e) { /* ignore */ }
  }
  document.addEventListener("click", (e) => {
    const el = e.target.closest("a,button,input,select,td,th,[onclick]") || e.target;
    report("click", el, {});
  }, true);
  document.addEventListener("change", (e) => {
    const el = e.target;
    if (el.tagName === "SELECT") report("select", el, { option: norm(el.options[el.selectedIndex]?.text) });
    else if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") report("fill", el, { length: (el.value || "").length });
  }, true);

  window.__cu = { snapshot, describe, describeElement: describeEl, resolve, setMasking, applyRedaction, pageText, role, accName };
})();
