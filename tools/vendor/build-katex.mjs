// Copies KaTeX into src/researchos/webapp/vendor/ from its npm tarball
// (DESIGN.md 22.0.1, task V17 B3a). Run once, by hand, never at install or run
// time; nothing here fetches from a network, and nothing in the tarball is run
// but katex.min.js itself, inside an empty node context, to check it.
//
//   node tools/vendor/build-katex.mjs <katex-<version>.tgz>
//
// The script:
//   1. refuses (writes nothing) unless the tarball's sha256 and npm integrity
//      are katex.lock.json's, and its package.json names the lock's package,
//      version and licence;
//   2. reads katex.min.js, katex.min.css, LICENSE and the lock's woff2 fonts
//      out of the tarball (a tar reader of its own; nothing is extracted);
//   3. cuts each @font-face's src in the stylesheet down to its woff2 file and
//      refuses unless the fonts it names are exactly the lock's and no other
//      url() is left;
//   4. runs katex.min.js in an empty node context and refuses unless it sets
//      `katex` at the lock's version and typesets \frac{a}{b};
//   5. writes katex.js (katex.min.js byte for byte), katex.css, LICENSE.katex
//      and fonts/<name>.woff2 (byte for byte), and its block of vendor/README
//      with the sha256 of every file it wrote. Same tarball, same bytes.

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync, unlinkSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import zlib from "node:zlib";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const lock = JSON.parse(readFileSync(path.join(here, "katex.lock.json"), "utf8"));
const vendorDir = path.join(repo, "src", "researchos", "webapp", "vendor");
const fontsDir = path.join(vendorDir, "fonts");

function fail(message) {
  console.error("build-katex: " + message + " -- nothing was written.");
  process.exit(1);
}
const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");

// 1. The tarball is the lock's.
const tarball = process.argv[2];
if (!tarball || !existsSync(tarball)) fail("give the path of " + lock.tarball.name);
const tgz = readFileSync(tarball);
if (sha(tgz) !== lock.tarball.sha256) fail(`the tarball's sha256 is ${sha(tgz)}, the lock says ${lock.tarball.sha256}`);
const integrity = "sha512-" + createHash("sha512").update(tgz).digest("base64");
if (integrity !== lock.tarball.integrity) fail(`the tarball's integrity is ${integrity}, the lock says ${lock.tarball.integrity}`);

// 2. Its regular files, by name (ustar, with pax path records).
const tar = zlib.gunzipSync(tgz);
const entries = new Map();
const field = (block, at, len) => block.subarray(at, at + len).toString("utf8").replace(/\0.*$/s, "");
for (let at = 0, paxPath = null; at + 512 <= tar.length;) {
  const head = tar.subarray(at, at + 512);
  if (head.every((b) => b === 0)) break;
  const size = parseInt(field(head, 124, 12).trim() || "0", 8);
  const type = String.fromCharCode(head[156] || 48);
  const prefix = field(head, 345, 155);
  const body = tar.subarray(at + 512, at + 512 + size);
  if (type === "x") {
    const m = /\d+ path=([^\n]*)\n/.exec(body.toString("utf8"));
    paxPath = m ? m[1] : null;
  } else {
    const name = paxPath || (prefix ? prefix + "/" : "") + field(head, 0, 100);
    if (type === "0") entries.set(name, Buffer.from(body));
    paxPath = null;
  }
  at += 512 + Math.ceil(size / 512) * 512;
}
const member = (name) => entries.get(name) || fail(`the tarball has no ${name}`);
const pkg = JSON.parse(member("package/package.json").toString("utf8"));
for (const key of ["name", "version", "license"]) {
  if (pkg[key] !== lock.package[key]) fail(`the tarball's package ${key} is ${pkg[key]}, the lock says ${lock.package[key]}`);
}

// 3. The stylesheet, woff2 only.
const named = new Set();
const css = member(lock.files["katex.css"]).toString("utf8").replace(/src:([^;}]*)/g, (all, list) => {
  const woff2 = [...list.matchAll(/url\(([^)]*)\)\s*format\("([^"]*)"\)/g)].filter((m) => m[2] === "woff2");
  if (woff2.length !== 1 || !/^fonts\/KaTeX_[A-Za-z0-9-]+\.woff2$/.test(woff2[0][1])) fail("a font's src has no single fonts/KaTeX_*.woff2: " + all);
  named.add(woff2[0][1].slice("fonts/".length));
  return `src:url(${woff2[0][1]}) format("woff2")`;
});
if (JSON.stringify([...named].sort()) !== JSON.stringify([...lock.fonts].sort())) {
  fail("the stylesheet's fonts are not the lock's:\n  css:  " + [...named].sort().join(", ") + "\n  lock: " + lock.fonts.join(", "));
}
const urls = [...css.matchAll(/url\(([^)]*)\)/g)].map((m) => m[1]);
if (urls.some((u) => !named.has(u.slice("fonts/".length)) || !u.startsWith("fonts/"))) fail("the stylesheet names another url()");
const fonts = lock.fonts.map((name) => {
  const bytes = member("package/dist/fonts/" + name);
  if (bytes.subarray(0, 4).toString("latin1") !== "wOF2") fail(name + " is not a woff2 file");
  return [name, bytes];
});

// 4. The script typesets.
const js = member(lock.files["katex.js"]);
const context = vm.createContext({});
vm.runInContext(js.toString("utf8"), context);
const katex = context[lock.global];
if (!katex || katex.version !== lock.package.version) fail(`katex.min.js does not set ${lock.global} at ${lock.package.version}`);
if (!/class="mfrac"/.test(katex.renderToString("\\frac{a}{b}"))) fail("katex.min.js does not typeset \\frac{a}{b}");

// 5. Write.
const written = [["katex.js", js], ["katex.css", Buffer.from(css, "utf8")], ["LICENSE.katex", member(lock.files["LICENSE.katex"])],
  ...fonts.map(([name, bytes]) => ["fonts/" + name, bytes])];
const readmePath = path.join(vendorDir, "README");
const readme = readFileSync(readmePath, "utf8");
const heading = "katex.js, katex.css, fonts/\n";
const placeholder = "KaTeX (22.0.1, batch B3) is not vendored yet;";
const start = readme.indexOf(heading) >= 0 ? readme.indexOf(heading) : readme.indexOf(placeholder);
const end = readme.indexOf("sha256\n------\n", start);
if (start < 0 || end < 0) fail("vendor/README has neither the KaTeX block nor its placeholder before its sha256 section");
const block = `${heading}${"-".repeat(heading.length - 1)}
KaTeX ${lock.package.version} (${lock.package.license}; licence text: LICENSE.katex here), written by
tools/vendor/build-katex.mjs from the npm tarball ${lock.tarball.name}
(sha256 ${lock.tarball.sha256})
as tools/vendor/katex.lock.json pins it. katex.js is dist/katex.min.js byte for
byte and sets window.${lock.global}; katex.css is dist/katex.min.css with each font's
src cut to its woff2 file, fonts/<name>, which /vendor/katex.css resolves to
/vendor/fonts/<name>; fonts/ holds the ${lock.fonts.length} woff2 fonts byte for byte. Loaded
only by ROLatex.loadMath() (webapp/latex.js), from the editor.

Rebuilding: node tools/vendor/build-katex.mjs <${lock.tarball.name}>. It refuses,
writing nothing, unless the tarball's sha256 and integrity are the lock's, the
stylesheet names exactly the lock's fonts, and the script typesets a formula.
It rewrites only this block of the README. Before committing a KaTeX change, run
RO_KATEX_TGZ=<${lock.tarball.name}> pytest tests/test_v17_vendor.py: without the
tarball its byte-for-byte rebuild check is skipped.

${written.map(([name, bytes]) => `${sha(bytes)}  ${name}`).join("\n")}

`;
mkdirSync(fontsDir, { recursive: true });
for (const old of readdirSync(fontsDir)) unlinkSync(path.join(fontsDir, old));
for (const [name, bytes] of written) writeFileSync(path.join(vendorDir, name), bytes);
writeFileSync(readmePath, readme.slice(0, start) + block + readme.slice(end));
for (const [name, bytes] of written) console.log(`${sha(bytes)}  ${name}`);
