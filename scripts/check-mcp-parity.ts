import { readFile } from 'node:fs/promises';

import { compareHttpAndMcpOrder } from '../src/evaluation/mcp-parity.ts';

const [httpPath, mcpPath] = process.argv.slice(2);
if (!httpPath || !mcpPath) {
  console.error('Usage: npm run check:mcp-parity -- <http-search-or-summary.json> <mcp-tool-result.json>');
  process.exitCode = 2;
} else {
  const [httpText, mcpText] = await Promise.all([
    readFile(httpPath, 'utf8'),
    readFile(mcpPath, 'utf8'),
  ]);
  const result = compareHttpAndMcpOrder(
    JSON.parse(httpText) as unknown,
    JSON.parse(mcpText) as unknown,
  );
  console.log(JSON.stringify(result, null, 2));
  if (result.status !== 'passed') process.exitCode = 1;
}
