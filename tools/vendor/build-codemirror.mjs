// Builds src/researchos/webapp/vendor/codemirror.js from codemirror-entry.js
// (DESIGN.md 22.0.1, task V17 B1a). Built once, by hand, never at install or
// run time; nothing here fetches from a network.
//
//   node tools/vendor/build-codemirror.mjs <node_modules folder>
//
// <node_modules folder> must already hold the packages of codemirror.lock.json
// at exactly those versions, and esbuild at the lock's version. The script:
//   1. bundles the entry with esbuild and the lock's options;
//   2. refuses (writes nothing) unless the packages esbuild read are exactly
//      the lock's, at the lock's versions;
//   3. runs the bundle in an empty node context and refuses unless it sets
//      window-level `ROCodeMirror` with exactly the lock's exports;
//   4. writes the bundle, each package's licence text (LICENSE.<package>) and
//      vendor/README with the sha256 of every file it wrote.
// esbuild's output is deterministic for the same inputs and options, so a
// rebuild from the same packages gives the same sha256.

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, realpathSync, existsSync, mkdirSync, readdirSync, unlinkSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const lock = JSON.parse(readFileSync(path.join(here, "codemirror.lock.json"), "utf8"));
const vendorDir = path.join(repo, "src", "researchos", "webapp", "vendor");

function fail(message) {
  console.error("build-codemirror: " + message + " -- nothing was written.");
  process.exit(1);
}

const nodeModules = process.argv[2];
if (!nodeModules || !existsSync(nodeModules)) fail("give the node_modules folder that holds the locked packages");
const esbuild = createRequire(path.join(path.resolve(nodeModules), "_.js"))("esbuild");
if (esbuild.version !== lock.bundler.version) fail(`esbuild is ${esbuild.version}, the lock says ${lock.bundler.version}`);

const banner = "/* CodeMirror 6 for Research OS (DESIGN.md 22.0.1), built by tools/vendor/build-codemirror.mjs " +
  "from tools/vendor/codemirror.lock.json. MIT licences: LICENSE.* beside this file; see README. */";
const result = await esbuild.build({
  ...lock.bundler.options,
  entryPoints: [path.join(here, "codemirror-entry.js")],
  nodePaths: [path.resolve(nodeModules)],
  banner: { js: banner },
  metafile: true,
  write: false,
  outfile: "codemirror.js",
  logLevel: "silent",
});

// 2. Which packages went in: the nearest package.json above each input.
function packageOf(file) {
  let dir = path.dirname(realpathSync(file));
  while (dir !== path.dirname(dir)) {
    const pj = path.join(dir, "package.json");
    if (existsSync(pj)) {
      const j = JSON.parse(readFileSync(pj, "utf8"));
      if (j.name && j.version) return { name: j.name, version: j.version, license: j.license, dir };
    }
    dir = path.dirname(dir);
  }
  return null;
}
const entry = realpathSync(path.join(here, "codemirror-entry.js"));
const used = new Map();
for (const input of Object.keys(result.metafile.inputs)) {
  const abs = path.resolve(input);
  if (realpathSync(abs) === entry) continue;
  const pkg = packageOf(abs);
  if (!pkg) fail(`no package.json above ${input}`);
  used.set(pkg.name, pkg);
}
const want = lock.packages.map((p) => `${p.name}@${p.version} ${p.license}`).sort();
const got = [...used.values()].map((p) => `${p.name}@${p.version} ${p.license}`).sort();
if (JSON.stringify(want) !== JSON.stringify(got)) {
  fail("the bundled packages are not the lock's:\n  bundled: " + got.join(", ") + "\n  locked:  " + want.join(", "));
}

// 3. What it exposes.
const code = result.outputFiles[0].contents;
const text = Buffer.from(code).toString("utf8");
const context = vm.createContext({});
vm.runInContext(text, context);
const exported = Object.keys(context[lock.global] || {}).sort();
if (JSON.stringify(exported) !== JSON.stringify([...lock.exports].sort())) {
  fail("the bundle's exports are not the lock's:\n  bundle: " + exported.join(", ") + "\n  lock:   " + lock.exports.join(", "));
}

// 4. Write.
const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");
mkdirSync(vendorDir, { recursive: true });
for (const old of readdirSync(vendorDir)) if (old.startsWith("LICENSE.") && old !== "LICENSE.katex") unlinkSync(path.join(vendorDir, old));
const written = [["codemirror.js", Buffer.from(code)]];
for (const p of [...used.values()].sort((a, b) => a.name.localeCompare(b.name))) {
  const licence = readdirSync(p.dir).find((n) => /^licen[cs]e(\.md|\.txt)?$/i.test(n));
  if (!licence) fail(`${p.name} has no licence file`);
  written.push([`LICENSE.${p.name.replace(/^@/, "").replace("/", "-")}`, readFileSync(path.join(p.dir, licence))]);
}
for (const [name, bytes] of written) writeFileSync(path.join(vendorDir, name), bytes);

const rows = written.map(([name, bytes]) => `${sha(bytes)}  ${name}`).join("\n");
const packages = lock.packages.map((p) => `- ${p.name} ${p.version} (${p.license})`).join("\n");
// KaTeX's block is build-katex.mjs's: kept as it stands, from its heading up to the sha256 section.
const oldReadme = existsSync(path.join(vendorDir, "README")) ? readFileSync(path.join(vendorDir, "README"), "utf8") : "";
const katexAt = oldReadme.indexOf("katex.js, katex.css, fonts/\n");
const katexBlock = katexAt >= 0 ? oldReadme.slice(katexAt, oldReadme.indexOf("sha256\n------\n", katexAt)) :
  "KaTeX (22.0.1, batch B3) is not vendored yet; its katex.js, katex.css and\n" +
  "fonts/*.woff2 will be added here by a script of its own, with its rows below.\n\n";
writeFileSync(path.join(vendorDir, "README"), `Third-party code vendored into Research OS (DESIGN.md 22.0.1)
====================================================================

Written by tools/vendor/build-codemirror.mjs; do not edit by hand.

These files are code Research OS did not write. They are served same-origin by
GET /vendor/<name> from an allow-list (webapp/server.py, _VENDOR_FILES), never
fetched from a network, and loaded only by the LaTeX editor through
ROLatex.loadEditorLibs() and ROLatex.loadMath() (webapp/latex.js). The tests
that scan the page's scripts and styles skip this folder by name.

codemirror.js
-------------
CodeMirror 6, bundled by esbuild ${lock.bundler.version} (${lock.bundler.license}) as an IIFE that sets
window.${lock.global}; options: ${JSON.stringify(lock.bundler.options)}.
Exports: ${lock.exports.join(", ")}.

Packages (all bundled; each one's licence text is LICENSE.<package> here):
${packages}

${katexBlock}sha256
------
${rows}

Rebuilding
----------
1. Have a node_modules folder holding exactly the packages and versions of
   tools/vendor/codemirror.lock.json, and esbuild ${lock.bundler.version}.
2. From the repository root:
     node tools/vendor/build-codemirror.mjs <that node_modules folder>
   It refuses, writing nothing, when the packages it bundles or the names the
   bundle exposes differ from the lock.
3. Commit codemirror.js, the LICENSE.* files and this README together.
   To change a version or an export, change the lock (and the entry) first.
`);
for (const [name, bytes] of written) console.log(`${sha(bytes)}  ${name}`);
