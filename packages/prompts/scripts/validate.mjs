import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function walk(dir) {
  const entries = readdirSync(dir);
  const files = [];
  for (const entry of entries) {
    const full = join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      files.push(...walk(full));
    } else if (entry.endsWith(".md")) {
      files.push(full);
    }
  }
  return files;
}

const mdFiles = walk(root);
const problems = [];

for (const file of mdFiles) {
  const text = readFileSync(file, "utf8");
  if (!text.trim()) {
    problems.push(`${file} is empty`);
  }
}

const registryPath = join(root, "registry.json");
let registry;
try {
  registry = JSON.parse(readFileSync(registryPath, "utf8"));
} catch (error) {
  problems.push(`${registryPath} is not valid JSON: ${error.message}`);
}

function validateRegistryEntry(entry, label) {
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
    problems.push(`${label} must be an object`);
    return;
  }
  const version = entry.active;
  if (typeof version !== "string" || !version) {
    problems.push(`${label} must declare an active version`);
    return;
  }
  const promptPath = entry[version];
  if (typeof promptPath !== "string" || !promptPath) {
    problems.push(`${label} has no path for active version ${version}`);
    return;
  }
  const resolvedPath = resolve(root, promptPath);
  const pathFromRoot = relative(root, resolvedPath);
  if (pathFromRoot.startsWith("..") || pathFromRoot === "" || !promptPath.endsWith(".md")) {
    problems.push(`${label} active prompt path is unsafe or not Markdown: ${promptPath}`);
    return;
  }
  try {
    if (!statSync(resolvedPath).isFile()) {
      problems.push(`${label} active prompt is not a file: ${promptPath}`);
    }
  } catch {
    problems.push(`${label} active prompt is missing: ${promptPath}`);
  }
}

if (registry && typeof registry === "object" && !Array.isArray(registry)) {
  for (const family of ["tutor", "quiz", "citation", "guardrail"]) {
    validateRegistryEntry(registry[family], family);
  }
  for (const mode of ["teaching", "assignment", "exam"]) {
    validateRegistryEntry(registry.policy?.[mode], `policy.${mode}`);
  }
}

if (problems.length > 0) {
  console.error(problems.join("\n"));
  process.exit(1);
}

console.log(`Validated ${mdFiles.length} prompt files.`);
