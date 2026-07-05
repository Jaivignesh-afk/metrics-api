import time
import uuid
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI()

ALLOWED_ORIGIN = "https://dash-7et21z.example.com"
YOUR_EMAIL = "23f1001347@ds.study.iitm.ac.in"  # <-- put your real logged-in email here

# --- CORS setup ---
# This tells FastAPI: only allow the one specific origin above.
# CORSMiddleware automatically handles OPTIONS preflight requests correctly:
# it adds the ACAO header only when the request's Origin matches allow_origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["GET", "OPTIONS"],
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

# --- The actual endpoint ---
@app.get("/stats")
def get_stats(values: str):
    # values comes in as a string like "1,2,3,4"
    try:
        numbers = [int(v.strip()) for v in values.split(",") if v.strip() != ""]
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": "values must be a comma-separated list of integers"},
        )

    if not numbers:
        return JSONResponse(
            status_code=400,
            content={"error": "no values provided"},
        )

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