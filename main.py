# main.py
from fastapi import FastAPI, Header, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any
import uuid, json

# Known Agent Builder/Agents origins. Add/remove as needed.
ALLOWED_ORIGINS = [
    "https://agents.openai.com",
    "https://builder.openai.com",
    "https://platform.openai.com",
    "https://chat.openai.com",           # sometimes previews/embeds resolve here
    "https://stackblitz.com",            # for your manual tests
    "https://*.stackblitz.io",           # SB preview sandboxes
]

app = FastAPI()

# ==== CORS ====
# We need: allow_credentials=True AND explicit origins (not "*")
# Also expose the custom header so JS can read it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["POST", "OPTIONS", "GET"],
    allow_headers=["content-type", "mcp-session-id"],
    expose_headers=["mcp-session-id"],
)

# Simple in-memory sessions (demo only)
SESSIONS: Dict[str, Dict[str, Any]] = {}

@app.get("/")
def root():
    return {"service": "MCP demo", "endpoints": ["/health", "/mcp"]}

@app.get("/health")
def health():
    return {"ok": True}

# ---- JSON-RPC models ----
class InitializeParams(BaseModel):
    protocolVersion: str
    clientInfo: Dict[str, Any]
    capabilities: Dict[str, Any]

class JsonRpcReq(BaseModel):
    jsonrpc: str
    id: Optional[str] = None
    method: str
    params: Optional[Dict[str, Any]] = None

def _new_session_id() -> str:
    return uuid.uuid4().hex

def _ok(id, result):
    return {"jsonrpc":"2.0","id":id,"result":result}

def _err(id, code, message, data=None):
    out = {"jsonrpc":"2.0","id":id,"error":{"code":code,"message":message}}
    if data is not None:
        out["error"]["data"] = data
    return out

# Explicit preflight—important with some CDNs/proxies
@app.options("/mcp")
def mcp_options(response: Response):
    response.status_code = 204
    # Echo explicit headers for strict agents:
    response.headers["Access-Control-Allow-Origin"] = ", ".join(ALLOWED_ORIGINS)
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "content-type, mcp-session-id"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Expose-Headers"] = "mcp-session-id"
    return

@app.post("/mcp")
async def mcp(request: Request, response: Response,
              mcp_session_id: Optional[str] = Header(None)):

    # Defensive: ensure these are present even if middleware is bypassed by an edge
    response.headers["Access-Control-Expose-Headers"] = "mcp-session-id"

    try:
        payload = await request.json()
    except Exception:
        response.status_code = 400
        return _err(None, -32700, "Parse error")

    try:
        req = JsonRpcReq(**payload)
    except Exception as e:
        response.status_code = 400
        return _err(None, -32600, "Invalid Request", str(e))

    # initialize -> create session and expose header
    if req.method == "initialize":
        try:
            _ = InitializeParams(**(req.params or {}))
        except Exception as e:
            response.status_code = 400
            return _err(req.id, -32602, "Invalid request parameters", str(e))
        sid = _new_session_id()
        SESSIONS[sid] = {"ready": False}
        response.headers["mcp-session-id"] = sid
        # Also put sessionId in body for human debugging (Agent Builder ignores it, harmless)
        return _ok(req.id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "sessionId": sid
        })

    # After initialize, a valid session id is required
    if not mcp_session_id or mcp_session_id not in SESSIONS:
        response.status_code = 400
        return _err(req.id, -32000, "Missing or invalid session")

    if req.method == "notifications/initialized":
        SESSIONS[mcp_session_id]["ready"] = True
        response.status_code = 202
        return {}

    if req.method == "tools/list":
        tools = [
            {
                "name":"ping",
                "description":"health check",
                "inputSchema":{"type":"object","properties":{"message":{"type":"string"}}}
            },
            {
                "name":"get_student_profile",
                "description":"Lookup by student_id",
                "inputSchema":{
                    "type":"object",
                    "required":["student_id"],
                    "properties":{"student_id":{"type":"string"}}
                }
            }
        ]
        return _ok(req.id, {"tools": tools})

    if req.method == "tools/call":
        name = (req.params or {}).get("name")
        args = (req.params or {}).get("arguments") or {}

        if name == "ping":
            msg = args.get("message","")
            return _ok(req.id, {
                "content":[{"type":"text","text":f"pong: {msg}"}],
                "structuredContent":{"ok":True}
            })

        if name == "get_student_profile":
            sid = args.get("student_id","")
            FIX = {
                "student_en_001": {
                    "id":"student_en_001","first_name":"Ava","last_name":"Johnson",
                    "language":"en","eligible_fafsa":True,"year":"2025–26",
                    "dependency":"dependent","parent_status_2023":"divorced",
                    "contributors_expected":2,"schools":["Harvard University"]
                },
                "student_es_001": {
                    "id":"student_es_001","first_name":"Mateo","last_name":"García",
                    "language":"es","eligible_fafsa":True,"year":"2025–26",
                    "dependency":"dependent","parent_status_2023":"divorciado",
                    "contributors_expected":2,"schools":["Universidad de Harvard"]
                }
            }
            if sid in FIX:
                obj = FIX[sid]
                return _ok(req.id, {
                    "content":[{"type":"text","text":json.dumps(obj, ensure_ascii=False)}],
                    "structuredContent": obj,
                    "isError": False
                })
            else:
                miss = {"error":"not found","student_id":sid}
                return _ok(req.id, {
                    "content":[{"type":"text","text":json.dumps(miss, ensure_ascii=False)}],
                    "structuredContent": miss,
                    "isError": False
                })

        return _err(req.id, -32601, f"Unknown tool '{name}'")

    return _err(req.id, -32601, f"Unknown method '{req.method}'")
