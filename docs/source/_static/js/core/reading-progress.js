/**
 * ⑨a Reading progress: fills a 3-px bar fixed to the top of the viewport
 * based on the visible portion of the main article (`.yue`), not the
 * whole page (sidebar / TOC fixed regions don't affect reading state).
 */
(function () {
  "use strict";
  const article = document.querySelector("article.yue, .yue");
  if (!article) return;

  const bar = document.createElement("div");
  bar.className = "reading-progress";
  document.body.appendChild(bar);

  function update() {
    const top = article.getBoundingClientRect().top + window.scrollY;
    const height = article.offsetHeight;
    const visible = Math.min(
      Math.max(window.scrollY - top + window.innerHeight, 0),
      height
    );
    const pct = height > 0 ? (visible / height) * 100 : 0;
    bar.style.width = `${pct}%`;
  }
  window.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update);
  update();
})();
