# -*- coding: utf-8 -*-
"""Lingtai MCP Diagnostic Server v5 - Final"""
import json, sys, os, ssl, datetime, subprocess, argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CERT_DIR = SCRIPT_DIR / ".cache"
CERT_PATH = CERT_DIR / "lingtai-cert.pem"

def ensure_cert():
    if CERT_PATH.exists():
        print("[CERT] OK"); return True
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["openssl","req","-x509","-newkey","rsa:2048",
            "-keyout",str(CERT_PATH),"-out",str(CERT_PATH),
            "-days","3650","-nodes",
            "-subj","/CN=localhost/O=Lingtai/C=CN",
            "-addext","subjectAltName=DNS:localhost,DNS:127.0.0.1,IP:127.0.0.1"],
            check=True, capture_output=True)
        print("[CERT] GENERATED"); return True
    except Exception as e:
        print(f"[CERT] FAIL: {e}"); return False

class H(BaseHTTPRequestHandler):
    def log_message(self,f,*a): print(f"[HTTP] {a[0]}")
    def _rb(self):
        n=int(self.headers.get('Content-Length',0))
        if n>0:
            d=self.rfile.read(n)
            try: return d.decode('utf-8'),json.loads(d)
            except: return d.decode('utf-8',errors='replace'),{}
        return '',{}
    def _send(self,d,s=200,ex=None):
        b=json.dumps(d,ensure_ascii=False).encode('utf-8')
        self.send_response(s)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(b)))
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers','*')
        if ex:
            for k,v in ex.items(): self.send_header(k,str(v))
        self.end_headers()
        self.wfile.write(b)
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type,Mcp-Session-Id,Authorization')
        self.send_header('Access-Control-Max-Age','86400')
        self.end_headers()
    def do_GET(self):
        print(f"\n[GET] {self.path}")
        for k,v in self.headers.items(): print(f"  H {k}: {v}")
        self._send({'status':'ok','name':'lingtai-v5','time':datetime.datetime.now().isoformat()})
    def do_POST(self):
        raw,req=self._rb()
        sid=self.headers.get('Mcp-Session-Id',None)
        if not sid:
            sid=f'lts-{datetime.datetime.now().timestamp():.6f}-{os.getpid()}'
        print(f"\n{'='*60}")
        print(f"[POST] {self.path}")
        print(f"  Session: {sid}")
        for k,v in self.headers.items(): print(f"  H {k}: {v}")
        print(f"  Body({len(raw)}): {raw[:500]}")
        m=req.get('method','?');rid=req.get('id');p=req.get('params',{})
        print(f"  -> method={m} id={rid}")
        r=self._handle(m,rid,p,sid)
        rs=json.dumps(r,ensure_ascii=False)
        print(f"  <- Response({len(rs)}): {rs[:300]}\n")
        self._send(r,ex={'Mcp-Session-Id':sid})
    def _handle(self,m,rid,p,sid):
        if m=='initialize':
            return {'jsonrpc':'2.0','id':rid,'result':{
                'protocolVersion':'2024-11-05',
                'capabilities':{'tools':{}},
                'serverInfo':{'name':'lingtai-v5','version':'5.0'}}}
        if m=='tools/list':
            return {'jsonrpc':'2.0','id':rid,'result':{'tools':[
                {'name':'ping','description':'ping','inputSchema':{'type':'object','properties':{}}},
                {'name':'info','description':'info','inputSchema':{'type':'object','properties':{}}}]}}
        if m=='tools/call':
            nm=p.get('name','?');print(f"  [TOOL] {nm}")
            return {'jsonrpc':'2.0','id':rid,'result':{
                'content':[{'type':'text','text':f'OK [{nm}] v5'}],'isError':False}}
        if m=='ping':
            return {'jsonrpc':'2.0','id':rid,'result':{}}
        print(f"  [??] unknown method -> ok")
        return {'jsonrpc':'2.0','id':rid,'result':{'_ok':True}}

def main():
    ap=argparse.ArgumentParser(description='Lingtai MCP v5 Diagnostic Server')
    ap.add_argument('--port',type=int,default=9876)
    ap.add_argument('--host',default='127.0.0.1')
    ap.add_argument('--http',action='store_true',help='use HTTP instead of HTTPS')
    args=ap.parse_args()
    os.environ['LINGTAI_VAULT']=os.environ.get('LINGTAI_VAULT',str(SCRIPT_DIR.parent.parent))
    print('\n'+'#'*60)
    print('#  Lingtai MCP Diagnostic Server v5')
    print('#  All requests logged to terminal')
    print('#'*60+'\n')
    ctx=None;scheme='https'
    if not args.http:
        if ensure_cert():
            ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(CERT_PATH))
            ctx.minimum_version=ssl.TLSVersion.TLSv1_2
        else:
            scheme='http';print('[!] fallback to HTTP')
    else:
        scheme='http'
    u=f'{scheme}://{args.host}:{args.port}'
    print(f'  Listen: {u}')
    print(f'  MCP:    {u}/mcp')
    print(f'  Health: {u}/health\n  Starting...\n')
    srv=HTTPServer((args.host,args.port),H)
    if ctx: srv.socket=ctx.wrap_socket(srv.socket,server_side=True)
    try: srv.serve_forever()
    except KeyboardInterrupt: print('\nStopped')

if __name__=='__main__':
    main()
