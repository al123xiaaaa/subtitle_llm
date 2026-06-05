import { readdir, rm, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, "..");
const outDir = path.join(projectRoot, "dist", "electron");
const electronDir = path.join(projectRoot, "electron");
const libDir = path.join(electronDir, "lib");

await rm(outDir, { recursive: true, force: true });
await mkdir(outDir, { recursive: true });

const shared = {
  platform: "node",
  target: "node22",
  sourcemap: true,
  logLevel: "silent",
};

await build({
  ...shared,
  entryPoints: [path.join(electronDir, "main.ts")],
  outfile: path.join(outDir, "main.js"),
  bundle: true,
  format: "esm",
  external: ["electron"],
});

await build({
  ...shared,
  entryPoints: [path.join(electronDir, "preload.ts")],
  outfile: path.join(outDir, "preload.cjs"),
  bundle: true,
  format: "cjs",
  external: ["electron"],
});

const libEntryPoints = (await readdir(libDir))
  .filter((fileName) => fileName.endsWith(".ts"))
  .map((fileName) => path.join(libDir, fileName));

await build({
  ...shared,
  entryPoints: libEntryPoints,
  outdir: path.join(outDir, "lib"),
  bundle: false,
  format: "esm",
});
