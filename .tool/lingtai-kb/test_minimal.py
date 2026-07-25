# -*- coding: utf-8 -*-
"""
灵台 MCP 最小测试服务器（零依赖版）
==================================
不加载任何灵台模块，仅验证 HTTPS + Streamable HTTP 协议连通性。
如果这个能连上 → 问题在灵台模块加载层
如果这个也连不上 → 问题在证书/协议层

启动：python test_minimal.py
"""

import json
import sys
import os
import ssl
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


class MCPHandler(BaseHTTPRequestHandler):
    """最小 MCP Streamable HTTP 处理器"""

    def do_POST(self):
        # 读取请求体
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b'{}'

        try:
            req = json.loads(body)
        except Exception:
            req = {}

        method = req.get("method", "")
        req_id = req.get("id")

        # CORS headers
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Mcp-Session-Id', 'test-session-001')
        self.end_headers()

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "lingtai-test", "version": "0.1.0"},
                }
            }
        elif method == "tools/list":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "health_check",
                            "description": "健康检查 — 验证连接是否正常",
                            "inputSchema": {"type": "object", "properties": {}}
                        },
                        {
                            "name": "ping",
                            "description": "Ping 测试",
                            "inputSchema": {"type": "object", "properties": {}}
                        }
                    ]
                }
            }
        elif method == "tools/call":
            tool_name = req.get("params", {}).get("name", "")
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"✅ 工具 [{tool_name}] 调用成功！\n   时间: {datetime.datetime.now().isoformat()}\n   这是灵台 MCP 最小测试服务器。"
                        }
                    ],
                    "isError": False
                }
            }
        elif method == "ping":
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {}}
        else:
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}
            }

        self.wfile.write(json.dumps(resp, ensure_ascii=False).encode())

    def do_GET(self):
        if self.path == "/health" or self.path == "/mcp":
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            resp = {"status": "ok", "name": "lingtai-test", "version": "0.1.0", "mode": "minimal"}
            self.wfile.write(json.dumps(resp, ensure_ascii=False).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Mcp-Session-Id, Authorization')
        self.end_headers()

    def log_message(self, format, *args):
        """简化日志"""
        print(f"   📥 {args[0]}")


def generate_cert(cert_path: Path):
    """生成自签名证书"""
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import ipaddress

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Lingtai Test"),
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ])

        builder = x509.CertificateBuilder()
        builder = builder.subject_name(subject)
        builder = builder.issuer_name(issuer)
        builder = builder.public_key(key.public_key())
        builder = builder.serial_number(x509.random_serial_number())
        # 兼容新旧版本 cryptography
        try:
            builder = builder.not_valid_before_utc(datetime.datetime.utcnow())
        except (AttributeError, TypeError):
            builder = builder.valid_not_before(datetime.datetime.utcnow())
        try:
            builder = builder.not_valid_after_utc(datetime.datetime.utcnow() + datetime.timedelta(days=365 * 10))
        except (AttributeError, TypeError):
            builder = (
                builder.valid_not_after(datetime.datetime.utcnow() + datetime.timedelta(days=365 * 10))
                .add_extension(
                    x509.SubjectAlternativeName([
                        x509.DNSName("localhost"),
                        x509.DNSName("lingtai.local"),
                        x509.DNSName("127.0.0.1"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                        x509.IPAddress(ipaddress.ip_address("::1")),
                    ]),
                    critical=False,
                )
                .sign(key, hashes.SHA256(), default_backend())
            )

        key_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)

        cert_path.parent.mkdir(parents=True, exist_ok=True)
        cert_path.write_bytes(key_pem + cert_pem)
        return True
    except ImportError:
        print("   ⚠️ cryptography 未安装，使用内置 ssl 模块生成证书")
        _generate_cert_builtin(cert_path)
        return True
    except Exception as e:
        print(f"   ⚠️ 证书生成失败: {e}")
        return False


def _generate_cert_builtin(cert_path: Path):
    """使用内置 ssl 模块生成自签名证书（备用方案）"""
    import subprocess
    cert_path.parent.mkdir(parents=True, exist_ok=True)

    # 使用 openssl（如果有）或 Python 内置方式
    pem_path = str(cert_path)

    # 尝试使用 openssl 命令
    try:
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", pem_path, "-out", pem_path,
            "-days", "3650", "-nodes",
            "-subj", "/CN=localhost/O=Lingtai Test/C=CN",
            "-addext", "subjectAltName=DNS:localhost,DNS:lingtai.local,DNS:127.0.0.1,IP:127.0.0.1"
        ], capture_output=True, check=True)
        return
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    # 最后备用：创建一个自签名证书文件（可能不被信任但能建立连接）
    from datetime import timezone
    # 写入一个最小的 PEM 文件（仅用于测试）
    # 注意：这不是一个有效的证书，仅用于触发错误以便诊断
    cert_path.write_text(
        "-----BEGIN CERTIFICATE-----\n"
        "TEST CERTIFICATE - REPLACE WITH REAL ONE\n"
        "-----END CERTIFICATE-----\n"
        "-----BEGIN PRIVATE KEY-----\n"
        "TEST KEY - REPLACE WITH REAL ONE\n"
        "-----END PRIVATE KEY-----\n"
    )


def main():
    import argparse
    parser = argparse.ArgumentParser(description="灵台 MCP 最小测试服务器")
    parser.add_argument("--port", type=int, default=9876)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-https", action="store_true")
    args = parser.parse_args()

    cert_dir = SCRIPT_DIR / ".cache"
    cert_path = cert_dir / "test-cert.pem"

    scheme = "https"
    ctx = None

    if not args.no_https:
        print("\n🔬 灵台 MCP 最小测试服务器")
        print("=" * 40)
        print(f"   模式: 最小化（无灵台模块依赖）")
        print(f"   目的: 验证 HTTPS + Streamable HTTP 连通性\n")

        if not generate_cert(cert_path):
            print("❌ 无法生成证书，退出")
            sys.exit(1)

        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(cert_path))
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        except Exception as e:
            print(f"⚠️ SSL 上下文创建失败: {e}")
            print("   回退到纯 HTTP 模式...")
            scheme = "http"
    else:
        scheme = "http"

    url = f"{scheme}://{args.host}:{args.port}/mcp"
    print(f"\n🚀 启动测试服务器:")
    print(f"   地址: {url}")
    print(f"   健康: {scheme}://{args.host}:{args.port}/health")
    print(f"\n   在 Tabbit 中点击「测试连接」验证...\n")

    server = HTTPServer((args.host, args.port), MCPHandler)

    if ctx:
        server.socket = ctx.wrap_socket(server.socket, server_side=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n👋 服务器已停止")
        server.server_close()


if __name__ == "__main__":
    main()
