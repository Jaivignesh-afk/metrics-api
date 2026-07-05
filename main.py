import os
import time
import uuid
import jwt
from jwt import InvalidTokenError
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN")
YOUR_EMAIL = os.environ.get("YOUR_EMAIL")

# --- CORS setup ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# --- Middleware for X-Request-ID and X-Process-Time ---
@app.middleware("http")
async def add_custom_headers(request: Request, call_next):
    start_time = time.time()
    request_id = str(uuid.uuid4())

    response = await call_next(request)

    process_time = time.time() - start_time
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = f"{process_time:.6f}"
    return response


@app.get("/")
async def root():
    return {"message": "Hello World"}


# --- /stats endpoint ---
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
# /verify endpoint
# ===========================================================

EXPECTED_ISSUER = "https://idp.exam.local"
EXPECTED_AUDIENCE = "tds-huopxh4n.apps.exam.local"

# Read the public key from the environment variable at startup.
# .replace("\\n", "\n") is a safety net: if your hosting platform
# stores the key as one line with literal "\n" text instead of
# real line breaks, this converts it back into real line breaks.
# If your platform preserves real line breaks already, this line
# does nothing harmful (it just won't find anything to replace).
_raw_key = os.environ.get("IDP_PUBLIC_KEY", "")
IDP_PUBLIC_KEY = _raw_key.replace("\\n", "\n")

if not IDP_PUBLIC_KEY:
    # This will show up loudly in your deploy logs if the env var
    # wasn't set, instead of silently failing every /verify call.
    print("WARNING: IDP_PUBLIC_KEY environment variable is not set!")


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

    except InvalidTokenError:
        return JSONResponse(status_code=401, content={"valid": False})