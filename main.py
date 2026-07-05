import os
import re
import time
import uuid
import jwt
from jwt import InvalidTokenError, InvalidKeyError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
import os
import glob
import time
import uuid
from collections import defaultdict, deque

try:
    import yaml
except ImportError:
    yaml = None

app = FastAPI()

ALLOWED_ORIGIN = "https://dash-7et21z.example.com"
YOUR_EMAIL = "23f1001347@ds.study.iitm.ac.in"
ANALYTICS_API_KEY = "ak_vzqzkhznhc7e3wztts7scwhb"


# ===========================================================
# CUSTOM CORS + REQUEST-ID/TIMING MIDDLEWARE (combined)
# ===========================================================
# We're no longer using FastAPI's built-in CORSMiddleware because
# it applies ONE policy to the whole app. We need TWO policies:
#   - /stats and /verify -> only ALLOWED_ORIGIN gets the header
#   - /analytics          -> everyone gets "*"
# So we handle CORS by hand, based on request.url.path.


@app.middleware("http")
async def unified_middleware(request: Request, call_next):
    origin = request.headers.get("origin")
    path = request.url.path

    OPEN_CORS_PATHS = ("/effective-config", "/analytics", "/orders")

    # ============================================================
    # Handle OPTIONS preflight requests first, before anything else
    # ============================================================
    if request.method == "OPTIONS":
        headers = {}

        if path == "/ping":
            if origin in (ALLOWED_ORIGIN_10, EXAM_PAGE_ORIGIN):
                headers["Access-Control-Allow-Origin"] = origin
                headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
                headers["Access-Control-Allow-Headers"] = "*"

        elif path in OPEN_CORS_PATHS:
            headers["Access-Control-Allow-Origin"] = "*"
            headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            headers["Access-Control-Allow-Headers"] = "*"

        else:
            # Strict policy for /stats, /verify, etc.
            if origin == ALLOWED_ORIGIN:
                headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
                headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
                headers["Access-Control-Allow-Headers"] = "*"
            # No match -> no CORS headers at all

        return JSONResponse(status_code=200, content={}, headers=headers)

    # ============================================================
    # /ping-specific: rate limiting (only applies to this path)
    # ============================================================
    if path == "/ping":
        client_id = request.headers.get("X-Client-Id", "anonymous")
        now = time.time()
        bucket = ping_client_buckets[client_id]
        ping_client_buckets[client_id] = [t for t in bucket if now - t < WINDOW_SECONDS_10]

        if len(ping_client_buckets[client_id]) >= BUCKET_SIZE:
            return JSONResponse(status_code=429, content={"error": "rate limited"})
        ping_client_buckets[client_id].append(now)

        # Stash request_id for the route handler to use
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

    # ============================================================
    # Run the actual route
    # ============================================================
    start_time = time.time()
    generic_request_id = str(uuid.uuid4())

    response = await call_next(request)

    process_time = time.time() - start_time

    # ============================================================
    # Attach response headers based on path
    # ============================================================
    if path == "/ping":
        response.headers["X-Request-ID"] = request.state.request_id
        if origin in (ALLOWED_ORIGIN_10, EXAM_PAGE_ORIGIN):
            response.headers["Access-Control-Allow-Origin"] = origin

    else:
        response.headers["X-Request-ID"] = generic_request_id
        response.headers["X-Process-Time"] = f"{process_time:.6f}"

        if path in OPEN_CORS_PATHS:
            response.headers["Access-Control-Allow-Origin"] = "*"
        elif origin == ALLOWED_ORIGIN:
            response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN

    return response
@app.get("/")
async def root():
    return {"message": "Hello World"}


# ===========================================================
# /stats endpoint (unchanged)
# ===========================================================
@app.get("/stats")
def get_stats(values: str):
    try:
        numbers = [int(v.strip()) for v in values.split(",") if v.strip() != ""]
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": "values must be a comma-separated list of integers"},
        )
    if not numbers:
        return JSONResponse(status_code=400, content={"error": "no values provided"})

    count = len(numbers)
    total = sum(numbers)
    minimum = min(numbers)
    maximum = max(numbers)
    mean = total / count

    return {
        "email": YOUR_EMAIL,
        "count": count,
        "sum": total,
        "min": minimum,
        "max": maximum,
        "mean": mean,
    }


# ===========================================================
# /verify endpoint (unchanged)
# ===========================================================
EXPECTED_ISSUER = "https://idp.exam.local"
EXPECTED_AUDIENCE = "tds-huopxh4n.apps.exam.local"

_raw_key = os.environ.get(
    "IDP_PUBLIC_KEY",
    "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2okOHspNjgA+2rTLbeuY\ncxiP/hG8C6Sb9iwg3yiLAA4HCnpITcbWCSelbvbYGuc3EbNy4xFyf5Cbj5DHJMID\nEkryOgyd2giIIIBOUBj8S63uGcnRpOBh9NFatfNwheKuzsPuVNldu6A9cNteNpXc\nWyJjG2axVfmq7i6SuKr1JoWYG7xTTAvKPujSl4OtsQfO3h5NepzdfXpr28oNnzfW\ned+zclR6BcmNNo/WVfJ4xyCLSf0BCOgdTgW6PdaChd1l9VDetJZVEgC5tkyvXsfI\nSI6iyrYbKR0NEBSqq4XkadEjsCs4F1RncsS4LlgniT7GlkL9Mce3b0wGLs9/7ZIX\ndQIDAQAB\n-----END PUBLIC KEY-----",
)
IDP_PUBLIC_KEY = _raw_key.replace("\\n", "\n").strip()

if not IDP_PUBLIC_KEY.startswith("-----BEGIN PUBLIC KEY-----"):
    print(
        "WARNING: IDP_PUBLIC_KEY is missing or malformed! "
        f"First 40 chars seen: {IDP_PUBLIC_KEY[:40]!r}"
    )


class VerifyRequest(BaseModel):
    token: str


@app.post("/verify")
def verify_token(body: VerifyRequest):
    try:
        claims = jwt.decode(
            body.token,
            IDP_PUBLIC_KEY,
            algorithms=["RS256"],
            issuer=EXPECTED_ISSUER,
            audience=EXPECTED_AUDIENCE,
        )
        return {
            "valid": True,
            "email": claims.get("email"),
            "sub": claims.get("sub"),
            "aud": claims.get("aud"),
        }
    except (InvalidTokenError, InvalidKeyError):
        return JSONResponse(status_code=401, content={"valid": False})
    except Exception:
        return JSONResponse(status_code=401, content={"valid": False})


# ===========================================================
# NEW: /analytics endpoint
# ===========================================================
class AnalyticsEvent(BaseModel):
    user: str
    amount: float
    ts: int


class AnalyticsRequest(BaseModel):
    events: list[AnalyticsEvent]


@app.post("/analytics")
def analytics(body: AnalyticsRequest, request: Request):
    # 1. Check the API key first, before doing anything else.
    provided_key = request.headers.get("X-API-Key")
    if provided_key != ANALYTICS_API_KEY:
        return JSONResponse(
            status_code=401, content={"error": "invalid or missing API key"}
        )

    events = body.events

    # 2. total_events = just count everything in the list
    total_events = len(events)

    # 3. unique_users = how many distinct "user" names appear
    unique_users = len(set(e.user for e in events))

    # 4. revenue = sum of amounts, but ONLY positive ones
    revenue = sum(e.amount for e in events if e.amount > 0)

    # 5. top_user = whoever has the highest POSITIVE total
    #    We build a running total per user, but only add positive amounts.
    per_user_positive_totals: dict[str, float] = {}
    for e in events:
        if e.amount > 0:
            per_user_positive_totals[e.user] = (
                per_user_positive_totals.get(e.user, 0) + e.amount
            )

    top_user = None
    if per_user_positive_totals:
        # max(..., key=...) finds the dict key with the highest value
        top_user = max(per_user_positive_totals, key=per_user_positive_totals.get)

    return {
        "email": YOUR_EMAIL,
        "total_events": total_events,
        "unique_users": unique_users,
        "revenue": revenue,
        "top_user": top_user,
    }


# ===========================================================
# /effective-config endpoint
# ===========================================================

DEFAULTS = {
    "port": 8000,
    "workers": 1,
    "debug": False,
    "log_level": "info",
    "api_key": "default-secret-000",
}


def load_yaml_layer():
    env_name = os.environ.get("APP_ENV", "development")
    path = f"config.{env_name}.yaml"
    if yaml and os.path.exists(path):
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data
    return {}


def load_dotenv_layer():
    """Parses a .env file manually (KEY=VALUE per line)."""
    layer = {}
    if os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()

                # Map .env keys (APP_PORT, NUM_WORKERS, etc.) to our config keys
                if key == "APP_PORT":
                    layer["port"] = value
                elif key in ("APP_WORKERS", "NUM_WORKERS"):
                    layer["workers"] = value
                elif key == "APP_DEBUG":
                    layer["debug"] = value
                elif key == "APP_LOG_LEVEL":
                    layer["log_level"] = value
                elif key == "APP_API_KEY":
                    layer["api_key"] = value
    return layer


def load_os_env_layer():
    """Reads real OS environment variables with APP_ prefix."""
    layer = {}
    mapping = {
        "APP_PORT": "port",
        "APP_WORKERS": "workers",
        "NUM_WORKERS": "workers",
        "APP_DEBUG": "debug",
        "APP_LOG_LEVEL": "log_level",
        "APP_API_KEY": "api_key",
    }
    for env_key, config_key in mapping.items():
        if env_key in os.environ:
            layer[config_key] = os.environ[env_key]
    return layer


def coerce_types(config: dict) -> dict:
    result = dict(config)
    if "port" in result:
        result["port"] = int(result["port"])
    if "workers" in result:
        result["workers"] = int(result["workers"])
    if "debug" in result:
        val = result["debug"]
        if isinstance(val, bool):
            pass
        else:
            result["debug"] = str(val).strip().lower() in ("true", "1", "yes", "on")
    if "log_level" in result:
        result["log_level"] = str(result["log_level"])
    return result


@app.get("/effective-config")
def effective_config(request: Request):
    # Merge layers from lowest to highest precedence.
    # Each dict.update() call overwrites keys from earlier layers.
    merged = dict(DEFAULTS)
    merged.update(load_yaml_layer())
    merged.update(load_dotenv_layer())
    merged.update(load_os_env_layer())

    # Apply ?set=key=value overrides (highest precedence).
    # request.query_params.getlist("set") gets ALL "set" params,
    # since multiple ?set=... can appear in the same URL.
    for override in request.query_params.getlist("set"):
        if "=" in override:
            key, _, value = override.partition("=")
            merged[key.strip()] = value.strip()

    merged = coerce_types(merged)

    # Always mask the secret before returning it.
    merged["api_key"] = "****"

    return merged


START_TIME = time.time()
REQUEST_COUNTER = 0
LOG_BUFFER = deque(maxlen=1000)  # keeps only the most recent 1000 entries


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    global REQUEST_COUNTER
    REQUEST_COUNTER += 1  # counts EVERY request, to any endpoint

    request_id = str(uuid.uuid4())
    response = await call_next(request)

    LOG_BUFFER.append(
        {
            "level": "info",
            "ts": time.time(),
            "path": request.url.path,
            "request_id": request_id,
        }
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/work")
def work(n: int):
    # Simulate K units of work — cheap CPU loop
    total = 0
    for i in range(n):
        total += i
    return {"email": YOUR_EMAIL, "done": n}


@app.get("/metrics")
def metrics():
    # Prometheus text exposition format
    body = (
        "# HELP http_requests_total Total HTTP requests\n"
        "# TYPE http_requests_total counter\n"
        f"http_requests_total {REQUEST_COUNTER}\n"
    )
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "uptime_s": time.time() - START_TIME}


@app.get("/logs/tail")
def logs_tail(limit: int = 10):
    # Return the last `limit` entries, most recent last (or first — either is fine)
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
        # Vendor: look for a capitalized phrase ending in a common company suffix
        vendor_match = re.search(
            r"([A-Z][A-Za-z0-9\-&]*(?:\s+[A-Z][A-Za-z0-9\-&]*)*\s+"
            r"(?:Industries|Inc|Ltd|LLC|Corp|Co)\.?)",
            text,
        )
        vendor = vendor_match.group(1) if vendor_match else "Unknown Vendor"

        # Amount + currency: look for patterns like "USD 1234.56" or "$1234.56"
        amount_match = re.search(r"(USD|EUR|GBP)\s*([\d,]+\.?\d*)", text)
        if not amount_match:
            amount_match = re.search(r"([\d,]+\.?\d*)\s*(USD|EUR|GBP)", text)
        if amount_match:
            groups = amount_match.groups()
            if groups[0] in ("USD", "EUR", "GBP"):
                currency, amount_str = groups[0], groups[1]
            else:
                amount_str, currency = groups[0], groups[1]
            amount = float(amount_str.replace(",", ""))
        else:
            amount, currency = 0.0, "USD"

        # Date: look for YYYY-MM-DD
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        date = date_match.group(1) if date_match else "1970-01-01"

        return ExtractResponse(
            vendor=vendor, amount=amount, currency=currency, date=date
        )

    except Exception:
        # Never crash — return best-effort defaults instead of a 500
        return ExtractResponse(
            vendor="Unknown", amount=0.0, currency="USD", date="1970-01-01"
        )


ALLOWED_ORIGIN_10 = "https://app-kuinjq.example.com"
EXAM_PAGE_ORIGIN = (
    "https://exam.example.com"  # replace with the actual exam page origin
)
BUCKET_SIZE = 9
WINDOW_SECONDS_10 = 10

ping_client_buckets: dict[str, list[float]] = defaultdict(list)


@app.get("/ping")
def ping(request: Request):
    request_id = request.headers.get("X-Request-ID") or "unset"
    return {"email": YOUR_EMAIL, "request_id": request_id}
