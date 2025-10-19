# main.py
from fastapi import FastAPI, Header, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, Dict, Any
import uuid, json

app = FastAPI()

# Broad CORS so Agent Builder can talk to us from any origin while you test
ALLOWED_ORIGINS = [
    "https://platform.openai.com",
    "https://builder.openai.com",
    "https://chat.openai.com",
    "*",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*", "content-type", "mcp-session-id", "x-mcp-session-id"],
    expose_headers=["mcp-session-id", "x-mcp-session-id"],
    max_age=600,
)

# In-memory session store (demo)
SESSIONS: Dict[str, Dict[str, Any]] = {}

def _new_session_id() -> str:
    return uuid.uuid4().hex

def _ok(id, result):
    return {"jsonrpc": "2.0", "id": id, "result": result}

def _err(id, code, message, data=None):
    out = {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}
    if data is not None:
        out["error"]["data"] = data
    return out

@app.get("/")
def root():
    return {"ok": True, "hint": "Use /health and POST /mcp"}

@app.get("/health")
def health():
    return {"ok": True}

@app.options("/mcp")
def mcp_options(_: Request, response: Response):
    # Satisfy CORS preflight
    response.status_code = 204
    return Response(status_code=204)

@app.post("/mcp")
async def mcp(
    request: Request,
    response: Response,
    mcp_session_id: Optional[str] = Header(None),
    x_mcp_session_id: Optional[str] = Header(None),
):
    # Accept either header name for session continuity
    session_id = mcp_session_id or x_mcp_session_id

    # Parse body as JSON (be permissive)
    try:
        payload = await request.json()
    except Exception as e:
        response.status_code = 400
        return _err(None, -32700, "Parse error")

    # Minimal JSON-RPC shape checks (no strict schema)
    if not isinstance(payload, dict):
        response.status_code = 400
        return _err(None, -32600, "Invalid Request: body must be object")
    method = payload.get("method")
    rpc_id = payload.get("id")

    # ---- initialize (no strict param validation) ----
    if method == "initialize":
        # Do not validate fields; just create session and return capabilities
        sid = _new_session_id()
        SESSIONS[sid] = {"ready": False}
        response.headers["mcp-session-id"] = sid
        response.headers["x-mcp-session-id"] = sid
        return _ok(rpc_id, {"protocolVersion": "2024-11-05", "capabilities": {}})

    # Everything else requires a valid session
    if not session_id or session_id not in SESSIONS:
        response.status_code = 400
        return _err(rpc_id, -32000, "Missing or invalid session")

    # ---- notifications/initialized ----
    if method == "notifications/initialized":
        SESSIONS[session_id]["ready"] = True
        response.status_code = 202
        return {}

    # ---- tools/list ----
    if method == "tools/list":
        tools = [
            {
                "name": "ping",
                "description": "Health check tool",
                "inputSchema": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}}
                }
            },
            {
                "name": "get_student_profile",
                "description": "Lookup by student_id",
                "inputSchema": {
                    "type": "object",
                    "required": ["student_id"],
                    "properties": {"student_id": {"type": "string"}}
                }
            }
        ]
        return _ok(rpc_id, {"tools": tools})

    # ---- tools/call ----
    if method == "tools/call":
        params = payload.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}

        if name == "ping":
            msg = args.get("message", "")
            return _ok(rpc_id, {
                "content": [{"type": "text", "text": f"pong: {msg}"}],
                "structuredContent": {"ok": True}
            })

        if name == "get_student_profile":
            sid = args.get("student_id", "")
            FIX = {
                "student_en_001": {
                    "id": "student_en_001", "first_name": "Ava", "last_name": "Johnson",
                    "language": "en", "eligible_fafsa": True, "year": "2025–26",
                    "dependency": "dependent", "parent_status_2023": "divorced",
                    "contributors_expected": 2, "schools": ["Harvard University"]
                },
                "student_es_001": {
                    "id": "student_es_001", "first_name": "Mateo", "last_name": "García",
                    "language": "es", "eligible_fafsa": True, "year": "2025–26",
                    "dependency": "dependent", "parent_status_2023": "divorciado",
                    "contributors_expected": 2, "schools": ["Universidad de Harvard"]
                }
            }
            if sid in FIX:
                obj = FIX[sid]
                return _ok(rpc_id, {
                    "content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}],
                    "structuredContent": obj,
                    "isError": False
                })
            else:
                miss = {"error": "not found", "student_id": sid}
                return _ok(rpc_id, {
                    "content": [{"type": "text", "text": json.dumps(miss, ensure_ascii=False)}],
                    "structuredContent": miss,
                    "isError": False
                })

        return _err(rpc_id, -32601, f"Unknown tool '{name}'")

    # Unknown method
    return _err(rpc_id, -32601, f"Unknown method '{method}'")
