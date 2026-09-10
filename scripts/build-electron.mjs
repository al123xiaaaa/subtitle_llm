import { copyFile, readdir, rm, mkdir } from "node:fs/promises";
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

// lib 以 bundle:false 转译，desktop-contract.json 的 import 在运行时按相对路径
// 解析（dist/electron/lib -> dist/src/...），这里把契约镜像到对应位置。
const contractSource = path.join(projectRoot, "src", "subtitle_llm", "config", "desktop-contract.json");
const contractTarget = path.join(projectRoot, "dist", "src", "subtitle_llm", "config", "desktop-contract.json");
await mkdir(path.dirname(contractTarget), { recursive: true });
await copyFile(contractSource, contractTarget);
