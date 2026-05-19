/**
 * docs interactive layer — global entry.
 *
 * Imports of individual modules are appended by each pattern's commit
 * (⑥ glossary, ⑧ cmdk, ⑨ progress / scroll-spy / anchor copy). Reverting
 * one of those commits removes both its module file and its import line
 * here, leaving this file cleanly smaller.
 *
 * This script is loaded with `defer`, so the DOM is parsed before it runs.
 */
(function () {
  "use strict";

  // ⑥ glossary
  if (window.tmhpGlossary && typeof window.tmhpGlossary.attach === "function") {
    document.addEventListener("DOMContentLoaded", window.tmhpGlossary.attach);
  }

  // ⑧ cmdk — module self-wires its keydown listener; nothing to do here.

  // ⑨ reading-progress + scroll-spy + anchor-copy — all three modules
  // self-wire on load; nothing to do here.
})();
