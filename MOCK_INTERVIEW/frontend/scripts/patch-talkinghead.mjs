// Makes @met4citizen/talkinghead bundle under Next.js (Turbopack and webpack).
//
// The pinned commit of the library has two problems for bundlers:
//   1. talkinghead.mjs does `new URL('./playback-worklet.js', import.meta.url)`, but the package
//      does not ship playback-worklet.js -> "Module not found" and the interview room returns 500.
//   2. lipsyncGetProcessor() does `import(moduleName)` with a computed path, which Turbopack
//      rejects at build time.
// This script copies the worklet next to the module and replaces the computed import with static
// imports of the lip-sync modules the package ships. It is idempotent and exits non-zero if the
// library's code no longer matches what it expects (so a silent upstream change can't slip through).
//
// Runs on postinstall, predev and prebuild.
import { copyFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const modDir = join(root, "node_modules", "@met4citizen", "talkinghead", "modules");
const mainFile = join(modDir, "talkinghead.mjs");
const MARKER = "/* niti-patched: static lipsync imports */";

if (!existsSync(mainFile)) {
  console.log("[patch-talkinghead] package not installed, skipping");
  process.exit(0);
}

// 1. worklet
const worklet = join(modDir, "playback-worklet.js");
if (!existsSync(worklet)) {
  copyFileSync(join(root, "public", "playback-worklet.js"), worklet);
  console.log("[patch-talkinghead] added playback-worklet.js");
}

// 2. computed lip-sync import
let src = readFileSync(mainFile, "utf8");
if (src.includes(MARKER)) {
  console.log("[patch-talkinghead] already patched");
  process.exit(0);
}
const original = "      import(moduleName).then( module => {";
if (!src.includes(original)) {
  console.error("[patch-talkinghead] talkinghead.mjs changed upstream; update scripts/patch-talkinghead.mjs");
  process.exit(1);
}
const replacement =
  `      ${MARKER}\n` +
  "      const __lipsyncLoaders = {\n" +
  "        en: () => import('./lipsync-en.mjs'),\n" +
  "        fi: () => import('./lipsync-fi.mjs'),\n" +
  "        de: () => import('./lipsync-de.mjs'),\n" +
  "        fr: () => import('./lipsync-fr.mjs'),\n" +
  "        lt: () => import('./lipsync-lt.mjs'),\n" +
  "      };\n" +
  "      const __load = __lipsyncLoaders[lang.toLowerCase()] || (() => import(/* webpackIgnore: true */ /* turbopackIgnore: true */ moduleName));\n" +
  "      __load().then( module => {";
src = src.replace(original, replacement);
writeFileSync(mainFile, src);
console.log("[patch-talkinghead] replaced computed lip-sync import with static imports");
