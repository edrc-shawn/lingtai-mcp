/**
 * 灵台 MCP 最小测试服务器（Node.js 版）
 * ======================================
 * 零依赖，仅用 Node.js 内置模块。
 * 验证 HTTPS + Streamable HTTP 连通性。
 *
 * 启动：node test_minimal.mjs
 */

import { createServer } from 'https';
import { readFileSync, existsSync, writeFileSync, mkdirSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { execSync } from 'child_process';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CERT_DIR = resolve(__dirname, '.cache');
const CERT_PATH = resolve(CERT_DIR, 'test-cert-node.pem');

const PORT = 9876;
const HOST = '127.0.0.1';

// ── 生成自签名证书 ──
function ensureCert() {
  if (existsSync(CERT_PATH)) return true;
  mkdirSync(CERT_DIR, { recursive: true });

  try {
    // 使用 openssl 生成
    execSync(
      `openssl req -x509 -newkey rsa:2048 -keyout "${CERT_PATH}" -out "${CERT_PATH}" ` +
      `-days 3650 -nodes ` +
      `-subj "/CN=localhost/O=Lingtai Test/C=CN" ` +
      `-addext "subjectAltName=DNS:localhost,DNS:lingtai.local,DNS:127.0.0.1,IP:127.0.0.1,IP:::1"`,
      { stdio: 'pipe' }
    );
    console.log('   ✅ 证书已生成 (openssl)');
    return true;
  } catch {
    // openssl 不可用，生成一个最小 PEM（用于诊断）
    writeFileSync(CERT_PATH, [
      '-----BEGIN CERTIFICATE-----',
      'MIIBkTCB+wIJAKHBfpLxAAAAADANBgkqhkiG9w0BAQsFADANMQswCQYDVQQGEwJD',
      'TjERMA8GA1UECAwMVW5rbm93bjEQMA4GA1UEBwwHVW5rbm93bjEUMBIGA1UECgwL',
      'TGluZ3RhaSBUZXN0MRIwEAYDVQQDDAlsb2NhbGhvc3QwHhcNMjUwMTAxMDAwMDAw',
      'WhczMzUwMTAxMDAwMDAwWjANMQswCQYDVQQGEwJDTjERMA8GA1UECAwMVW5rbm93',
      'bjEQMA4GA1UEBwwHVW5rbm93bjEUMBIGA1UECgwLTGluZ3RhaSBUZXN0MRIwEAYD',
      'VQQDDAlsb2NhbGhvc3QwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQDN',
      'xLNGKjYbMqXCLzIbqmLOtRJxLCIh7VQ0iC3EONl6rWdTLgYFy3ROFvLLZK+jpPd',
      'V7qJ4eJnJXLsMfVw+qJ8fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9',
      'fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ',
      '9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXq',
      'J9fXqJ9fXqJ9fXqJ9AgMBAAEwDQYJKoZIhvcNAQELBQADggEBADANBgkqhkiG9w0B',
      'AQsFAAOCAQEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      '-----END CERTIFICATE-----',
      '',
      '-----BEGIN PRIVATE KEY-----',
      'MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDNxLNGKjYbMqXC',
      'LzIbqmLOtRJxLCIh7VQ0iC3EONl6rWdTLgYFy3ROFvLLZK+jpPdV7qJ4eJnJXLsMf',
      'Vw+qJ8fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ',
      '9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXq',
      'J9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXqJ9fXq',
      'J9fXqJ9fXqJ9fXqJ9AgMBAAECggEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      '-----END PRIVATE KEY-----',
    ].join('\n'));
    console.log('   ⚠️ 使用测试证书（可能不被信任）');
    return true;
  }
}

// ── MCP 响应构造 ──
function jsonRpc(id, result) {
  return JSON.stringify({ jsonrpc: '2.0', id, result });
}

function jsonRpcError(id, code, message) {
  return JSON.stringify({ jsonrpc: '2.0', id, error: { code, message } });
}

// ── HTTP 处理器 ──
const handler = (req, res) => {
  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Mcp-Session-Id, Authorization');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  // Session ID
  const sessionId = req.headers['mcp-session-id'] || 'test-session-' + Date.now();

  if (req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      status: 'ok',
      name: 'lingtai-test-node',
      version: '0.1.0',
      mode: 'minimal-nodejs',
      session: sessionId,
    }));
    return;
  }

  if (req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      let rpcReq;
      try { rpcReq = JSON.parse(body); } catch { rpcReq = {}; }

      const method = rpcReq?.method || '';
      const id = rpcReq?.id;

      res.writeHead(200, {
        'Content-Type': 'application/json',
        'Mcp-Session-Id': sessionId,
      });

      switch (method) {
        case 'initialize':
          res.end(jsonRpc(id, {
            protocolVersion: '2024-11-05',
            capabilities: { tools: {} },
            serverInfo: { name: 'lingtai-test-node', version: '0.1.0' },
          }));
          break;

        case 'tools/list':
          res.end(jsonRpc(id, { tools: [
            { name: 'health_check', description: '健康检查', inputSchema: { type: 'object', properties: {} } },
            { name: 'ping', description: 'Ping 测试', inputSchema: { type: 'object', properties: {} } },
            { name: 'get_status', description: '获取服务状态', inputSchema: { type: 'object', properties: {} } },
          ]}));
          break;

        case 'tools/call': {
          const toolName = rpcReq?.params?.name || 'unknown';
          const now = new Date().toISOString();
          res.end(jsonRpc(id, {
            content: [{ type: 'text', text: `✅ 工具 [${toolName}] 调用成功！\n时间: ${now}\n这是灵台 MCP 最小测试服务器 (Node.js版)` }],
            isError: false,
          }));
          break;
        }

        case 'ping':
          res.end(jsonRpc(id, {}));
          break;

        default:
          res.end(jsonRpcError(id, -32601, `Method not found: ${method}`));
      }
    });
    return;
  }

  res.writeHead(405);
  res.end();
};

// ── 启动 ──
console.log('\n🔬 灵台 MCP 最小测试服务器 (Node.js)');
console.log('='.repeat(40));
console.log(`   模式: 最小化（零依赖）`);
console.log(`   目的: 验证 HTTPS + Streamable HTTP 连通性\n`);

if (!ensureCert()) {
  console.log('❌ 无法生成证书，退出');
  process.exit(1);
}

try {
  const cert = readFileSync(CERT_PATH);
  const server = createServer({ key: cert, cert: cert }, handler);

  server.listen(PORT, HOST, () => {
    console.log(`\n🚀 测试服务器已启动:`);
    console.log(`   地址: https://${HOST}:${PORT}/mcp`);
    console.log(`   健康: https://${HOST}:${PORT}/health`);
    console.log(`\n   在 Tabbit 中点击「测试连接」验证...\n`);
    console.log(`   按 Ctrl+C 停止\n`);
  });

  server.on('error', (err) => {
    if (err.code === 'EADDRINUSE') {
      console.log(`❌ 端口 ${PORT} 已被占用，请先关闭占用该端口的进程`);
    } else {
      console.log(`❌ 服务器启动失败: ${err.message}`);
    }
    process.exit(1);
  });
} catch (err) {
  console.log(`❌ SSL 上下文创建失败: ${err.message}`);
  process.exit(1);
}
