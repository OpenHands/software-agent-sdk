import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const contractPath = process.argv[2]
  ? path.resolve(process.argv[2])
  : path.join(root, 'client-behavior-parity.json');
const contract = JSON.parse(fs.readFileSync(contractPath, 'utf8'));
const errors = [];

for (const exception of contract.exceptions ?? []) {
  for (const field of ['id', 'reason', 'owner', 'trackingIssue']) {
    if (typeof exception[field] !== 'string' || !exception[field].trim()) {
      errors.push(`Exception ${exception.id ?? '<unknown>'} must define ${field}`);
    }
  }
}

for (const capability of contract.capabilities ?? []) {
  for (const language of ['typescript', 'python']) {
    const target = capability[language];
    if (!target?.file || !target?.symbol) {
      errors.push(`${capability.id}: missing ${language} file or symbol`);
      continue;
    }
    const file = path.resolve(path.dirname(contractPath), target.file);
    if (!fs.existsSync(file)) {
      errors.push(`${capability.id}: ${language} file does not exist: ${target.file}`);
      continue;
    }
    const source = fs.readFileSync(file, 'utf8');
    const escaped = target.symbol.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    if (!new RegExp(`\\b${escaped}\\b`).test(source)) {
      errors.push(
        `${capability.id}: ${language} symbol ${target.symbol} not found in ${target.file}`
      );
    }
  }
}

if (errors.length) {
  console.error(
    'Client behavior parity check failed:\n' + errors.map((error) => `- ${error}`).join('\n')
  );
  process.exit(1);
}
console.log(`Client behavior parity check passed (${contract.capabilities.length} capabilities).`);
