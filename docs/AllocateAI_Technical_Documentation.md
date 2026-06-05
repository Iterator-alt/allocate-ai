# Allocate.AI - Complete Technical Documentation

**Version:** 1.0
**Date:** May 2026
**Document Type:** Internal Technical Reference

---

## Table of Contents

1. [Executive Overview](#1-executive-overview)
2. [System Architecture](#2-system-architecture)
3. [API Endpoints Reference](#3-api-endpoints-reference)
4. [Complete Workflow - Step by Step](#4-complete-workflow---step-by-step)
5. [Stage 1: Competitor Matching](#5-stage-1-competitor-matching)
6. [Stage 2: Prompt Assembly](#6-stage-2-prompt-assembly)
7. [Stage 3: Output Parsing](#7-stage-3-output-parsing)
8. [Stage 4: Feedback Generation](#8-stage-4-feedback-generation)
9. [Database Schema](#9-database-schema)
10. [AI Prompts & Logic](#10-ai-prompts--logic)
11. [Data Flow Diagrams](#11-data-flow-diagrams)
12. [Error Handling & Guards](#12-error-handling--guards)
13. [Configuration & Deployment](#13-configuration--deployment)

---

## 1. Executive Overview

### What is Allocate.AI?

Allocate.AI is an AI-powered media budget allocation backend that recommends optimal advertising channel splits (TV, Digital, Radio, OOH, etc.) for brands. It uses:

- **YouGov data**: Brand KPI metrics (adaware, aware, consider)
- **Nielsen data**: Advertising spend by channel and brand
- **OpenAI GPT-4o**: AI reasoning for allocation recommendations

### Key Success Metric

Budget allocation recommendations achieve **<20% deviation** from benchmark models.

### Technology Stack

| Component | Technology |
|-----------|------------|
| Framework | FastAPI (Python 3.11) |
| Database | PostgreSQL + SQLAlchemy 2.0 (async) |
| LLM | OpenAI GPT-4o (JSON mode) |
| Background Tasks | FastAPI BackgroundTasks |
| Rate Limiting | SlowAPI |

---

## 2. System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           JS BACKEND (Consumer)                          │
│                    Sends requests with X-Session-Token                   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         ALLOCATE.AI API (FastAPI)                        │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐                │
│  │   /runs       │  │ /competitors  │  │   /result     │                │
│  │   /status     │  │   /confirm    │  │   /chat       │                │
│  │   /stop       │  │               │  │   /trace      │                │
│  └───────────────┘  └───────────────┘  └───────────────┘                │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                        SERVICES LAYER                            │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │    │
│  │  │   Stage 1   │  │   Stage 2   │  │   Stage 3   │              │    │
│  │  │  Competitor │  │   Prompt    │  │   Output    │              │    │
│  │  │  Matching   │  │  Assembly   │  │   Parsing   │              │    │
│  │  └─────────────┘  └─────────────┘  └─────────────┘              │    │
│  │  ┌─────────────┐  ┌─────────────┐                               │    │
│  │  │   Stage 4   │  │ LLM Gateway │                               │    │
│  │  │  Feedback   │  │  (OpenAI)   │                               │    │
│  │  │ Generation  │  │             │                               │    │
│  │  └─────────────┘  └─────────────┘                               │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                      REPOSITORY LAYER                            │    │
│  │  RunRepo │ YouGovRepo │ NielsenRepo │ ResultRepo │ TraceRepo    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           POSTGRESQL DATABASE                            │
│   runs │ yougov │ nielsen │ allocation_results │ chat_history │ traces  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Project Structure

```
allocate-ai/
├── src/
│   ├── main.py                     # FastAPI entry point
│   ├── config.py                   # Settings (pydantic-settings)
│   ├── dependencies.py             # Dependency injection
│   │
│   ├── api/v1/                     # API endpoints
│   │   ├── runs.py                 # POST /runs, GET /status, POST /stop
│   │   ├── competitors.py          # GET/POST competitors
│   │   ├── results.py              # GET /result, GET /chat
│   │   ├── traces.py               # GET /trace (Owner only)
│   │   └── stage1.py               # Direct Stage 1 access (debug)
│   │
│   ├── api/schemas/                # Pydantic request/response models
│   ├── api/middleware/             # Session validation, rate limiting
│   │
│   ├── db/
│   │   ├── session.py              # Async session factory
│   │   └── models/                 # SQLAlchemy models
│   │
│   ├── repositories/               # Data access layer
│   │
│   └── services/
│       ├── stage1/                 # Competitor matching pipeline
│       ├── mediamix/               # Stages 2-4 (prompt, parse, feedback)
│       └── llm_gateway/            # OpenAI client with retry/circuit breaker
│
├── alembic/                        # Database migrations
└── docs/                           # Documentation
```

---

## 3. API Endpoints Reference

### Base URL
```
http://127.0.0.1:8081/api/v1
```

### Authentication
All endpoints require `X-Session-Token` header.

### Endpoints Summary

| Method | Endpoint | Description | Stage |
|--------|----------|-------------|-------|
| POST | /runs | Create new run | - |
| GET | /runs/{id}/status | Poll run progress | - |
| POST | /runs/{id}/stop | Cancel run | - |
| GET | /runs/{id}/competitors | Get matched competitors | S1.5 |
| POST | /runs/{id}/competitors/confirm | Approve/cancel competitors | S1.5 |
| GET | /runs/{id}/result | Get allocation results | After S4 |
| GET | /runs/{id}/chat | Get feedback cards | After S4 |
| GET | /runs/{id}/trace | Get prompt traces (Owner) | Debug |

### Detailed Endpoint Documentation

#### POST /runs - Create New Run

**Purpose:** Creates a new budget allocation run and auto-triggers Stage 1 (competitor matching) in background.

**Request:**
```json
{
    "customer_name": "Knorr",        // Required: Brand name
    "industry": "Food",              // Required: Industry category
    "brand_kpi": "adaware",          // Required: adaware | aware | consider
    "total_budget": 500000,          // Optional: Budget in EUR
    "time_period_start": "2025-01-01T00:00:00Z",  // Optional
    "time_period_end": "2025-12-31T23:59:59Z",    // Optional
    "channels": ["TV", "Digital"],   // Optional: Specific channels
    "goal_text": "Increase awareness",  // Optional: User goal
    "direction": "increase"          // Optional: increase | maintain | decrease
}
```

**Response (201 Created):**
```json
{
    "id": 15,
    "session_token": "test-session-123",
    "customer_name": "Knorr",
    "industry": "Food",
    "brand_kpi": "adaware",
    "status": "pending",
    "created_at": "2026-05-21T13:42:44.194520Z"
}
```

**What Happens:**
1. Run record created in database with status=PENDING
2. Background task `run_stage1_background()` triggered immediately
3. Response returned without waiting for Stage 1

---

#### GET /runs/{id}/status - Poll Status

**Purpose:** Check current run status and progress.

**Response:**
```json
{
    "id": 15,
    "status": "awaiting_confirmation",
    "stage": "S1.5",
    "progress_pct": 30,
    "progress": "Waiting for competitor confirmation (Stage 1.5)",
    "started_at": "2026-05-21T08:12:44.394014Z",
    "completed_at": null,
    "error_message": null
}
```

**Status Progression:**

| Status | Stage | progress_pct | Meaning |
|--------|-------|--------------|---------|
| pending | null | 0 | Queued for processing |
| matching | S1 | 20 | Stage 1 running |
| awaiting_confirmation | S1.5 | 30 | Waiting for user to confirm competitors |
| generating | S2 | 50 | AI generation in progress |
| parsing | S3 | 75 | Parsing LLM response |
| feedback | S4 | 90 | Generating feedback cards |
| completed | null | 100 | Done |
| failed | null | 0 | Error occurred |
| cancelled | null | 0 | User cancelled |

---

#### GET /runs/{id}/competitors - Get Matched Competitors

**Purpose:** Retrieve competitors found by Stage 1 for user review.

**Precondition:** Status must be `awaiting_confirmation`

**Response:**
```json
{
    "run_id": 15,
    "industry": "Food",
    "sector_label": "Food II",
    "competitors": [
        {
            "nielsen_brand": "Maggi",
            "yougov_brand_label": "Maggi",
            "wirtschaftsgruppe": "Food",
            "has_nielsen_data": true,
            "has_yougov_data": true,
            "total_spend_eur": 45000000,
            "match_confidence": 1.0
        },
        // ... more competitors
    ],
    "total_competitors": 10,
    "competitors_with_full_data": 8,
    "warnings": [],
    "brand_info": {
        "brand_label": "Knorr",
        "nielsen_brand": "KNORR",
        "match_type": "exact",
        "confidence": 0.95,
        "kpi_scores": {
            "adaware": 9.99,
            "aware": null,
            "consider": 38.27
        },
        "total_spend_teuro": 62854.77
    }
}
```

---

#### POST /runs/{id}/competitors/confirm - Confirm Competitors

**Purpose:** Approve or cancel the competitor set. If approved, auto-triggers AI generation (Stage 2-4).

**Request (Approve All):**
```json
{
    "action": "approve"
}
```

**Request (Approve Selected):**
```json
{
    "action": "approve",
    "selected_competitors": ["Maggi", "Heinz", "Nestlé"]
}
```

**Request (Cancel):**
```json
{
    "action": "cancel",
    "reason": "Not enough competitors"
}
```

**Response (Approve):**
```json
{
    "run_id": 15,
    "status": "approved",
    "confirmed_competitors": ["Maggi", "Heinz", "Nestlé", ...],
    "message": "Confirmed 10 competitors. AI generation started automatically. Poll /status for progress."
}
```

**What Happens on Approve:**
1. Updates `run.confirmed_competitors` with final selection
2. Changes status to `generating`
3. Triggers `run_ai_generation_background()` immediately
4. Stages 2-4 execute in sequence

---

#### GET /runs/{id}/result - Get Allocation Results

**Purpose:** Retrieve final budget allocation recommendation.

**Precondition:** Status must be `completed`

**Response:**
```json
{
    "run_id": 15,
    "allocations": [
        {
            "channel": "TV",
            "share_pct": "40.0",
            "budget_gross_eur": "400000.0",
            "reasoning": "TV offers broad reach for ad awareness"
        },
        {
            "channel": "Digital",
            "share_pct": "30.0",
            "budget_gross_eur": "300000.0",
            "reasoning": "Digital enables precise targeting"
        },
        {
            "channel": "Out-of-home",
            "share_pct": "20.0",
            "budget_gross_eur": "200000.0",
            "reasoning": "OOH reinforces brand visibility"
        },
        {
            "channel": "Radio",
            "share_pct": "10.0",
            "budget_gross_eur": "100000.0",
            "reasoning": "Radio adds local reach"
        }
    ],
    "reasoning_summary": "Strategy prioritizes TV and OOH for awareness...",
    "confidence_score": "0.90",
    "warnings": ["Unknown channel 'Out-of-home'"],
    "is_cached": false,
    "created_at": "2026-05-21T13:43:21.972194Z"
}
```

---

#### GET /runs/{id}/chat - Get Feedback Cards

**Purpose:** Retrieve feedback messages (warnings, recommendations, summary).

**Response:**
```json
{
    "run_id": 15,
    "messages": [
        {
            "id": 29,
            "message_type": "warning",
            "severity": "warning",
            "title": "Validation Warning",
            "content": "Unknown channel 'Out-of-home'",
            "display_order": 0
        },
        {
            "id": 31,
            "message_type": "recommendation",
            "severity": "info",
            "title": "Consider Additional Channels for ADAWARE",
            "content": "To maximize adaware, you might also consider: Social Media, Video.",
            "display_order": 0
        },
        {
            "id": 32,
            "message_type": "summary",
            "severity": "info",
            "title": "Allocation Strategy Summary",
            "content": "The strategy prioritizes high-reach channels...",
            "display_order": 3
        }
    ],
    "total_messages": 4,
    "has_warnings": true,
    "has_alerts": false
}
```

---

#### GET /runs/{id}/trace - Get Prompt Traces (Owner Only)

**Purpose:** Debug endpoint showing full LLM prompts and responses.

**Access:** Requires `X-User-Role: owner` header

**Response:**
```json
{
    "run_id": 15,
    "traces": [
        {
            "id": 1,
            "called_at": "2026-05-21T13:43:10.123Z",
            "model": "gpt-4o",
            "prompt": "SYSTEM:\nYou are an expert media planner...\n\nUSER:\n## Client: Knorr...",
            "response": "{\"allocations\": [...], \"confidence\": 0.9}",
            "prompt_tokens": 2500,
            "completion_tokens": 800,
            "total_tokens": 3300,
            "latency_ms": 4200,
            "status": "success"
        }
    ],
    "total_traces": 1,
    "total_tokens": 3300,
    "total_latency_ms": 4200,
    "success_rate": 1.0
}
```

---

## 4. Complete Workflow - Step by Step

### Visual Flow

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         COMPLETE API WORKFLOW                             │
└──────────────────────────────────────────────────────────────────────────┘

  User/JS Backend                    Allocate.AI API
       │                                  │
       │  1. POST /runs                   │
       │  {customer: "Knorr", ...}        │
       │─────────────────────────────────▶│
       │                                  │  ┌─────────────────────────┐
       │  Response: {id: 15, status:      │  │ Creates Run record      │
       │            "pending"}            │  │ Triggers Stage 1 in     │
       │◀─────────────────────────────────│  │ background              │
       │                                  │  └─────────────────────────┘
       │                                  │
       │  2. GET /runs/15/status          │  ┌─────────────────────────┐
       │  (poll until awaiting_confirm)   │  │ STAGE 1 (Background)    │
       │─────────────────────────────────▶│  │ - AI Call #1: Industry  │
       │                                  │  │ - AI Call #2: Brand     │
       │  Response: {stage: "S1.5",       │  │ - Data fetch: YouGov    │
       │            progress_pct: 30}     │  │ - Data fetch: Nielsen   │
       │◀─────────────────────────────────│  │ - Find competitors      │
       │                                  │  └─────────────────────────┘
       │                                  │
       │  3. GET /runs/15/competitors     │
       │─────────────────────────────────▶│
       │                                  │
       │  Response: {competitors: [...],  │
       │            brand_info: {...}}    │
       │◀─────────────────────────────────│
       │                                  │
       │  [User reviews competitors]      │
       │                                  │
       │  4. POST /runs/15/competitors/   │
       │     confirm {action: "approve"}  │
       │─────────────────────────────────▶│
       │                                  │  ┌─────────────────────────┐
       │  Response: {status: "approved",  │  │ Triggers Stages 2-4     │
       │            message: "AI gen..."}│  │ in background           │
       │◀─────────────────────────────────│  └─────────────────────────┘
       │                                  │
       │  5. GET /runs/15/status          │  ┌─────────────────────────┐
       │  (poll until completed)          │  │ STAGE 2 (Background)    │
       │─────────────────────────────────▶│  │ - Assemble prompt       │
       │                                  │  │ - Call OpenAI GPT-4o    │
       │  Response: {stage: "S2",         │  └─────────────────────────┘
       │            progress_pct: 50}     │
       │◀─────────────────────────────────│  ┌─────────────────────────┐
       │                                  │  │ STAGE 3 (Background)    │
       │  [Continue polling...]           │  │ - Parse JSON response   │
       │                                  │  │ - Validate allocations  │
       │  Response: {status: "completed", │  │ - Store results         │
       │            progress_pct: 100}    │  └─────────────────────────┘
       │◀─────────────────────────────────│
       │                                  │  ┌─────────────────────────┐
       │                                  │  │ STAGE 4 (Background)    │
       │                                  │  │ - Generate warnings     │
       │                                  │  │ - Generate summary      │
       │                                  │  │ - Store chat messages   │
       │                                  │  └─────────────────────────┘
       │                                  │
       │  6. GET /runs/15/result          │
       │─────────────────────────────────▶│
       │                                  │
       │  Response: {allocations: [...],  │
       │            confidence: 0.9}      │
       │◀─────────────────────────────────│
       │                                  │
       │  7. GET /runs/15/chat            │
       │─────────────────────────────────▶│
       │                                  │
       │  Response: {messages: [...],     │
       │            has_warnings: true}   │
       │◀─────────────────────────────────│
       │                                  │
       ▼                                  ▼
```

---

## 5. Stage 1: Competitor Matching

### Purpose
Match user's brand to database records and find relevant competitors.

### Location
`src/services/stage1/`

### Components

| File | Class/Function | Purpose |
|------|----------------|---------|
| orchestrator.py | Stage1Orchestrator | Main pipeline coordinator |
| ai_resolution.py | AIResolutionService | AI-powered entity matching |
| ai_resolution.py | AIWithWebSearchService | Fallback with web search |
| repository.py | Stage1Repository | Database queries with caching |
| cache.py | stage1_cache | 24-hour TTL cache |

### Data Flow

```
UserCampaignInput
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│                    STAGE 1 ORCHESTRATOR                      │
│                                                              │
│  1. INDUSTRY RESOLUTION (AI Call #1)                        │
│     ┌──────────────────────────────────────────────────┐    │
│     │ Input: user industry (e.g., "Food")              │    │
│     │ Query: Get all DISTINCT sector_labels from YouGov│    │
│     │ Query: Get all DISTINCT wirtschaftsgruppe from   │    │
│     │        Nielsen                                    │    │
│     │ AI Prompt: "Map 'Food' to these categories..."   │    │
│     │ Output: yougov_sectors=["Food II"],              │    │
│     │         nielsen_sectors=["Food"]                 │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  2. BRAND RESOLUTION (AI Call #2)                           │
│     ┌──────────────────────────────────────────────────┐    │
│     │ Input: user brand (e.g., "Knorr")                │    │
│     │ Query: Get DISTINCT brand_labels in resolved     │    │
│     │        sectors from YouGov                       │    │
│     │ Query: Get DISTINCT marke in resolved            │    │
│     │        wirtschaftsgruppen from Nielsen           │    │
│     │ AI Prompt: "Match 'Knorr' to these brands..."    │    │
│     │ Output: yougov_brand="Knorr",                    │    │
│     │         nielsen_brand="KNORR",                   │    │
│     │         match_type="exact", confidence=0.95      │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│           ┌──────────────┴──────────────┐                   │
│           │ Brand found?                 │                   │
│           └──────────────┬──────────────┘                   │
│              YES         │         NO                        │
│               │          │          │                        │
│               ▼          │          ▼                        │
│     [Continue to         │  3. WEB ENRICHMENT (AI Call #3)  │
│      data fetch]         │     ┌─────────────────────────┐  │
│                          │     │ Search: DuckDuckGo for  │  │
│                          │     │ "{brand} company profile"│  │
│                          │     │ AI: Extract company info │  │
│                          │     └─────────────────────────┘  │
│                          │                │                  │
│                          │                ▼                  │
│                          │  4. PROXY SCORING (AI Call #4)   │
│                          │     ┌─────────────────────────┐  │
│                          │     │ Input: Similar brands    │  │
│                          │     │ AI: Score by relevance   │  │
│                          │     │ Output: Best proxy match │  │
│                          │     └─────────────────────────┘  │
│                          │                │                  │
│                          └────────────────┘                  │
│                                   │                          │
│                                   ▼                          │
│  5. DATA RETRIEVAL                                          │
│     ┌──────────────────────────────────────────────────┐    │
│     │ YouGov Query: SELECT brand_label, metric,        │    │
│     │   AVG(score) FROM yougov WHERE sector_label IN   │    │
│     │   (resolved_sectors) GROUP BY brand_label, metric│    │
│     │                                                   │    │
│     │ Nielsen Query: SELECT marke, mediengruppe,       │    │
│     │   SUM(teuro) FROM nielsen WHERE                  │    │
│     │   wirtschaftsgruppe IN (resolved_sectors)        │    │
│     │   GROUP BY marke, mediengruppe                   │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  6. COMPETITOR DISCOVERY                                    │
│     ┌──────────────────────────────────────────────────┐    │
│     │ Query: SELECT brand_label, AVG(score) FROM yougov│    │
│     │   WHERE sector_label IN (sectors)                │    │
│     │   AND brand_label != confirmed_brand             │    │
│     │   AND metric = brand_kpi                         │    │
│     │   ORDER BY ABS(AVG(score) - target_score)        │    │
│     │   LIMIT 10                                       │    │
│     │                                                   │    │
│     │ Output: Top 10 competitors by KPI proximity      │    │
│     └──────────────────────────────────────────────────┘    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
Stage1Result (stored in run.confirmed_competitors)
```

### AI Prompts Used

#### AI Call #1: Industry Resolution

**System Prompt:**
```
You are a data classification expert. Given a user's industry description,
map it to the closest matching categories from our database.
```

**User Prompt:**
```
User industry: "{industry}"

Available YouGov sectors:
{list of all sector_labels from database}

Available Nielsen Wirtschaftsgruppen:
{list of all wirtschaftsgruppe from database}

Return JSON:
{
    "yougov_sectors": ["sector1", "sector2"],
    "nielsen_sectors": ["sector1"],
    "confidence": 0.95,
    "reasoning": "..."
}
```

#### AI Call #2: Brand Resolution

**System Prompt:**
```
You are a brand matching expert. Match the user's brand name to the
closest matches in our brand database.
```

**User Prompt:**
```
User brand: "{brand_name}"
Industry context: {resolved_sectors}

Available YouGov brands in these sectors:
{list of brand_labels}

Available Nielsen brands (Marke) in these sectors:
{list of marke}

Return JSON:
{
    "yougov_brand": "exact match or null",
    "nielsen_brand": "exact match or null",
    "match_type": "exact | fuzzy | not_found",
    "confidence": 0.0-1.0,
    "alternatives": ["alt1", "alt2"]
}
```

### Database Queries (Stage1Repository)

```python
# Get distinct sectors for AI resolution
async def get_distinct_yougov_sectors():
    SELECT DISTINCT sector_label FROM yougov ORDER BY sector_label

async def get_distinct_nielsen_sectors():
    SELECT DISTINCT wirtschaftsgruppe FROM nielsen ORDER BY wirtschaftsgruppe

# Get brands within sectors
async def get_distinct_yougov_brands(sectors):
    SELECT DISTINCT brand_label FROM yougov
    WHERE sector_label IN (sectors) ORDER BY brand_label

# Get competitors by KPI proximity
async def get_yougov_competitors(sectors, exclude_brand, primary_kpi, target_score):
    SELECT brand_label, metric, AVG(score) as avg_score
    FROM yougov
    WHERE sector_label IN (sectors)
      AND brand_label != exclude_brand
      AND metric = primary_kpi
    GROUP BY brand_label, metric
    ORDER BY ABS(AVG(score) - target_score)
    LIMIT 10
```

### Output: Stage1Result

```python
@dataclass
class Stage1Result:
    status: Stage1Status  # COMPLETED, FAILED, PARTIAL

    # Resolved industries
    yougov_sectors: List[str]
    nielsen_sectors: List[str]

    # Confirmed brand match
    confirmed_brand: ConfirmedBrand  # yougov_brand, nielsen_brand, match_type, confidence

    # If fallback used
    proxy_candidates: List[ProxyCandidate]

    # Brand data (12+ data points)
    brand_data: BrandDataPoints

    # Competitors
    competitors: List[CompetitorInfo]  # Top 10 by KPI proximity

    # Full data for Stage 2
    yougov_kpi_data: List[Dict]
    nielsen_spend_data: List[Dict]

    # Metadata
    latency_ms: int
    ai_calls_count: int
    web_searches_count: int
    warnings: List[str]
    errors: List[str]
```

---

## 6. Stage 2: Prompt Assembly

### Purpose
Prepare the full prompt for OpenAI GPT-4o including data context, expert knowledge, and output constraints.

### Location
`src/services/mediamix/prompt_assembly.py`

### Data Flow

```
Stage1Result + User Inputs
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│                  PROMPT ASSEMBLY SERVICE                     │
│                                                              │
│  1. DATA FILTERING                                          │
│     ┌──────────────────────────────────────────────────┐    │
│     │ YouGov: Fetch KPI data for all brands in sector  │    │
│     │         Format as markdown table                 │    │
│     │                                                   │    │
│     │ Nielsen: Fetch spend data by channel             │    │
│     │          Top 5 Mediengruppe by total spend      │    │
│     │          Format as markdown table                │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  2. EXPERT KNOWLEDGE FETCH                                  │
│     ┌──────────────────────────────────────────────────┐    │
│     │ Query: SELECT * FROM expert_knowledge            │    │
│     │        WHERE is_active = true                    │    │
│     │                                                   │    │
│     │ Categories:                                       │    │
│     │ - channel_heuristics: "TV best for awareness..." │    │
│     │ - budget_rules: "Minimum 10% per channel..."    │    │
│     │ - seasonality: "Q4 requires higher spend..."    │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  3. GUARDRAILS FETCH                                        │
│     ┌──────────────────────────────────────────────────┐    │
│     │ Query: SELECT * FROM prompt_guardrails           │    │
│     │        WHERE is_active = true                    │    │
│     │                                                   │    │
│     │ Types:                                            │    │
│     │ - output_format: "Return valid JSON..."          │    │
│     │ - value_constraints: "Percentages sum to 100..." │    │
│     │ - validation_rules: "Include reasoning..."      │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  4. PROMPT CONSTRUCTION                                     │
│     ┌──────────────────────────────────────────────────┐    │
│     │ SYSTEM PROMPT:                                    │    │
│     │ "You are an expert media planner at MEDIAPLUS... │    │
│     │  {guardrails}"                                   │    │
│     │                                                   │    │
│     │ USER PROMPT:                                      │    │
│     │ "## Client Information                           │    │
│     │  Brand: {customer_name}                          │    │
│     │  Industry: {industry}                            │    │
│     │  KPI: {brand_kpi}                                │    │
│     │  Budget: {total_budget}                          │    │
│     │                                                   │    │
│     │  ## Competitor Data                              │    │
│     │  {data_context}                                  │    │
│     │                                                   │    │
│     │  ## Expert Knowledge                             │    │
│     │  {expert_knowledge}                              │    │
│     │                                                   │    │
│     │  ## Task                                         │    │
│     │  Recommend optimal channel allocation..."        │    │
│     └──────────────────────────────────────────────────┘    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
AssembledPrompt (system_prompt + user_prompt)
```

### Actual System Prompt Template

```
You are an expert media planner at MEDIAPLUS, a leading German media agency.

Your task is to recommend optimal advertising budget allocation across media channels
based on competitor data, industry benchmarks, and expert knowledge.

## Output Format Requirements
- Return ONLY valid JSON
- Include all required fields: allocations, confidence, reasoning_summary
- Allocations must sum to exactly 100%
- Each allocation must have: channel, percentage, amount (if budget provided), reasoning

## Constraints
- Use only the channels present in the competitor data
- Base recommendations on the provided KPI ({brand_kpi})
- Consider seasonality if time period is specified
- Maintain minimum 5% allocation per included channel
```

### Actual User Prompt Template

```
## Client Information
- Brand: {customer_name}
- Industry: {industry}
- Target KPI: {brand_kpi}
- Budget: {total_budget} EUR (if provided)
- Time Period: {time_period_start} to {time_period_end}
- Goal: {goal_text}

## Competitor Advertising Spend Data
{nielsen_spend_markdown_table}

## Brand KPI Benchmarks
{yougov_kpi_markdown_table}

## Expert Knowledge
{expert_knowledge_content}

## Task
Based on the above data, recommend an optimal media channel allocation for {customer_name}
to maximize {brand_kpi}.

Return JSON in this format:
{
    "allocations": [
        {"channel": "TV", "percentage": 40, "amount": 200000, "reasoning": "..."},
        ...
    ],
    "confidence": 0.85,
    "reasoning_summary": "Overall strategy explanation...",
    "warnings": ["Any data quality issues..."]
}
```

### OpenAI API Call

```python
# LLM Gateway: src/services/llm_gateway/client.py

class OpenAIClient:
    model = "gpt-4o"
    timeout = 45  # seconds
    max_retries = 3

    async def generate(self, system_prompt, user_prompt, temperature=0.7, max_tokens=4096, json_mode=True):
        response = await openai.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"} if json_mode else None,
        )

        return LLMResponse(
            content=response.choices[0].message.content,
            model=response.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
            latency_ms=measured_latency,
        )
```

### Circuit Breaker & Retry Logic

```python
# Retry strategy
- Retry on: timeout, rate_limit (429), server_error (500, 502, 503)
- Don't retry on: invalid_request (400), auth_error (401)
- Backoff: exponential (1s, 2s, 4s)

# Circuit breaker
- Failure threshold: 3 failures in 10 minutes
- Recovery: Half-open after 30 seconds
- Full open: Rejects all requests immediately
```

---

## 7. Stage 3: Output Parsing

### Purpose
Parse and validate the JSON response from GPT-4o, then store in database.

### Location
`src/services/mediamix/output_parsing.py`

### Data Flow

```
LLMResponse (raw JSON string)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│                   OUTPUT PARSING SERVICE                     │
│                                                              │
│  1. JSON EXTRACTION                                         │
│     ┌──────────────────────────────────────────────────┐    │
│     │ Parse response.content as JSON                   │    │
│     │ Handle: markdown code blocks, extra whitespace   │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  2. SCHEMA VALIDATION                                       │
│     ┌──────────────────────────────────────────────────┐    │
│     │ Required fields:                                  │    │
│     │ - allocations: array of objects                  │    │
│     │   - channel: string                              │    │
│     │   - percentage: number (0-100)                   │    │
│     │   - amount: number (optional)                    │    │
│     │   - reasoning: string                            │    │
│     │ - confidence: number (0-1)                       │    │
│     │ - reasoning_summary: string                      │    │
│     │                                                   │    │
│     │ Optional fields:                                  │    │
│     │ - warnings: array of strings                     │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  3. VALUE VALIDATION                                        │
│     ┌──────────────────────────────────────────────────┐    │
│     │ Checks:                                           │    │
│     │ ✓ Total percentage = 100% (±1% tolerance)        │    │
│     │ ✓ Each percentage 0-100                          │    │
│     │ ✓ Channel names from VALID_CHANNELS set          │    │
│     │ ✓ 1-20 channels allocated                        │    │
│     │ ✓ Amounts non-negative                           │    │
│     │ ✓ Confidence between 0-1                         │    │
│     │                                                   │    │
│     │ VALID_CHANNELS = {                               │    │
│     │   "TV", "Digital", "Radio", "OOH", "Print",      │    │
│     │   "Social Media", "Video", "Audio", "Cinema",    │    │
│     │   "Sponsoring", "Events", "Influencer"           │    │
│     │ }                                                 │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  4. ERROR CLASSIFICATION                                    │
│     ┌──────────────────────────────────────────────────┐    │
│     │ Severity levels:                                  │    │
│     │ - ERROR: Blocks storage (invalid JSON, missing   │    │
│     │          required fields, sum != 100%)           │    │
│     │ - WARNING: Stored but flagged (unknown channel,  │    │
│     │            low confidence, small allocations)    │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  5. DATABASE STORAGE                                        │
│     ┌──────────────────────────────────────────────────┐    │
│     │ INSERT INTO allocation_results (                 │    │
│     │   run_id,                                        │    │
│     │   allocations,      -- JSON                      │    │
│     │   summary,          -- reasoning_summary         │    │
│     │   confidence_score, -- 0-1                       │    │
│     │   raw_response,     -- full LLM response        │    │
│     │   is_valid,         -- passed validation        │    │
│     │   validation_errors -- JSON array               │    │
│     │ )                                                │    │
│     └──────────────────────────────────────────────────┘    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
ParsedAllocationResult
```

### Example LLM Response

```json
{
    "allocations": [
        {
            "channel": "TV",
            "percentage": 40,
            "amount": 200000,
            "reasoning": "TV provides broad reach essential for building ad awareness. Competitors allocate 35-45% to TV."
        },
        {
            "channel": "Digital",
            "percentage": 30,
            "amount": 150000,
            "reasoning": "Digital enables precise targeting and real-time optimization for awareness campaigns."
        },
        {
            "channel": "OOH",
            "percentage": 20,
            "amount": 100000,
            "reasoning": "Out-of-home reinforces brand presence in high-traffic areas."
        },
        {
            "channel": "Radio",
            "percentage": 10,
            "amount": 50000,
            "reasoning": "Radio adds frequency and local reach at efficient CPMs."
        }
    ],
    "confidence": 0.85,
    "reasoning_summary": "The strategy prioritizes high-reach channels (TV 40%, OOH 20%) to maximize ad awareness, supplemented by Digital (30%) for targeting and Radio (10%) for frequency. This aligns with competitor benchmarks in the Food industry.",
    "warnings": []
}
```

### Stored Result Format

```json
// allocation_results.allocations (JSON column)
{
    "channels": [
        {"name": "TV", "percentage": 40.0, "amount": 200000, "reasoning": "..."},
        {"name": "Digital", "percentage": 30.0, "amount": 150000, "reasoning": "..."},
        {"name": "OOH", "percentage": 20.0, "amount": 100000, "reasoning": "..."},
        {"name": "Radio", "percentage": 10.0, "amount": 50000, "reasoning": "..."}
    ]
}
```

---

## 8. Stage 4: Feedback Generation

### Purpose
Analyze the allocation result and generate user-friendly feedback cards.

### Location
`src/services/mediamix/feedback_generation.py`

### Data Flow

```
ParsedAllocationResult + Run metadata
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│                 FEEDBACK GENERATION SERVICE                  │
│                                                              │
│  1. CONFIDENCE ANALYSIS                                     │
│     ┌──────────────────────────────────────────────────┐    │
│     │ if confidence < 0.4:                             │    │
│     │     → ALERT: "Low confidence - manual review"   │    │
│     │ elif confidence < 0.6:                           │    │
│     │     → WARNING: "Moderate confidence"             │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  2. CONCENTRATION ANALYSIS                                  │
│     ┌──────────────────────────────────────────────────┐    │
│     │ if any channel > 70%:                            │    │
│     │     → ALERT: "High concentration risk"           │    │
│     │ elif any channel > 50%:                          │    │
│     │     → WARNING: "Concentrated allocation"         │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  3. DIVERSIFICATION ANALYSIS                                │
│     ┌──────────────────────────────────────────────────┐    │
│     │ if channel_count < 3:                            │    │
│     │     → WARNING: "Limited diversification"         │    │
│     │                                                   │    │
│     │ if any channel < 5%:                             │    │
│     │     → INFO: "Low allocation to {channel}"        │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  4. DATA QUALITY NOTICES                                    │
│     ┌──────────────────────────────────────────────────┐    │
│     │ if validation_warnings:                          │    │
│     │     → WARNING per validation issue               │    │
│     │                                                   │    │
│     │ if competitor_data_missing:                      │    │
│     │     → WARNING: "Limited competitor data"         │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  5. RECOMMENDATIONS                                         │
│     ┌──────────────────────────────────────────────────┐    │
│     │ KPI-specific suggestions:                        │    │
│     │ - adaware: "Consider Social Media, Video"       │    │
│     │ - consider: "Focus on Digital, Influencer"      │    │
│     │ - aware: "Balance TV and Digital"               │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  6. SUMMARY GENERATION                                      │
│     ┌──────────────────────────────────────────────────┐    │
│     │ Create summary card with:                        │    │
│     │ - Top 3 channels with percentages                │    │
│     │ - Overall strategy in plain language             │    │
│     │ - Confidence indicator                           │    │
│     └──────────────────────────────────────────────────┘    │
│                          │                                   │
│                          ▼                                   │
│  7. DATABASE STORAGE                                        │
│     ┌──────────────────────────────────────────────────┐    │
│     │ INSERT INTO chat_history (                       │    │
│     │   run_id, message_type, severity,                │    │
│     │   title, content, extra_data, display_order      │    │
│     │ )                                                │    │
│     │                                                   │    │
│     │ message_types: warning, alert, summary,          │    │
│     │                recommendation, info              │    │
│     │ severity: info, warning, error                   │    │
│     └──────────────────────────────────────────────────┘    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
FeedbackGenerationResult (list of FeedbackMessage)
```

### Example Generated Feedback

```json
[
    {
        "message_type": "summary",
        "severity": "info",
        "title": "Allocation Strategy Summary",
        "content": "The strategy prioritizes high-reach channels like TV and OOH for ad awareness while leveraging digital for precision targeting.",
        "extra_data": {
            "top_channels": [
                {"channel": "TV", "percentage": 40.0},
                {"channel": "Digital", "percentage": 30.0},
                {"channel": "OOH", "percentage": 20.0}
            ],
            "total_channels": 4,
            "confidence": 0.85
        },
        "display_order": 0
    },
    {
        "message_type": "recommendation",
        "severity": "info",
        "title": "Consider Additional Channels for ADAWARE",
        "content": "To maximize adaware, you might also consider: Social Media, Video.",
        "extra_data": {
            "kpi": "adaware",
            "suggested_channels": ["Social Media", "Video"]
        },
        "display_order": 1
    },
    {
        "message_type": "warning",
        "severity": "warning",
        "title": "Concentrated Allocation",
        "content": "TV receives 40% of the budget. Consider diversifying if market conditions change.",
        "extra_data": {
            "channel": "TV",
            "percentage": 40.0
        },
        "display_order": 2
    }
]
```

---

## 9. Database Schema

### Entity Relationship Diagram

```
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│      runs       │       │allocation_results│      │  chat_history   │
├─────────────────┤       ├─────────────────┤       ├─────────────────┤
│ id (PK)         │───┐   │ id (PK)         │   ┌───│ id (PK)         │
│ session_token   │   │   │ run_id (FK) ────│───┤   │ run_id (FK) ────│───┐
│ customer_name   │   │   │ allocations     │   │   │ message_type    │   │
│ industry        │   │   │ summary         │   │   │ severity        │   │
│ brand_kpi       │   │   │ confidence_score│   │   │ title           │   │
│ total_budget    │   │   │ raw_response    │   │   │ content         │   │
│ status          │   │   │ is_valid        │   │   │ display_order   │   │
│ confirmed_      │   │   │ validation_     │   │   └─────────────────┘   │
│   competitors   │   │   │   errors        │   │                         │
│ input_hash      │   │   └─────────────────┘   │   ┌─────────────────┐   │
│ error_message   │   │                         │   │  prompt_traces  │   │
└─────────────────┘   │                         │   ├─────────────────┤   │
         │            │                         │   │ id (PK)         │   │
         │            └─────────────────────────┴───│ run_id (FK) ────│───┘
         │                                          │ model           │
         │                                          │ prompt          │
         │                                          │ response        │
         │                                          │ total_tokens    │
         │                                          │ latency_ms      │
         │                                          │ status          │
         │                                          └─────────────────┘
         │
         │            ┌─────────────────┐       ┌─────────────────┐
         │            │     yougov      │       │     nielsen     │
         │            ├─────────────────┤       ├─────────────────┤
         │            │ id (PK)         │       │ id (PK)         │
         │            │ date            │       │ wirtschafts-    │
         │            │ sector_label    │       │   gruppe        │
         │            │ brand_label     │       │ marke           │
         │            │ metric          │       │ mediengruppe    │
         │            │ score           │       │ jahr            │
         │            └─────────────────┘       │ monat           │
         │                                      │ teuro           │
         │            ┌─────────────────┐       └─────────────────┘
         │            │expert_knowledge │
         │            ├─────────────────┤       ┌─────────────────┐
         │            │ id (PK)         │       │prompt_guardrails│
         │            │ version         │       ├─────────────────┤
         │            │ category        │       │ id (PK)         │
         │            │ content         │       │ version         │
         │            │ is_active       │       │ guardrail_type  │
         │            └─────────────────┘       │ content         │
         │                                      │ is_active       │
         │                                      └─────────────────┘
         │
         └─────────────────────────────────────────────────────────────
```

### Key Tables

#### runs
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| session_token | VARCHAR(255) | Session from JS Backend |
| customer_name | VARCHAR(255) | Brand name |
| industry | VARCHAR(255) | Industry category |
| brand_kpi | VARCHAR(50) | Target KPI |
| total_budget | DECIMAL(15,2) | Budget in EUR |
| status | VARCHAR(50) | Run status |
| confirmed_competitors | JSON | Stage 1 results |
| input_hash | VARCHAR(64) | For change detection |

#### yougov
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| date | DATE | Data date |
| sector_label | TEXT | Industry sector |
| brand_label | TEXT | Brand name |
| metric | TEXT | adaware/aware/consider |
| score | FLOAT | KPI score |

#### nielsen
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| wirtschaftsgruppe | TEXT | German industry |
| marke | TEXT | Brand name |
| mediengruppe | TEXT | Media channel |
| jahr | INTEGER | Year |
| monat | TEXT | German month |
| teuro | FLOAT | Spend (thousands EUR) |

---

## 10. AI Prompts & Logic

### Summary of AI Calls

| Stage | AI Call | Model | Purpose | Fallback |
|-------|---------|-------|---------|----------|
| S1 | Industry Resolution | gpt-4o-mini | Map user industry to DB categories | Fuzzy match |
| S1 | Brand Resolution | gpt-4o-mini | Match user brand to DB names | Web search |
| S1 | Web Enrichment | gpt-4o-mini | Extract company info from web | Skip |
| S1 | Proxy Scoring | gpt-4o-mini | Score alternative brands | Use top result |
| S2 | Allocation Generation | gpt-4o | Main recommendation | Fail run |

### Token Usage Estimates

| Call | Prompt Tokens | Completion Tokens | Total |
|------|---------------|-------------------|-------|
| Industry Resolution | ~500 | ~100 | ~600 |
| Brand Resolution | ~800 | ~150 | ~950 |
| Web Enrichment | ~1500 | ~300 | ~1800 |
| Proxy Scoring | ~600 | ~200 | ~800 |
| Allocation Generation | ~2500 | ~800 | ~3300 |
| **Total (happy path)** | **~3800** | **~1050** | **~4850** |
| **Total (with fallback)** | **~5900** | **~1550** | **~7450** |

---

## 11. Data Flow Diagrams

### Complete Request Lifecycle

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    COMPLETE REQUEST LIFECYCLE                            │
└─────────────────────────────────────────────────────────────────────────┘

[HTTP Request]
     │
     ▼
┌─────────────────┐
│ Rate Limiter    │──── 429 if exceeded (20/hour, 100/min)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Session Validator│──── 401 if missing/invalid token
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  API Endpoint   │
│  (runs.py)      │
└────────┬────────┘
         │
         ├──[POST /runs]───────────────────────────────────┐
         │                                                  │
         │  1. Validate request body                       │
         │  2. Create Run record (status=PENDING)          │
         │  3. Calculate input_hash (Guard #3)             │
         │  4. Trigger background task                     │
         │  5. Return 201 immediately                      │
         │                                                  │
         │         ┌────────────────────────────────────────┘
         │         │ [Background: run_stage1_background]
         │         ▼
         │  ┌─────────────────────────────────────────┐
         │  │ STAGE 1: Competitor Matching            │
         │  │                                         │
         │  │ 1. AI Call #1: Industry Resolution     │
         │  │    - Query: DISTINCT sectors           │
         │  │    - Prompt: Map user industry         │
         │  │                                         │
         │  │ 2. AI Call #2: Brand Resolution        │
         │  │    - Query: Brands in sectors          │
         │  │    - Prompt: Match user brand          │
         │  │                                         │
         │  │ 3. [If not found] Web Enrichment       │
         │  │    - Search: DuckDuckGo                │
         │  │    - AI: Extract company profile       │
         │  │                                         │
         │  │ 4. [If low confidence] Proxy Scoring   │
         │  │    - AI: Score similar brands          │
         │  │                                         │
         │  │ 5. Data Fetch                          │
         │  │    - YouGov: KPI scores                │
         │  │    - Nielsen: Spend by channel         │
         │  │                                         │
         │  │ 6. Competitor Discovery                │
         │  │    - Top 10 by KPI proximity           │
         │  │                                         │
         │  │ → Update status: AWAITING_CONFIRMATION │
         │  └─────────────────────────────────────────┘
         │
         ├──[GET /runs/{id}/status]────────────────────────┐
         │                                                  │
         │  Read run.status, calculate stage/progress_pct  │
         │  Return current state                           │
         │                                                  │
         ├──[GET /runs/{id}/competitors]───────────────────┐
         │                                                  │
         │  Read run.confirmed_competitors JSON            │
         │  Format as CompetitorSetResponse                │
         │                                                  │
         ├──[POST /runs/{id}/competitors/confirm]──────────┐
         │                                                  │
         │  If action=approve:                             │
         │    1. Validate selected brands                  │
         │    2. Update confirmed_competitors              │
         │    3. Change status to GENERATING               │
         │    4. Trigger AI generation background          │
         │                                                  │
         │         ┌───────────────────────────────────────┘
         │         │ [Background: run_ai_generation_background]
         │         ▼
         │  ┌─────────────────────────────────────────┐
         │  │ STAGE 2: Prompt Assembly                │
         │  │                                         │
         │  │ 1. DataFilteringService                │
         │  │    - Fetch YouGov KPI data             │
         │  │    - Fetch Nielsen spend data          │
         │  │    - Format as markdown tables         │
         │  │                                         │
         │  │ 2. ExpertKnowledgeRepository           │
         │  │    - Get active expert knowledge       │
         │  │                                         │
         │  │ 3. PromptGuardrailsRepository          │
         │  │    - Get active output constraints     │
         │  │                                         │
         │  │ 4. Construct system + user prompts     │
         │  │                                         │
         │  │ 5. PromptTraceLogger.start_trace()     │
         │  │                                         │
         │  │ 6. OpenAIClient.generate()             │
         │  │    - Model: gpt-4o                     │
         │  │    - JSON mode: enabled                │
         │  │    - Timeout: 45s                      │
         │  │    - Retries: 3x exponential backoff   │
         │  │    - Circuit breaker protection        │
         │  │                                         │
         │  │ 7. PromptTraceLogger.complete_trace()  │
         │  │                                         │
         │  │ → Update status: PARSING               │
         │  └─────────────────────────────────────────┘
         │                     │
         │                     ▼
         │  ┌─────────────────────────────────────────┐
         │  │ STAGE 3: Output Parsing                 │
         │  │                                         │
         │  │ 1. Parse JSON from LLM response        │
         │  │                                         │
         │  │ 2. Schema validation                   │
         │  │    - Required fields present           │
         │  │    - Types correct                     │
         │  │                                         │
         │  │ 3. Value validation                    │
         │  │    - Percentages sum to 100%           │
         │  │    - Valid channel names               │
         │  │    - Confidence 0-1                    │
         │  │                                         │
         │  │ 4. Create AllocationResult record      │
         │  │                                         │
         │  │ → Update status: FEEDBACK              │
         │  └─────────────────────────────────────────┘
         │                     │
         │                     ▼
         │  ┌─────────────────────────────────────────┐
         │  │ STAGE 4: Feedback Generation            │
         │  │                                         │
         │  │ 1. Analyze confidence level            │
         │  │ 2. Check concentration risk            │
         │  │ 3. Check diversification               │
         │  │ 4. Add data quality notices            │
         │  │ 5. Generate recommendations            │
         │  │ 6. Create summary card                 │
         │  │ 7. Store as ChatHistory records        │
         │  │                                         │
         │  │ → Update status: COMPLETED             │
         │  └─────────────────────────────────────────┘
         │
         ├──[GET /runs/{id}/result]────────────────────────┐
         │                                                  │
         │  Read allocation_results by run_id              │
         │  Format as AllocationResultResponse             │
         │                                                  │
         ├──[GET /runs/{id}/chat]──────────────────────────┐
         │                                                  │
         │  Read chat_history by run_id                    │
         │  Filter by message_type (optional)              │
         │  Return sorted by display_order                 │
         │                                                  │
         └──[GET /runs/{id}/trace]─────────────────────────┐
                                                            │
            Require owner access (X-User-Role: owner)      │
            Read prompt_traces by run_id                   │
            Return with aggregated stats                   │
```

---

## 12. Error Handling & Guards

### Guard #1: Rate Limiting

```
Location: src/api/middleware/rate_limit.py

Limits:
- 100 requests/minute (global)
- 20 generations/hour (POST /runs)

Response on exceed:
HTTP 429 Too Many Requests
{
    "detail": "Rate limit exceeded",
    "retry_after": 60
}
```

### Guard #2: Data Feasibility

```
Location: src/services/guards/data_feasibility.py

Checks before LLM call:
1. Industry exists in database
2. At least 3 competitors found
3. KPI data available for brand_kpi
4. Nielsen spend data exists

Response on failure:
HTTP 400 Bad Request
{
    "detail": "Insufficient data for {industry}",
    "suggestions": ["Automotive", "Consumer Goods"]
}
```

### Guard #3: Change Detection

```
Location: src/services/guards/change_detection.py

Logic:
1. Calculate SHA256 hash of inputs:
   hash(customer_name + industry + brand_kpi + budget + time_period)

2. Check for existing completed run with same hash

3. If found: Return cached result (is_cached=true)
   If not: Proceed with new generation

Purpose: Prevent redundant LLM calls
```

### Error Response Format

```json
{
    "detail": "Error message",
    "error": "error_code",
    "code": "VALIDATION_ERROR"
}
```

### HTTP Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Created (POST /runs) |
| 400 | Bad request / validation error |
| 401 | Missing session token |
| 403 | Forbidden (not your run / not owner) |
| 404 | Run not found |
| 429 | Rate limit exceeded |
| 500 | Internal server error |

---

## 13. Configuration & Deployment

### Environment Variables

```bash
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/allocate_ai

# OpenAI
OPENAI_API_KEY=sk-...

# Application
APP_ENV=development  # development | staging | production
DEBUG=true
LOG_LEVEL=INFO

# Rate Limiting
RATE_LIMIT_GENERATIONS_PER_HOUR=20

# LLM Settings
LLM_TIMEOUT_SECONDS=45
LLM_MAX_RETRIES=3

# Caching
RESULT_CACHE_TTL_SECONDS=3600
```

### Running the Server

```bash
# Development
uvicorn src.main:app --host 127.0.0.1 --port 8081 --reload

# Production
uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Health Checks

```
GET /health
Response: {"status": "healthy", "version": "0.1.0"}

GET /ready
Response: {"status": "ready", "database": "connected"}
```

---

## Appendix A: Sample Data

### YouGov Sample (9,240 rows)

| date | sector_label | brand_label | metric | score |
|------|--------------|-------------|--------|-------|
| 2024-01-01 | Food II | Knorr | adaware | 12.5 |
| 2024-01-01 | Food II | Knorr | consider | 38.2 |
| 2024-01-01 | Food II | Maggi | adaware | 15.3 |

### Nielsen Sample (8,893 rows)

| wirtschaftsgruppe | marke | mediengruppe | jahr | monat | teuro |
|-------------------|-------|--------------|------|-------|-------|
| Food | KNORR | TV | 2024 | Januar | 2500.5 |
| Food | KNORR | Digital | 2024 | Januar | 1200.3 |
| Food | MAGGI | TV | 2024 | Januar | 1800.0 |

---

## Appendix B: Glossary

| Term | Definition |
|------|------------|
| adaware | KPI: Ad awareness score |
| aware | KPI: Brand awareness score |
| consider | KPI: Purchase consideration score |
| Wirtschaftsgruppe | German: Industry group (Nielsen classification) |
| sector_label | YouGov industry classification |
| TEuro | Thousands of Euros |
| Mediengruppe | Media group/channel |
| Marke | Brand (German) |

---

**Document End**

*Generated: May 2026*
*Allocate.AI v1.0*
