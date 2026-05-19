/**
 * ⑨b Scroll spy: highlights the right-side TOC entry corresponding to
 * the section heading currently most in view. Uses IntersectionObserver
 * on h2 / h3 inside the article.
 */
(function () {
  "use strict";
  const article = document.querySelector("article.yue, .yue");
  if (!article) return;
  const toc = document.querySelector(".sy-rside, nav.toc, .toc-list");
  if (!toc) return;

  const headings = article.querySelectorAll("h2[id], h3[id]");
  if (!headings.length) return;

  const linkByHash = new Map();
  toc.querySelectorAll('a[href*="#"]').forEach(a => {
    const hash = a.getAttribute("href").split("#")[1];
    if (hash) linkByHash.set(hash, a);
  });
  if (!linkByHash.size) return;

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

  headings.forEach(h => obs.observe(h));
})();
