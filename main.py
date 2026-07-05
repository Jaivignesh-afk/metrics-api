import os
import time
import uuid
import jwt
from jwt import InvalidTokenError, InvalidKeyError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
async def cors_and_headers_middleware(request: Request, call_next):
    origin = request.headers.get("origin")
    path = request.url.path

    # --- Handle CORS preflight (OPTIONS) requests ourselves ---
    if request.method == "OPTIONS":
        headers = {}
        if path == "/analytics":
            # Open policy: allow any origin
            headers["Access-Control-Allow-Origin"] = "*"
            headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            headers["Access-Control-Allow-Headers"] = "*"
        else:
            # Strict policy: only the assigned origin gets a header
            if origin == ALLOWED_ORIGIN:
                headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
                headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
                headers["Access-Control-Allow-Headers"] = "*"
            # If origin doesn't match, we simply add NO CORS headers.
        return JSONResponse(status_code=200, content={}, headers=headers)

    # --- Handle the actual request/response ---
    start_time = time.time()
    request_id = str(uuid.uuid4())

    response = await call_next(request)

    process_time = time.time() - start_time
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = f"{process_time:.6f}"

    # Add the correct CORS header to the real response too
    if path == "/analytics":
        response.headers["Access-Control-Allow-Origin"] = "*"
    else:
        if origin == ALLOWED_ORIGIN:
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
        return JSONResponse(status_code=400, content={"error": "values must be a comma-separated list of integers"})
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

_raw_key = os.environ.get("IDP_PUBLIC_KEY", "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2okOHspNjgA+2rTLbeuY\ncxiP/hG8C6Sb9iwg3yiLAA4HCnpITcbWCSelbvbYGuc3EbNy4xFyf5Cbj5DHJMID\nEkryOgyd2giIIIBOUBj8S63uGcnRpOBh9NFatfNwheKuzsPuVNldu6A9cNteNpXc\nWyJjG2axVfmq7i6SuKr1JoWYG7xTTAvKPujSl4OtsQfO3h5NepzdfXpr28oNnzfW\ned+zclR6BcmNNo/WVfJ4xyCLSf0BCOgdTgW6PdaChd1l9VDetJZVEgC5tkyvXsfI\nSI6iyrYbKR0NEBSqq4XkadEjsCs4F1RncsS4LlgniT7GlkL9Mce3b0wGLs9/7ZIX\ndQIDAQAB\n-----END PUBLIC KEY-----")
IDP_PUBLIC_KEY = _raw_key.replace("\\n", "\n").strip()

if not IDP_PUBLIC_KEY.startswith("-----BEGIN PUBLIC KEY-----"):
    print("WARNING: IDP_PUBLIC_KEY is missing or malformed! "
          f"First 40 chars seen: {IDP_PUBLIC_KEY[:40]!r}")


class VerifyRequest(BaseModel):
    token: str


@app.post("/verify")
@app.post("/")
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
        return JSONResponse(status_code=401, content={"error": "invalid or missing API key"})

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
            per_user_positive_totals[e.user] = per_user_positive_totals.get(e.user, 0) + e.amount

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