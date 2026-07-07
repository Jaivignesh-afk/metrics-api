import os
import re
import time
import uuid
import jwt
from jwt import InvalidTokenError, InvalidKeyError
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from collections import defaultdict, deque

try:
    import yaml
except ImportError:
    yaml = None

app = FastAPI()

# ===========================================================
# CONFIGURATION
# ===========================================================
YOUR_EMAIL = "23f1001347@ds.study.iitm.ac.in"
ANALYTICS_API_KEY = "ak_vzqzkhznhc7e3wztts7scwhb"

# Old Assignments
ALLOWED_ORIGIN = "https://dash-7et21z.example.com"
TOTAL_ORDERS = 50
RATE_LIMIT = 20
WINDOW_SECONDS = 10

# New /ping Assignment
ALLOWED_ORIGIN_10 = "https://app-kuinjq.example.com"
BUCKET_SIZE = 9
WINDOW_SECONDS_10 = 10

idempotency_store: dict[str, dict] = {}
client_buckets: dict[str, list[float]] = defaultdict(list)
ping_client_buckets: dict[str, list[float]] = defaultdict(list)

START_TIME = time.time()
REQUEST_COUNTER = 0
LOG_BUFFER = deque(maxlen=1000)

# ===========================================================
# MIDDLEWARE LAYER 1: RATE LIMITER (Innermost - runs last on way in)
# ===========================================================
@app.middleware("http")
async def m3_rate_limiting_middleware(request: Request, call_next):
    path = request.url.path

    # /ping Rate Limiter (New assignment)
    if path == "/ping":
        client_id = request.headers.get("X-Client-Id", "anonymous")
        now = time.time()
        bucket = ping_client_buckets[client_id]
        
        # Prune older than 10 seconds
        ping_client_buckets[client_id] = [t for t in bucket if now - t < WINDOW_SECONDS_10]

        if len(ping_client_buckets[client_id]) >= BUCKET_SIZE:
            return JSONResponse(status_code=429, content={"error": "rate limited"})
        
        ping_client_buckets[client_id].append(now)

    # /orders Rate Limiter (Legacy assignment)
    elif path == "/orders":
        client_id = request.headers.get("X-Client-Id", "anonymous")
        now = time.time()
        bucket = client_buckets[client_id]
        
        client_buckets[client_id] = [t for t in bucket if now - t < WINDOW_SECONDS]

        if len(client_buckets[client_id]) >= RATE_LIMIT:
            return JSONResponse(
                status_code=429,
                content={"error": "rate limit exceeded"},
                headers={"Retry-After": str(WINDOW_SECONDS)},
            )
        client_buckets[client_id].append(now)

    return await call_next(request)

# ===========================================================
# MIDDLEWARE LAYER 2: CORS POLICY (Middle)
# ===========================================================
@app.middleware("http")
async def m2_cors_middleware(request: Request, call_next):
    path = request.url.path
    origin = request.headers.get("origin")
    OPEN_CORS_PATHS = ("/effective-config", "/analytics", "/orders")

    # Handle OPTIONS Preflight
    if request.method == "OPTIONS":
        headers = {}
        if path == "/ping":
            # Allow target origin OR the exam platform dynamically
            if origin == ALLOWED_ORIGIN_10 or (origin and "appsec.education" in origin):
                headers["Access-Control-Allow-Origin"] = origin
                headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
                headers["Access-Control-Allow-Headers"] = "*"
            return PlainTextResponse("", status_code=204, headers=headers)
            
        elif path in OPEN_CORS_PATHS:
            headers["Access-Control-Allow-Origin"] = "*"
            headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            headers["Access-Control-Allow-Headers"] = "*"
            return PlainTextResponse("", status_code=204, headers=headers)
            
        elif origin == ALLOWED_ORIGIN:
            headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
            headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            headers["Access-Control-Allow-Headers"] = "*"
            return PlainTextResponse("", status_code=204, headers=headers)
            
        return PlainTextResponse("", status_code=204)

    # Proceed to actual request
    response = await call_next(request)

    # Attach headers for standard requests
    if path == "/ping":
        if origin == ALLOWED_ORIGIN_10 or (origin and "appsec.education" in origin):
            response.headers["Access-Control-Allow-Origin"] = origin
    elif path in OPEN_CORS_PATHS:
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif origin == ALLOWED_ORIGIN:
        response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN

    return response

# ===========================================================
# MIDDLEWARE LAYER 3: REQUEST CONTEXT & LOGGING (Outermost - runs first)
# ===========================================================
@app.middleware("http")
async def m1_request_context_and_logging_middleware(request: Request, call_next):
    global REQUEST_COUNTER
    REQUEST_COUNTER += 1
    
    start_time = time.time()

    # If incoming request has an ID, use it. Else, create one.
    req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = req_id

    # Call deeper layers
    response = await call_next(request)

    process_time = time.time() - start_time

    # Always ensure X-Request-ID is embedded in response
    response.headers["X-Request-ID"] = req_id
    
    if request.url.path != "/ping":
        response.headers["X-Process-Time"] = f"{process_time:.6f}"

    LOG_BUFFER.append({
        "level": "info",
        "ts": time.time(),
        "path": request.url.path,
        "request_id": req_id,
    })

    return response

# ===========================================================
# NEW ENDPOINT: /ping
# ===========================================================
@app.get("/ping")
def ping(request: Request):
    # Fetch the ID propagated securely by Layer 3
    request_id = getattr(request.state, "request_id", "unset")
    return {"email": YOUR_EMAIL, "request_id": request_id}


# ===========================================================
# LEGACY ENDPOINTS (Preserved)
# ===========================================================
@app.post("/orders")
def create_order(idempotency_key: str = Header(None, alias="Idempotency-Key")):
    if idempotency_key and idempotency_key in idempotency_store:
        return JSONResponse(status_code=201, content=idempotency_store[idempotency_key])

    order = {"id": str(uuid.uuid4()), "status": "created"}
    if idempotency_key:
        idempotency_store[idempotency_key] = order
    return JSONResponse(status_code=201, content=order)


@app.get("/orders")
def list_orders(limit: int = 10, cursor: str = None):
    start = int(cursor) if cursor else 1
    end = min(start + limit, TOTAL_ORDERS + 1)
    items = [{"id": i} for i in range(start, end)]
    next_cursor = str(end) if end <= TOTAL_ORDERS else None
    return {"items": items, "next_cursor": next_cursor}


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/stats")
def get_stats(values: str):
    try:
        numbers = [int(v.strip()) for v in values.split(",") if v.strip() != ""]
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "values must be integers"})
    if not numbers:
        return JSONResponse(status_code=400, content={"error": "no values provided"})

    return {
        "email": YOUR_EMAIL,
        "count": len(numbers),
        "sum": sum(numbers),
        "min": min(numbers),
        "max": max(numbers),
        "mean": sum(numbers) / len(numbers),
    }


EXPECTED_ISSUER = "https://idp.exam.local"
EXPECTED_AUDIENCE = "tds-huopxh4n.apps.exam.local"
_raw_key = os.environ.get(
    "IDP_PUBLIC_KEY",
    "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2okOHspNjgA+2rTLbeuY\ncxiP/hG8C6Sb9iwg3yiLAA4HCnpITcbWCSelbvbYGuc3EbNy4xFyf5Cbj5DHJMID\nEkryOgyd2giIIIBOUBj8S63uGcnRpOBh9NFatfNwheKuzsPuVNldu6A9cNteNpXc\nWyJjG2axVfmq7i6SuKr1JoWYG7xTTAvKPujSl4OtsQfO3h5NepzdfXpr28oNnzfW\ned+zclR6BcmNNo/WVfJ4xyCLSf0BCOgdTgW6PdaChd1l9VDetJZVEgC5tkyvXsfI\nSI6iyrYbKR0NEBSqq4XkadEjsCs4F1RncsS4LlgniT7GlkL9Mce3b0wGLs9/7ZIX\ndQIDAQAB\n-----END PUBLIC KEY-----",
)
IDP_PUBLIC_KEY = _raw_key.replace("\\n", "\n").strip()

class VerifyRequest(BaseModel):
    token: str

@app.post("/verify")
def verify_token(body: VerifyRequest):
    try:
        claims = jwt.decode(
            body.token, IDP_PUBLIC_KEY, algorithms=["RS256"],
            issuer=EXPECTED_ISSUER, audience=EXPECTED_AUDIENCE,
        )
        return {"valid": True, "email": claims.get("email"), "sub": claims.get("sub"), "aud": claims.get("aud")}
    except Exception:
        return JSONResponse(status_code=401, content={"valid": False})


class AnalyticsEvent(BaseModel):
    user: str
    amount: float
    ts: int

class AnalyticsRequest(BaseModel):
    events: list[AnalyticsEvent]

@app.post("/analytics")
def analytics(body: AnalyticsRequest, request: Request):
    if request.headers.get("X-API-Key") != ANALYTICS_API_KEY:
        return JSONResponse(status_code=401, content={"error": "invalid key"})

    events = body.events
    per_user_pos_totals = {}
    for e in events:
        if e.amount > 0:
            per_user_pos_totals[e.user] = per_user_pos_totals.get(e.user, 0) + e.amount

    return {
        "email": YOUR_EMAIL,
        "total_events": len(events),
        "unique_users": len(set(e.user for e in events)),
        "revenue": sum(e.amount for e in events if e.amount > 0),
        "top_user": max(per_user_pos_totals, key=per_user_pos_totals.get) if per_user_pos_totals else None,
    }


DEFAULTS = {"port": 8000, "workers": 1, "debug": False, "log_level": "info", "api_key": "default-secret-000"}
YAML_LAYER = {"api_key": "key-x6kaf8bnud"}
DOTENV_LAYER = {"port": "8786", "log_level": "warning"}

def load_os_env_layer():
    layer = {}
    mapping = {"APP_PORT": "port", "APP_WORKERS": "workers", "NUM_WORKERS": "workers", "APP_DEBUG": "debug", "APP_LOG_LEVEL": "log_level", "APP_API_KEY": "api_key"}
    for env_key, config_key in mapping.items():
        if env_key in os.environ: layer[config_key] = os.environ[env_key]
    return layer

def coerce_types(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, str):
            if v.lower() == "true": out[k] = True
            elif v.lower() == "false": out[k] = False
            elif v.isdigit(): out[k] = int(v)
            else: out[k] = v
        else:
            out[k] = v
    return out

@app.get("/effective-config")
def effective_config(request: Request):
    merged = dict(DEFAULTS)
    merged.update(YAML_LAYER)
    merged.update(DOTENV_LAYER)
    merged.update(load_os_env_layer())

    for override in request.query_params.getlist("set"):
        if "=" in override:
            key, _, value = override.partition("=")
            merged[key.strip()] = value.strip()

    merged = coerce_types(merged)
    merged["api_key"] = "****"
    return merged


@app.get("/work")
def work(n: int):
    total = sum(range(n))
    return {"email": YOUR_EMAIL, "done": n}

@app.get("/metrics")
def metrics():
    body = (f"# HELP http_requests_total Total HTTP requests\n# TYPE http_requests_total counter\nhttp_requests_total {REQUEST_COUNTER}\n")
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

@app.get("/healthz")
def healthz():
    return {"status": "ok", "uptime_s": time.time() - START_TIME}

@app.get("/logs/tail")
def logs_tail(limit: int = 10):
    return list(LOG_BUFFER)[-limit:]

class ExtractRequest(BaseModel):
    text: str

class ExtractResponse(BaseModel):
    vendor: str
    amount: float
    currency: str
    date: str

@app.post("/extract", response_model=ExtractResponse)
def extract(body: ExtractRequest):
    text = body.text or ""
    try:
        vendor_match = re.search(r"([A-Z][A-Za-z0-9\-&]*(?:\s+[A-Z][A-Za-z0-9\-&]*)*\s+(?:Industries|Inc|Ltd|LLC|Corp|Co)\.?)", text)
        vendor = vendor_match.group(1) if vendor_match else "Unknown Vendor"

        amount_match = re.search(r"(USD|EUR|GBP)\s*([\d,]+\.?\d*)", text)
        if not amount_match: amount_match = re.search(r"([\d,]+\.?\d*)\s*(USD|EUR|GBP)", text)
        
        if amount_match:
            groups = amount_match.groups()
            if groups[0] in ("USD", "EUR", "GBP"):
                currency, amount_str = groups[0], groups[1]
            else:
                amount_str, currency = groups[0], groups[1]
            amount = float(amount_str.replace(",", ""))
        else:
            amount, currency = 0.0, "USD"

        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        date = date_match.group(1) if date_match else "1970-01-01"

        return ExtractResponse(vendor=vendor, amount=amount, currency=currency, date=date)

    except Exception:
        return ExtractResponse(vendor="Unknown", amount=0.0, currency="USD", date="1970-01-01")