# -*- coding: utf-8 -*-
"""
MCP HTTP Bridge — 将 stdio MCP server 包装为 HTTP 端点
用于 @modelcontextprotocol/conformance 等需 HTTP 协议的测试工具。

用法:
    python mcp_http_bridge.py                  # 默认端口 9877，HTTP
    python mcp_http_bridge.py --port 9877      # 指定端口
    python mcp_http_bridge.py --https           # HTTPS (自签名证书)

    # 在另一个终端运行 conformance:
    npx @modelcontextprotocol/conformance server --url http://127.0.0.1:9877/mcp
"""

import json, os, sys, subprocess, threading, ssl, signal
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

LINGTAI_KB = Path(__file__).resolve().parent
VAULT = os.environ.get("LINGTAI_VAULT", r".")


class MCPStdioBridge:
    """包装 stdio MCP server 子进程，提供线程安全的调用"""

    def __init__(self):
        self.proc = None
        self._lock = threading.Lock()

    def start(self):
        self.proc = subprocess.Popen(
            [sys.executable, 'mcp_server.py'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(LINGTAI_KB),
            env={'LINGTAI_VAULT': VAULT, 'PYTHONIOENCODING': 'utf-8',
                 'LINGTAI_CLIENT_ID': 'http-bridge'},
        )

    def call(self, req: dict) -> dict:
        """发送 JSON-RPC 请求，返回响应"""
        raw = json.dumps(req, ensure_ascii=False) + '\n'
        with self._lock:
            self.proc.stdin.write(raw.encode('utf-8'))
            self.proc.stdin.flush()
            # 读一行
            line = b''
            while True:
                ch = self.proc.stdout.read(1)
                if not ch:
                    raise ConnectionError("MCP process closed stdout")
                line += ch
                if ch == b'\n':
                    break
        return json.loads(line.decode('utf-8'))

    def close(self):
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=3)
            except:
                self.proc.kill()


class MCPHTTPHandler(BaseHTTPRequestHandler):
    """HTTP → stdio MCP 桥接处理器"""

    bridge: MCPStdioBridge = None  # 类变量，在 main 中设置

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({
            "ok": True, "server": "lingtai-mcp-http-bridge",
            "usage": "POST /mcp with JSON-RPC body"
        }, ensure_ascii=False).encode('utf-8'))

    def do_POST(self):
        path = urlparse(self.path).path
        if path != '/mcp':
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"only POST /mcp"}')
            return

        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body.decode('utf-8'))
        except json.JSONDecodeError as e:
            self._respond(400, {"jsonrpc": "2.0", "id": None,
                                "error": {"code": -32700, "message": f"Parse error: {e}"}})
            return

        if self.bridge is None:
            self._respond(503, {"jsonrpc": "2.0", "id": req.get("id"),
                                "error": {"code": -32000, "message": "Bridge not initialized"}})
            return

        try:
            resp = self.bridge.call(req)
            self._respond(200, resp)
        except ConnectionError as e:
            self._respond(503, {"jsonrpc": "2.0", "id": req.get("id"),
                                "error": {"code": -32000, "message": str(e)}})
        except Exception as e:
            self._respond(500, {"jsonrpc": "2.0", "id": req.get("id"),
                                "error": {"code": -32000, "message": str(e)}})

    def _respond(self, status: int, body: dict):
        data = json.dumps(body, ensure_ascii=False)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data.encode('utf-8'))

    def log_message(self, fmt, *args):
        pass  # 静默日志


def main():
    import argparse
    parser = argparse.ArgumentParser(description="MCP HTTP Bridge")
    parser.add_argument('--port', type=int, default=9877, help='HTTP port')
    parser.add_argument('--host', default='127.0.0.1', help='Bind address')
    parser.add_argument('--https', action='store_true', help='Use HTTPS with self-signed cert')
    args = parser.parse_args()

    # 启动 MCP 子进程
    print(f"  🚀 启动 MCP stdio server...")
    bridge = MCPStdioBridge()
    bridge.start()
    MCPHTTPHandler.bridge = bridge
    print(f"  ✅ MCP started")

    # 启动 HTTP 服务器
    server = HTTPServer((args.host, args.port), MCPHTTPHandler)
    scheme = 'https' if args.https else 'http'

    if args.https:
        cert_dir = LINGTAI_KB / ".cache"
        cert_dir.mkdir(exist_ok=True)
        cert_path = cert_dir / "bridge_cert.pem"
        if not cert_path.exists():
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(cert_path), "-out", str(cert_path),
                "-days", "365", "-nodes",
                "-subj", "/CN=localhost/O=Lingtai/C=CN",
            ], check=True, capture_output=True)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert_path))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)

    url = f"{scheme}://{args.host}:{args.port}/mcp"
    print(f"  🌐 HTTP bridge ready: {url}")
    print(f"  📋 Run conformance: npx @modelcontextprotocol/conformance server --url {url}")
    print(f"  ⏎  Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down...")
    finally:
        server.server_close()
        bridge.close()


if __name__ == "__main__":
    main()