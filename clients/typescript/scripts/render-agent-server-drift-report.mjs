import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const option = argv[index];
    const value = argv[index + 1];
    if (!option?.startsWith('--') || value === undefined) {
      throw new Error(`Expected --option value arguments, received ${JSON.stringify(argv)}`);
    }
    values[option.slice(2)] = value;
  }
  for (const required of ['sdk-sha', 'summary', 'package', 'run-url', 'output']) {
    if (!values[required]) {
      throw new Error(`Missing required --${required} argument`);
    }
  }
  if (!/^[0-9a-f]{40}$/.test(values['sdk-sha'])) {
    throw new Error(`--sdk-sha must be an exact 40-character commit SHA`);
  }
  return values;
}

const args = parseArgs(process.argv.slice(2));
const packageJson = JSON.parse(await readFile(resolve(args.package), 'utf8'));
const image = packageJson.config?.agentServerImage;
if (typeof image !== 'string') {
  throw new Error('package.json config.agentServerImage must be a string');
}
const summary = (await readFile(resolve(args.summary), 'utf8')).trim();
const sha = args['sdk-sha'];
const testedCommit = `https://github.com/OpenHands/software-agent-sdk/commit/${sha}`;
const marker = '<!-- agent-server-sdk-main-drift -->';
const body = [
  marker,
  '',
  'The informational SDK-main audit found upcoming generated contract drift.',
  '',
  `- Exact SDK commit: [\`${sha}\`](${testedCommit})`,
  `- Client pin: \`${image}\``,
  `- Audit run and downloadable candidate: [workflow run](${args['run-url']})`,
  '',
  '## Generated change summary',
  '',
  summary,
  '',
  'This audit does not gate ordinary client PRs. The next exact SDK release',
  'will open the reproducible pinned update PR.',
  '',
].join('\n');

await writeFile(resolve(args.output), body);
process.stdout.write(body);
