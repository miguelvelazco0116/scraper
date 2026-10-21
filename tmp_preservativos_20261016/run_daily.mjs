import fs from "node:fs/promises";
import { spawnSync } from "node:child_process";

const projectDir = process.env.SCRAPER_PROJECT_DIR || new URL(".", import.meta.url).pathname;
const normalizedProjectDir = projectDir.replace(/\/$/, "");
const captureDate = process.env.SCRAPER_CAPTURE_DATE || new Date().toISOString().slice(0, 10);
const dataDir = `${normalizedProjectDir}/data`;
const outputDir = process.env.SCRAPER_OUTPUT_DIR || `${normalizedProjectDir}/outputs`;

function runNode(script, extraEnv = {}) {
  const result = spawnSync(process.execPath, ["--max-old-space-size=4096", `${normalizedProjectDir}/${script}`], {
    cwd: normalizedProjectDir,
    env: {
      ...process.env,
      SCRAPER_PROJECT_DIR: normalizedProjectDir,
      SCRAPER_CAPTURE_DATE: captureDate,
      SCRAPER_OUTPUT_DIR: outputDir,
      ...extraEnv,
    },
    encoding: "utf8",
    stdio: ["inherit", "pipe", "pipe"],
  });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.status !== 0) throw new Error(`${script} terminó con código ${result.status}`);
}

runNode("scrape_full_catalog.mjs");

const statusPath = `${dataDir}/estado_cobertura_${captureDate}.json`;
const status = JSON.parse(await fs.readFile(statusPath, "utf8"));
if (status.historicalWorkbookUpdateAllowed !== true) {
  throw new Error(
    `La captura ${captureDate} no puede incorporarse al histórico: ` +
    `allRetailersRepresented=${status.allRetailersRepresented}; ` +
    `faltantes sin documentar=${(status.missingPriceUrls || []).length}`,
  );
}

runNode("build_history.mjs");

console.log(JSON.stringify({
  captureDate,
  historicalWorkbook: `${outputDir}/Farmacias_Preservativos_2026-08-17.xlsx`,
  captureProducts: status.products,
  capturePrices: status.withPrice,
  strictValidationComplete: status.validationComplete,
  pendingSanPabloDom: status.pendingSanPabloDom,
  historicalWorkbookUpdated: true,
}, null, 2));