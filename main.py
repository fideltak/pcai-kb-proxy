import asyncio
import json
import logging
import os
import time
import uuid

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from urllib3.exceptions import InsecureRequestWarning

CONFIG = {
    "BASE_URL": os.getenv("BASE_URL", "").rstrip("/"),
    "HOST": os.getenv("HOST", "0.0.0.0"),
    "PORT": os.getenv("PORT", "8080"),
    "VERIFY_SSL": os.getenv("VERIFY_SSL", "true"),
    "TIMEOUT": os.getenv("TIMEOUT", "300"),
    "SA_NAME": os.getenv("SA_NAME", ""),
    "ENABLE_CITATIONS": os.getenv("ENABLE_CITATIONS", "true"),
    "MODEL_NAME": os.getenv("MODEL_NAME", ""),
    "PROXY_NAME": os.getenv("PROXY_NAME", "HPE-PCAI-KB-PROXY"),
    "PROMPT_TEMPLATE": """Context:
{context}

Chat_Conversations:
{chat_history}

Query:
{query}""",
}

app = FastAPI(title="KB Proxy")
logger = logging.getLogger("kb_proxy")


def validate_config():
    for name, value in CONFIG.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is empty or invalid")

    try:
        CONFIG["PORT"] = int(CONFIG["PORT"])
        CONFIG["TIMEOUT"] = int(CONFIG["TIMEOUT"])
    except ValueError as exc:
        raise ValueError("PORT and TIMEOUT must be integers") from exc

    if not 1 <= CONFIG["PORT"] <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    if CONFIG["TIMEOUT"] <= 0:
        raise ValueError("TIMEOUT must be greater than 0")
    if not CONFIG["BASE_URL"].startswith(("http://", "https://")):
        raise ValueError("BASE_URL must start with http:// or https://")

    for name in ("VERIFY_SSL", "ENABLE_CITATIONS"):
        value = CONFIG[name].lower()
        if value not in ("true", "false"):
            raise ValueError(f"{name} must be true or false")
        CONFIG[name] = value == "true"

    if not CONFIG["VERIFY_SSL"]:
        requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    logger.info("Configuration validation successful")


@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = uuid.uuid4().hex[:8]
    request.state.request_id = request_id
    started = time.monotonic()
    client = request.client.host if request.client else "unknown"
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        logger.info("[%s] IN %s %s client=%s -> %s duration=%.3fs",
                    request_id, request.method, request.url.path, client,
                    status, time.monotonic() - started)


def get_bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise HTTPException(status_code=401, detail="Bearer token is required")
    return authorization[7:].strip()


def get_messages(messages):
    if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
        raise HTTPException(status_code=400, detail="messages must be an array of objects")
    system_messages = [m for m in messages if m.get("role") in ("system", "developer")]
    user_messages = [m for m in messages if m.get("role") == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="User message is required")
    system_context = system_messages[0].get("content", "") if system_messages else ""
    prompt = user_messages[-1].get("content")
    if not isinstance(system_context, str):
        raise HTTPException(status_code=400, detail="System content must be a string")
    if not isinstance(prompt, str):
        raise HTTPException(status_code=400, detail="User content must be a string")
    for message in messages:
        if message.get("role") in ("user", "assistant") and not isinstance(message.get("content"), str):
            raise HTTPException(status_code=400, detail="Message content must be a string")
    return system_context


def extract_answer(result):
    try:
        answer = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="Unsupported PCAI RAG Coordinator response format") from exc
    if not isinstance(answer, str):
        raise HTTPException(status_code=502, detail="Response content must be a string")
    return answer


def make_completion(model, answer, usage=None):
    completion = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
    }
    if usage is not None:
        completion["usage"] = usage
    return completion


def stream_completion(completion):
    for index, delta in enumerate(({"role": "assistant", "content": ""},
                                   {"content": completion["choices"][0]["message"]["content"]}, {})):
        chunk = {
            "id": completion["id"], "object": "chat.completion.chunk",
            "created": completion["created"], "model": completion["model"],
            "choices": [{"index": 0, "delta": delta, "finish_reason": "stop" if index == 2 else None}],
        }
        yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
    yield "data: [DONE]\n\n"


def upstream_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "X-SA-NAME": CONFIG["SA_NAME"],
        "X-ENABLE-CITATIONS": str(CONFIG["ENABLE_CITATIONS"]).lower(),
    }


async def forward(request: Request, method: str, path: str, token: str, body=None):
    url = f"{CONFIG['BASE_URL']}{path}"
    request_id = request.state.request_id
    started = time.monotonic()
    logger.info("[%s] FW %s %s", request_id, method, url)
    try:
        response = await asyncio.to_thread(
            requests.request, method, url,
            headers=upstream_headers(token), json=body,
            timeout=CONFIG["TIMEOUT"], verify=CONFIG["VERIFY_SSL"],
        )
    except requests.RequestException as exc:
        logger.error("[%s] FW %s %s failed: %s", request_id, method, url, type(exc).__name__)
        raise HTTPException(status_code=502, detail="PCAI RAG Coordinator connection failed") from exc
    logger.info("[%s] FW %s %s -> %s duration=%.3fs", request_id,
                method, url, response.status_code, time.monotonic() - started)
    return response


def ensure_upstream_ok(response, request_id):
    if response.ok:
        return
    logger.error("[%s] PCAI RAG Coordinator returned HTTP %s", request_id, response.status_code)
    raise HTTPException(status_code=response.status_code,
                        detail="PCAI RAG Coordinator denied or failed the request")


def parse_upstream_json(response, request_id):
    try:
        return response.json()
    except ValueError as exc:
        logger.error("[%s] PCAI RAG Coordinator returned invalid JSON", request_id)
        raise HTTPException(status_code=502, detail="Invalid PCAI RAG Coordinator JSON response") from exc


@app.get("/health")
def health():
    return {"status": "ok"}



@app.get("/v1/models")
async def list_models(request: Request):
    token = get_bearer_token(request)

    response = await forward(
        request, "GET", "/v1/models", token
    )

    status = response.status_code

    if status == 200:
        # New RAG Coordinator
        logger.info(
            "[%s] RAG Coordinator models API available",
            request.state.request_id
        )

    elif status == 404:
        # Legacy RAG Coordinator
        logger.info(
            "[%s] Legacy RAG Coordinator detected (models API unavailable)",
            request.state.request_id
        )

    elif status in (401, 403):
        raise HTTPException(
            status_code=status,
            detail="RAG Coordinator rejected credentials"
        )

    else:
        logger.error(
            "[%s] Unexpected RAG Coordinator status: HTTP %s",
            request.state.request_id,
            status
        )
        raise HTTPException(
            status_code=502,
            detail="RAG Coordinator authentication check failed"
        )

    return {
        "object": "list",
        "data": [{
            "id": CONFIG["PROXY_NAME"],
            "object": "model",
            "created": 0,
            "owned_by": "kb-proxy"
        }]
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    token = get_bearer_token(request)
    try:
        incoming = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    if not isinstance(incoming, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")
    messages = incoming.get("messages", [])
    system_context = get_messages(messages)
    system_context = system_context.rstrip() + "\n\n" + CONFIG["PROMPT_TEMPLATE"]
    stream = incoming.get("stream", False)
    if not isinstance(stream, bool):
        raise HTTPException(status_code=400, detail="stream must be a boolean")
    upstream_body = {
        "model": CONFIG["MODEL_NAME"],
        "messages": [{"role": "system", "content": system_context}] + [
            {"role": m["role"], "content": m["content"]}
            for m in messages if m.get("role") in ("user", "assistant")
        ],
        "max_tokens": incoming.get("max_tokens", incoming.get("max_completion_tokens", 2048)),
        "frequency_penalty": incoming.get("frequency_penalty", 0),
        "temperature": incoming.get("temperature", 0),
        "presence_penalty": incoming.get("presence_penalty", 1),
        "top_p": incoming.get("top_p", 0.5),
    }
    response = await forward(request, "POST", "/v1/chat/completions", token, upstream_body)
    ensure_upstream_ok(response, request.state.request_id)
    result = parse_upstream_json(response, request.state.request_id)
    answer = extract_answer(result)
    completion = make_completion(CONFIG["PROXY_NAME"], answer,
                                 result.get("usage") if isinstance(result, dict) else None)
    if not stream:
        return JSONResponse(content=completion)
    return StreamingResponse(stream_completion(completion), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


validate_config()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    logger.info("KB Proxy starting host=%s port=%s proxy_model=%s rag_model=%s",
                CONFIG["HOST"], CONFIG["PORT"], CONFIG["PROXY_NAME"], CONFIG["MODEL_NAME"])
    logger.info("PCAI RAG Coordinator base=%s sa_name=%s ssl_verify=%s",
                CONFIG["BASE_URL"], CONFIG["SA_NAME"], CONFIG["VERIFY_SSL"])
    uvicorn.run(app, host=CONFIG["HOST"], port=CONFIG["PORT"], log_level="warning", access_log=False)
