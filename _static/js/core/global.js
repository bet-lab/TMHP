/**
 * docs interactive layer — global entry.
 *
 * Wires modules that need an explicit registration call. Modules that
 * self-wire (cmdk's document keydown, reading-progress / scroll-spy on
 * load) don't show up here.
 *
 * Loaded with `defer`, so the DOM is parsed before this runs.
 */
(function () {
  "use strict";

  // ⑥ glossary
  if (window.tmhpGlossary && typeof window.tmhpGlossary.attach === "function") {
    document.addEventListener("DOMContentLoaded", window.tmhpGlossary.attach);
  }

  // ⑧ cmdk — self-wires its keydown listener.
  // ⑨ reading-progress + scroll-spy — both self-wire on load.
})();
