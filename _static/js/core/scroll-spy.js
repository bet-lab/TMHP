/**
 * ⑨b Scroll spy: highlights the right-side TOC entry corresponding to
 * the section currently most in view.
 *
 * Sphinx 7+ emits `<section id="...">` wrappers around each heading
 * (rather than putting the id on the h2/h3 itself), so the observer
 * targets `section[id]` inside the article. Each section's id is
 * matched against the TOC link's `#hash`. Only top-level sections
 * that the right-TOC actually links to are observed — deeper nested
 * sections without a TOC entry are skipped.
 */
(function () {
  "use strict";
  const article = document.querySelector("article.yue, .yue");
  if (!article) return;
  const toc = document.querySelector(".sy-rside, nav.toc, .toc-list");
  if (!toc) return;

  const linkByHash = new Map();
  toc.querySelectorAll('a[href*="#"]').forEach(a => {
    const hash = a.getAttribute("href").split("#")[1];
    if (hash) linkByHash.set(hash, a);
  });
  if (!linkByHash.size) return;

  const targets = [];
  linkByHash.forEach((_link, hash) => {
    const el = article.querySelector(`#${CSS.escape(hash)}`);
    if (el) targets.push(el);
  });
  if (!targets.length) return;

  let last = null;
  const obs = new IntersectionObserver((entries) => {
    const hit = entries.filter(e => e.isIntersecting)
      .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
    if (!hit) return;
    const link = linkByHash.get(hit.target.id);
    if (link && link !== last) {
      if (last) last.classList.remove("is-active");
      link.classList.add("is-active");
      last = link;
    }
  }, { rootMargin: "-30% 0px -60% 0px", threshold: 0 });

  targets.forEach(t => obs.observe(t));
})();
