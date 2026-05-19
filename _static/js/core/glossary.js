/**
 * ⑥ Inline glossary popovers.
 *
 * Wraps an existing `<span class="glossary" data-term="xxx">` in any
 * page with hover/focus → small popover showing the term name, a 1-2
 * line definition, and a link to the concept page. Terms come from
 * /_static/data/glossary.json.
 *
 * Hover-bridge: a short hide timer lets the user travel from the
 * underlined term to the popover (to click the "Concepts page" link)
 * without the popover vanishing mid-motion. Either end of the bridge —
 * the span or the popover — cancels a pending hide on mouseenter.
 */
(function () {
  "use strict";

  const HIDE_DELAY_MS = 220;

  const STATE = { terms: null, active: null, pop: null, hideTimer: null };

  function staticDir() {
    const parts = window.location.pathname.replace(/\/$/, "").split("/").filter(Boolean);
    return parts.length ? "../".repeat(parts.length - 1) + "_static" : "_static";
  }

  async function loadTerms() {
    if (STATE.terms) return STATE.terms;
    const r = await fetch(`${staticDir()}/data/glossary.json`, { credentials: "same-origin" });
    STATE.terms = await r.json();
    return STATE.terms;
  }

  function cancelHide() {
    if (STATE.hideTimer) {
      clearTimeout(STATE.hideTimer);
      STATE.hideTimer = null;
    }
  }

  function scheduleHide() {
    cancelHide();
    STATE.hideTimer = setTimeout(() => {
      STATE.hideTimer = null;
      doHide();
    }, HIDE_DELAY_MS);
  }

  function ensurePopover() {
    if (STATE.pop) return STATE.pop;
    const el = document.createElement("div");
    el.className = "glossary-pop";
    el.setAttribute("role", "tooltip");
    // Either end of the hover bridge cancels a pending hide; leaving the
    // popover reschedules one.
    el.addEventListener("mouseenter", cancelHide);
    el.addEventListener("mouseleave", scheduleHide);
    document.body.appendChild(el);
    STATE.pop = el;
    return el;
  }

  function show(span, entry, baseUrl) {
    cancelHide();
    const pop = ensurePopover();
    pop.innerHTML = `
      <div class="head">${entry.name}</div>
      <div class="def">${entry.def}</div>
      <a class="link" href="${baseUrl}/${entry.link}">↳ Concepts page</a>
    `;
    const r = span.getBoundingClientRect();
    // 2px gap (down from 4) — closes the dead zone the cursor used to
    // cross when reaching for the link.
    pop.style.top = `${window.scrollY + r.bottom + 2}px`;
    pop.style.left = `${window.scrollX + r.left}px`;
    pop.classList.add("visible");
    STATE.active = span;
  }

  function doHide() {
    if (STATE.pop) STATE.pop.classList.remove("visible");
    STATE.active = null;
  }

  async function attach() {
    const spans = document.querySelectorAll("span.glossary[data-term]");
    if (!spans.length) return;
    const terms = await loadTerms();
    const baseUrl = staticDir().replace(/\/_static$/, "");

    spans.forEach(span => {
      const term = terms[span.dataset.term];
      if (!term) return;
      span.tabIndex = 0;
      span.setAttribute("aria-label", term.name);

      span.addEventListener("mouseenter", () => show(span, term, baseUrl));
      span.addEventListener("mouseleave", scheduleHide);
      span.addEventListener("focus", () => show(span, term, baseUrl));
      span.addEventListener("blur", scheduleHide);
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") { cancelHide(); doHide(); } });
    document.addEventListener("click", (e) => {
      if (STATE.active && !STATE.active.contains(e.target) &&
          STATE.pop && !STATE.pop.contains(e.target)) {
        cancelHide();
        doHide();
      }
    });
  }

  window.tmhpGlossary = { attach };
})();
