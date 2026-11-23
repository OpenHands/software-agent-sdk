import { parseSync } from 'oxc-parser';

export function parseTypeScript(file, text) {
  const { program, errors } = parseSync(file, text);
  if (errors.length) throw new Error(`${file}: ${errors.map(({ message }) => message).join('\n')}`);
  return program;
}

export function visitChildren(node, visit) {
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) {
      for (const child of value) if (child && typeof child.type === 'string') visit(child);
    } else if (value && typeof value.type === 'string') {
      visit(value);
    }
  }
}
