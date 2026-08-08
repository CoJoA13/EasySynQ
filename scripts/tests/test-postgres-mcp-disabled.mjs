import assert from 'node:assert/strict';
import { access, readFile } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

async function exists(file) {
  try {
    await access(file);
    return true;
  } catch (error) {
    if (error.code === 'ENOENT') return false;
    throw error;
  }
}

test('repository MCP configuration exposes no PostgreSQL connector', async () => {
  const config = JSON.parse(await readFile(path.join(root, '.mcp.json'), 'utf8'));
  assert.deepEqual(config, { mcpServers: {} });

  const serialized = JSON.stringify(config).toLowerCase();
  assert.equal(serialized.includes('postgres'), false);
  assert.equal(serialized.includes('npx'), false);
  assert.equal(serialized.includes('@modelcontextprotocol/server-postgres'), false);
});

test('deprecated PostgreSQL MCP package and launcher are absent', async () => {
  const forbidden = [
    'tools/mcp-postgres/package.json',
    'tools/mcp-postgres/package-lock.json',
    'scripts/run-postgres-mcp.sh',
  ];
  for (const relativePath of forbidden) {
    assert.equal(await exists(path.join(root, relativePath)), false, `${relativePath} must be absent`);
  }
});
