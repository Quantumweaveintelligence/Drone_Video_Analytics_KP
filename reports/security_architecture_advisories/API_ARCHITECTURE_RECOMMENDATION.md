# Architectural Decision Record (ADR): API Modularization vs. Monolithic Server

**Date & Time:** 2026-08-14 04:20:00 PM IST (+05:30)  
**Topic:** Monolithic `server.py` (81+ Endpoints) vs. Modular `APIRouter` Architecture  
**Status:** Approved / Recommended  

---

## Executive Summary & Verdict

**Verdict: Modularize into Domain-Specific Router Files immediately.**

Keeping 81+ endpoints inside a single `server.py` file is an anti-pattern that increases technical debt, risk of security misconfigurations, and team merge conflicts. Splitting routes into modular files using FastAPI's `APIRouter` pattern is the industry standard for scalable microservices and web APIs.

---

## Comparative Analysis

### 1. Performance & Resource Optimization

| Dimension | Monolithic (`server.py`) | Modular Files (`APIRouter`) |
| :--- | :--- | :--- |
| **HTTP Request Latency** | Identical | Identical (FastAPI route tree lookup overhead is $O(1) / O(k)$ in both). |
| **Import & Startup Time** | Slower; loads heavy analytics/AI packages globally even if not needed. | Enables clean dependency isolation and optional lazy imports. |
| **Memory Segmentation** | Monolithic global state; risk of high memory retention across tasks. | Encapsulated service instances, dedicated connection pools per domain. |
| **Concurrency / Threading** | High risk of thread contention & global state race conditions. | Scoped state and clear boundaries between streaming vs. CPU-heavy tasks. |

> **Key Takeaway:** While raw routing execution speed is identical, a modular layout allows domain-scoped connection pooling, dedicated worker queues, and fine-grained caching strategies.

---

### 2. Security & Access Control

1. **Router-Level Security (Role-Based Access Control - RBAC):**
   - **Monolithic risk:** Applying authentication to 81+ endpoints individually creates high risk of human error (e.g., forgetting auth on 1 endpoint).
   - **Modular solution:** Security dependencies can be applied at the **router level**, automatically securing all endpoints within that group.
     ```python
     analytics_router = APIRouter(
         prefix="/analytics",
         dependencies=[Depends(verify_admin_or_analyst)]
     )
     ```
2. **Reduced Blast Radius & Auditability:**
   - Security auditing, penetration testing, and permission tier reviews (Public vs. Edge Node vs. Operator vs. Super Admin) are significantly easier when isolated into dedicated files.
3. **Targeted Rate Limiting & DoS Protection:**
   - Heavy compute/streaming routes (e.g., video streaming or complex analytics queries) can have strict rate limits applied without throttling lightweight health/telemetry endpoints.

---

### 3. Engineering Productivity & Maintainability

| Criteria | Monolithic `server.py` | Modular Architecture |
| :--- | :--- | :--- |
| **File Length** | 2,000–5,000+ lines (hard to read/navigate) | 100–300 lines per domain file |
| **Git Conflicts** | Constant merge conflicts when multiple contributors edit a monolithic file | Minimal conflicts; each capability is modularized |
| **Unit / Integration Testing** | Complex mocking across global dependencies | Easy unit testing per router |
| **Swagger / OpenAPI Documentation** | Cluttered, disorganized route list | Cleanly organized by tags, versions, and categories in `/docs` |

---

## Recommended Project Structure

```text
dvas_edge/ (or root server package)
├── server.py                 # Main entrypoint: App config, middleware & router inclusion
├── config/                   # Environment & runtime configurations
├── routers/                  # Modular endpoint definitions
│   ├── __init__.py
│   ├── auth.py               # Authentication & session endpoints
│   ├── edge_control.py       # Mission control & edge lifecycle
│   ├── analytics.py          # Video analytics, aggregations & reporting
│   ├── streaming.py          # Video feed streams (MJPEG/WebRTC)
│   └── telemetry.py          # Device telemetry, sensors & system health
├── services/                 # Core business logic & external integrations
├── schemas/                  # Pydantic request & response schemas
└── dependencies/             # Reusable security & database dependencies
```

---

## Implementation Example

### `server.py` (Clean Orchestrator)
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import auth, edge_control, analytics, streaming, telemetry

app = FastAPI(
    title="DVAS Edge & Analytics API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Global Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register Modular Routers
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(edge_control.router, prefix="/api/v1/edge", tags=["Edge Control"])
app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["Analytics"])
app.include_router(streaming.router, prefix="/api/v1/stream", tags=["Video Streaming"])
app.include_router(telemetry.router, prefix="/api/v1/telemetry", tags=["Telemetry"])
```

### `routers/analytics.py` (Domain-Specific Router)
```python
from fastapi import APIRouter, Depends, HTTPException, status
from dependencies.auth import verify_admin_token
from schemas.analytics import AnalyticsReportRequest, AnalyticsReportResponse

router = APIRouter(
    dependencies=[Depends(verify_admin_token)]
)

@router.post("/reports", response_model=AnalyticsReportResponse)
async def generate_report(payload: AnalyticsReportRequest):
    # Domain specific logic
    return {"status": "success", "data": {}}
```

---

## Action Plan for the Team

1. **Step 1:** Create the `routers/`, `schemas/`, and `services/` directory structure.
2. **Step 2:** Group existing 81 endpoints into logical categories (e.g. 5–6 core domains).
3. **Step 3:** Migrate one domain at a time (e.g. Telemetry -> Streaming -> Analytics -> Edge Control).
4. **Step 4:** Move shared dependencies (Auth, DB connectors) into `dependencies/`.
5. **Step 5:** Keep `server.py` as a lightweight registration hub (< 100 lines).
