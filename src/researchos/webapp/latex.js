// The LaTeX editor -- DESIGN.md Section 22 (22.0.1, 22.1); task V17, batch B1.
// Served verbatim at /latex.js and loaded by app.html with a plain same-origin
// <script src>, exactly as /desk.js and /node.js are. It defines one global,
// window.ROLatex.
//
// So far (B1a) it holds only the loader of the vendored libraries: the editor
// itself comes in B1c. The vendored code (tools/vendor/, served by
// GET /vendor/<name> from an allow-list) is never in a page's markup: the
// first call of loadEditorLibs() adds its <script> tag once, and every later
// call gets the same promise, so no page loads it until the editor opens
// (22.8 item 7). KaTeX (B3) will be added to VENDOR_SCRIPTS when it is
// vendored.
(function () {
  "use strict";

  // The vendored scripts the editor needs, in load order, and the global each one sets.
  const VENDOR_SCRIPTS = [{ src: "/vendor/codemirror.js", global: "ROCodeMirror" }];

  let loading = null;

  function addScript(entry) {
    return new Promise(function (resolve, reject) {
      if (window[entry.global]) { resolve(window[entry.global]); return; }
      const tag = document.createElement("script");
      tag.src = entry.src;
      tag.async = false;
      tag.onload = function () {
        if (window[entry.global]) resolve(window[entry.global]);
        else reject(new Error(entry.src + " did not define " + entry.global));
      };
      tag.onerror = function () { tag.remove(); reject(new Error(entry.src + " could not be loaded")); };
      document.head.appendChild(tag);
    });
  }

  // Resolves to {CodeMirror} once every vendored script has run. A failure is not
  // kept: the next call (opening the editor again) tries afresh.
  function loadEditorLibs() {
    if (!loading) {
      loading = VENDOR_SCRIPTS.reduce(function (chain, entry) {
        return chain.then(function (libs) {
          return addScript(entry).then(function (lib) { libs[entry.global] = lib; return libs; });
        });
      }, Promise.resolve({})).then(function (libs) {
        return { CodeMirror: libs.ROCodeMirror };
      }, function (err) {
        loading = null;
        throw err;
      });
    }
    return loading;
  }

  window.ROLatex = Object.freeze({ loadEditorLibs: loadEditorLibs, _vendorScripts: VENDOR_SCRIPTS });
})();
