// Loads only the official SystemPrompt service, without an agent or tool plugin.
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { readFile } from 'node:fs/promises';

const dependencies = process.argv[2];
const packagePath = join(dependencies, '@deepseek-ai/dsh-system-prompt');
const info = JSON.parse(await readFile(join(packagePath, 'package.json'), 'utf8'));
const chunks = [];
let inputBytes = 0;
for await (const chunk of process.stdin) {
  inputBytes += chunk.length;
  if (inputBytes > 4 * 1024 * 1024) throw new Error('Input exceeds compiler bound');
  chunks.push(chunk);
}
const value = JSON.parse(Buffer.concat(chunks, inputBytes).toString('utf8'));
if (value.version !== '0.2.2-rc.1' || info.version !== value.version || !Array.isArray(value.sections) || value.sections.length > 64) {
  throw new Error('Unsupported prompt assembly input');
}
const names = new Set();
for (const section of value.sections) {
  if (!section || typeof section.name !== 'string' || typeof section.text !== 'string' || names.has(section.name)) {
    throw new Error('Invalid prompt section');
  }
  names.add(section.name);
}
const { Context } = await import(pathToFileURL(join(dependencies, '@deepseek-ai/cordis/lib/index.js')));
const { SystemPrompt, renderPrompt } = await import(pathToFileURL(join(packagePath, 'lib/index.js')));
const context = new Context();
await context.plugin(SystemPrompt, { includeHarnessIdentity: false, includeRuntimeContext: false });
try {
  value.sections.forEach((section, index) => context.systemPrompt.section({
    name: section.name, order: index, text: section.text, interpolate: false,
  }));
  const assembly = await context.systemPrompt.assemble();
  if (assembly.tools.length || assembly.contexts.length) throw new Error('Unexpected tool or context provider');
  process.stdout.write(JSON.stringify({ text: renderPrompt(assembly), version: info.version,
    toolCount: assembly.tools.length, contextCount: assembly.contexts.length }));
} finally {
  await context.fiber.dispose();
}
