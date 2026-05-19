/**
 * ⑧ Command palette (Cmd+K / Ctrl+K).
 *
 * Reads page titles + URLs from Sphinx's _static/searchindex.js. Opens
 * a modal with a fuzzy filter; ↑ ↓ navigate, Enter opens, Esc closes.
 */
(function () {
  "use strict";

  const STATE = { items: null, modal: null, input: null, list: null, focusIdx: 0 };

  function staticDir() {
    const parts = window.location.pathname.replace(/\/$/, "").split("/").filter(Boolean);
    return parts.length ? "../".repeat(parts.length - 1) + "_static" : "_static";
  }
  function docRootUrl() {
    const parts = window.location.pathname.replace(/\/$/, "").split("/").filter(Boolean);
    return parts.length ? "../".repeat(parts.length - 1) : "./";
  }

  function loadIndex() {
    if (STATE.items) return Promise.resolve(STATE.items);
    return new Promise((resolve, reject) => {
      const prev = window.Search;
      window.Search = {
        setIndex(idx) {
          const docs = idx.docnames || [];
          const titles = idx.titles || [];
          STATE.items = docs.map((d, i) => ({
            url: docRootUrl() + d + ".html",
            title: titles[i] || d,
            doc: d,
          }));
          window.Search = prev;
          resolve(STATE.items);
        },
      };
      const s = document.createElement("script");
      s.src = `${staticDir()}/searchindex.js`;
      s.onerror = () => reject(new Error("searchindex.js failed to load"));
      document.head.appendChild(s);
    });
  }

  function ensureModal() {
    if (STATE.modal) return STATE.modal;
    const wrap = document.createElement("div");
    wrap.className = "cmdk-overlay";
    wrap.innerHTML = `
      <div class="cmdk-modal" role="dialog" aria-label="Command palette">
        <input class="cmdk-input" type="text" placeholder="Search pages…" autocomplete="off">
        <ul class="cmdk-list" role="listbox"></ul>
      </div>
    `;
    document.body.appendChild(wrap);
    STATE.modal = wrap;
    STATE.input = wrap.querySelector(".cmdk-input");
    STATE.list = wrap.querySelector(".cmdk-list");

    wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });
    STATE.input.addEventListener("input", render);
    STATE.input.addEventListener("keydown", onKey);
    return wrap;
  }

  function open() {
    ensureModal();
    STATE.modal.classList.add("open");
    STATE.input.value = "";
    STATE.focusIdx = 0;
    loadIndex().then(render).catch(console.error);
    setTimeout(() => STATE.input.focus(), 10);
  }

  function close() {
    if (STATE.modal) STATE.modal.classList.remove("open");
  }

  function render() {
    if (!STATE.items) return;
    const q = STATE.input.value.trim().toLowerCase();
    const items = !q ? STATE.items.slice(0, 12)
      : STATE.items.filter(it =>
          it.title.toLowerCase().includes(q) || it.doc.toLowerCase().includes(q)
        ).slice(0, 12);
    if (!items.length) {
      STATE.list.innerHTML = `<li class="cmdk-empty">No pages match “${q}”.</li>`;
      return;
    }
    STATE.list.innerHTML = items.map((it, i) =>
      `<li role="option" class="cmdk-item${i === STATE.focusIdx ? " active" : ""}"
           data-url="${it.url}">${it.title}<span class="cmdk-doc">${it.doc}</span></li>`
    ).join("");
    STATE.list.querySelectorAll("li.cmdk-item").forEach((li, i) => {
      li.addEventListener("mouseenter", () => { STATE.focusIdx = i; updateFocus(); });
      li.addEventListener("click", () => { window.location = li.dataset.url; });
    });
  }

  function updateFocus() {
    const items = STATE.list.querySelectorAll("li.cmdk-item");
    items.forEach((li, i) => li.classList.toggle("active", i === STATE.focusIdx));
    const cur = STATE.list.querySelector("li.cmdk-item.active");
    if (cur) cur.scrollIntoView({ block: "nearest" });
  }

  function onKey(e) {
    const items = STATE.list.querySelectorAll("li.cmdk-item");
    const n = items.length;
    if (e.key === "Escape") { close(); }
    else if (e.key === "Enter") {
      const cur = STATE.list.querySelector("li.cmdk-item.active");
      if (cur) window.location = cur.dataset.url;
    }
    else if (e.key === "ArrowDown" && n > 0) {
      e.preventDefault();
      STATE.focusIdx = (STATE.focusIdx + 1) % n; updateFocus();
    }
    else if (e.key === "ArrowUp" && n > 0) {
      e.preventDefault();
      STATE.focusIdx = (STATE.focusIdx - 1 + n) % n; updateFocus();
    }
  }

  document.addEventListener("keydown", (e) => {
    const isMac = navigator.platform.toLowerCase().includes("mac");
    const trigger = (isMac && e.metaKey) || (!isMac && e.ctrlKey);
    if (trigger && (e.key === "k" || e.key === "K")) { e.preventDefault(); open(); }
  });

  window.tmhpCmdK = { open, close };
})();
