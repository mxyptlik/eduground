import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(new URL("..", import.meta.url)));

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

if (problems.length > 0) {
  console.error(problems.join("\n"));
  process.exit(1);
}

console.log(`Validated ${mdFiles.length} prompt files.`);
