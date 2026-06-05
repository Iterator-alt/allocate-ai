# AllocateAI Backend - Comprehensive Codebase Knowledge

## Project Overview

**AllocateAI** is an AI-powered media budget allocation system that helps brands optimize their advertising spend across channels. The system analyzes competitor data from YouGov (brand perception) and Nielsen (advertising spend) to generate data-driven allocation recommendations.

### Key Value Proposition
- AI discovers competitors automatically based on brand and industry
- Uses real competitor spend and KPI data to inform recommendations
- Supports two modes: Budget-to-Impact (optimize KPI with fixed budget) and Goal-to-Budget (calculate budget for KPI goal)

### Architecture
- **Python Backend** (this codebase): FastAPI + SQLAlchemy async
- **JS Backend**: Prisma ORM + Node.js (separate codebase, owns ProjectVersion tables)
- **Database**: PostgreSQL with both Python-managed and Prisma-managed tables
- **LLM**: OpenAI GPT-4o for allocation generation, GPT-4o-mini for Stage 1 AI calls

---

## Folder and File Structure

```
C:\Users\Mohit\allocate-ai/
├── src/                           # Main application code
│   ├── main.py                   # FastAPI app entry point, middleware, routers
│   ├── config.py                 # Environment configuration with pydantic-settings
│   ├── dependencies.py           # Dependency injection (get_db)
│   │
│   ├── api/                      # API layer
│   │   ├── v1/                   # Version 1 endpoints
│   │   │   ├── runs.py          # POST /runs, GET /status, GET /result, GET /debug-zip
│   │   │   ├── competitors.py   # GET /search, POST /confirm
│   │   │   ├── chat.py          # POST /message, GET /history
│   │   │   ├── stage1.py        # POST /process (standalone Stage 1)
│   │   │   ├── results.py       # Placeholder (results via runs.py)
│   │   │   └── traces.py        # Placeholder (returns 501)
│   │   ├── schemas/              # Pydantic request/response models
│   │   │   ├── common.py        # BaseSchema, ErrorResponse, SuccessResponse
│   │   │   ├── runs.py          # RunStatus enum, RunStatusResponse, StartRunRequest
│   │   │   ├── competitors.py   # ConfirmCompetitorsRequest/Response
│   │   │   ├── chat.py          # ChatMessageRequest/Response
│   │   │   ├── chat_agent.py    # Agent-specific schemas
│   │   │   ├── results.py       # AllocationResultResponse
│   │   │   └── traces.py        # PromptTraceResponse
│   │   └── middleware/           # HTTP middleware
│   │       ├── session.py       # X-Session-Token validation
│   │       └── rate_limit.py    # slowapi rate limiting (20/hour)
│   │
│   ├── db/                       # Database layer
│   │   ├── base.py              # SQLAlchemy Base, TimestampMixin
│   │   ├── session.py           # AsyncSessionLocal factory, connection pool config
│   │   └── models/               # SQLAlchemy ORM models
│   │       ├── data.py          # YouGov, Nielsen tables (data sources)
│   │       ├── prisma_tables.py # PrismaProjectVersion, PrismaProjectVersionAiRun
│   │       ├── run.py           # Run, AllocationResult, ChatHistory (Python tables)
│   │       ├── mapping.py       # IndustryMap, BrandMap (deprecated in Prisma-only)
│   │       ├── prompt.py        # ExpertKnowledge, PromptGuardrails, PromptTrace
│   │       ├── shared.py        # User, Project, ProjectVersion (legacy)
│   │       └── logging.py       # UsageLog table
│   │
│   ├── services/                 # Business logic layer
│   │   ├── stage1/               # Stage 1: Competitor Discovery
│   │   │   ├── orchestrator.py  # Main Stage 1 coordinator
│   │   │   ├── ai_resolution.py # AI calls #1-6 (industry, brand, competitors)
│   │   │   ├── repository.py    # YouGov/Nielsen queries for Stage 1
│   │   │   ├── cache.py         # 24-hour TTL cache for DISTINCT values
│   │   │   └── debug_output.py  # Debug file logging
│   │   │
│   │   ├── mediamix/             # Stages 2-4: Allocation Generation
│   │   │   ├── prompt_assembly.py    # Builds LLM prompt from competitor data
│   │   │   ├── data_filtering.py     # Filters/formats data for prompt
│   │   │   ├── output_parsing.py     # Parses LLM JSON response
│   │   │   ├── feedback_generation.py # Creates user-facing feedback cards
│   │   │   └── competitor_matching.py # Industry lookup (Prisma-only mode)
│   │   │
│   │   ├── chat/                 # Chat Agent
│   │   │   ├── agent.py         # Main ChatAgent orchestrator
│   │   │   ├── intent_classifier.py # NLU for user intent
│   │   │   └── tools/            # Agent tools
│   │   │       ├── context_loader.py   # Load run context
│   │   │       ├── competitor_tool.py  # Edit competitors
│   │   │       ├── editing_tool.py     # Modify allocations
│   │   │       ├── rerun_tool.py       # Trigger reruns
│   │   │       └── question_tool.py    # Q&A about allocations
│   │   │
│   │   ├── llm_gateway/          # OpenAI integration
│   │   │   ├── client.py        # Resilient OpenAI wrapper (retry, circuit breaker)
│   │   │   └── trace_logger.py  # Logs LLM calls to PromptTrace table
│   │   │
│   │   ├── guards/               # Validation services
│   │   │   ├── change_detection.py # Detect input changes for Stage 1 skip
│   │   │   └── feasibility.py      # Validate allocation feasibility
│   │   │
│   │   ├── data_sources/         # Data source adapters
│   │   │   ├── base.py          # Abstract base
│   │   │   ├── database.py      # Database adapter
│   │   │   └── api.py           # API adapter (future)
│   │   │
│   │   ├── usage_logging.py     # Token usage and cost tracking
│   │   └── data_validation.py   # Input validation
│   │
│   ├── repositories/             # Data access layer
│   │   ├── base.py              # BaseRepository abstract class
│   │   ├── yougov.py            # YouGov queries
│   │   ├── nielsen.py           # Nielsen queries
│   │   ├── run.py               # Run, AllocationResult, ChatHistory CRUD
│   │   ├── prompt.py            # ExpertKnowledge, Guardrails, PromptTrace
│   │   └── mapping.py           # IndustryMap, BrandMap (deprecated)
│   │
│   └── utils/                    # Utilities
│       └── date_utils.py        # Date parsing helpers
│
├── alembic/                      # Database migrations
│   ├── versions/
│   │   ├── 001_initial_schema.py    # Creates all Python tables
│   │   └── 002_stage1_tables.py     # Creates YouGov, Nielsen with Stage 1 schema
│   └── env.py                   # Alembic configuration
│
├── scripts/                      # Utility scripts
│   ├── ingest_csv_data.py       # Ingest YouGov/Nielsen CSV files
│   ├── seed_prisma_test_data.py # Insert test data into Prisma tables
│   └── seed_staging_ehrmann.py  # Seed Ehrmann yogurt test data
│
├── tests/                        # Test suite
│   ├── conftest.py              # Pytest fixtures
│   ├── unit/                    # Unit tests
│   └── integration/             # Integration tests
│
├── debug_output/                 # Debug files (when STAGE1_DEBUG_MODE=True)
│
├── .env                          # Environment variables (not in git)
├── .env.example                  # Environment template
├── alembic.ini                   # Alembic config
└── pytest.ini                    # Pytest config
```

---

## API Endpoints

### Run Management (`/api/v1/runs`)

| Method | Path | Request Body | Response | Purpose |
|--------|------|--------------|----------|---------|
| POST | `/runs` | `{run_id: int, action: "start"}` | `{run_id, status: "started", error_message}` | Start a new generation run. Triggers Stage 1-4 pipeline in background. Rate limited: 20/hour. |
| GET | `/runs/{run_id}/status` | - | `{id, status, stage, progress_pct, progress, started_at, completed_at, error_message}` | Poll run status. Use to wait for completion. |
| GET | `/runs/{run_id}/result` | - | `{allocations: [...], summary, total_budget_eur, ...}` | Get allocation result. Only available when status="completed". |
| GET | `/runs/{run_id}/debug-zip` | - | ZIP file | Download debug files (prompts, responses, parsed output). Requires STAGE1_DEBUG_MODE=True. |

### Competitor Management (`/api/v1/runs/{run_id}/competitors`)

| Method | Path | Request Body | Response | Purpose |
|--------|------|--------------|----------|---------|
| GET | `/runs/{run_id}/competitors/search` | Query: `?q=brand_name` | `{found, results: [{source, brand, has_yougov_data, has_nielsen_data, warning}], warning}` | Search for a competitor brand in snapshot/database. |
| POST | `/runs/{run_id}/competitors/confirm` | `{run_id, action: "approved" | "dismissed"}` | `{run_id, status, confirmed_competitors, message}` | Confirm/dismiss competitors after Stage 1. Frontend must save confirmedCompetitors to DB first. |

### Chat Agent (`/api/v1/chat`)

| Method | Path | Request Body | Response | Purpose |
|--------|------|--------------|----------|---------|
| POST | `/chat/message` | `{run_id, message, project_id?, version_id?}` | `{agent_response, tool_used, updated_competitor_set, updated_inputs, rerun_triggered, chat_message_id, pending_changes}` | Send message to chat agent. May trigger reruns. |
| GET | `/chat/{run_id}/history` | - | `{messages: [{role, content, tool_used, timestamp}]}` | Get chat history for a run. |

### Stage 1 Standalone (`/api/v1/stage1`)

| Method | Path | Request Body | Response | Purpose |
|--------|------|--------------|----------|---------|
| POST | `/stage1/process` | `{brand_name, industry, brand_kpi, media_channels, goal_direction}` | `{status, competitors, confirmed_brand, yougov_sectors, nielsen_sectors, errors}` | Execute Stage 1 pipeline standalone (for testing). |
| GET | `/stage1/cache/clear` | - | `{message}` | Clear Stage 1 caches. |

---

## Database Tables

### Prisma-Managed Tables (JS Backend owns, Python reads/writes)

#### `ProjectVersion`
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| projectId | TEXT | Foreign key to Project |
| versionNumber | INT | Version index |
| versionName | TEXT | Version display name |
| **customer** | TEXT | Brand name (e.g., "Ehrmann Almighurt") |
| **industry** | TEXT | Wirtschaftsgruppe (e.g., "Lebensmittel") |
| **brandKpi** | TEXT | Target KPI: "adaware", "aware", "consider" |
| **mediaChannels** | TEXT[] | Selected channels: ["TV", "Digital", "OOH", ...] |
| **goalMode** | TEXT | "budget" or "goal" |
| **goalText** | TEXT | User's goal description |
| status | TEXT | "active", "archived", etc. |
| createdAt, updatedAt, deletedAt | TIMESTAMP | Timestamps (no timezone) |

#### `ProjectVersionAiRun`
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| projectVersionId | TEXT | Foreign key to ProjectVersion |
| **externalRunId** | INT | Unique reference used by Python (lookup key) |
| **status** | TEXT | "pending", "matching", "awaiting_confirmation", "generating", "parsing", "completing", "completed", "failed", "cancelled" |
| **stage** | TEXT | Current stage: "S1", "S2", "S3", "S4" |
| **progressPct** | INT | Progress 0-100 |
| **progressMessage** | TEXT | Human-readable status |
| startedAt, completedAt | TIMESTAMP | Timing |
| errorMessage | TEXT | Error details if failed |
| **competitorSnapshot** | JSONB | Stage 1 output with competitor list |
| **confirmedCompetitors** | TEXT[] | User-confirmed YouGov brand labels |
| **allocationResult** | JSONB | Stage 2-4 output with allocations |
| **chatSnapshot** | JSONB | Chat history |
| **rawPayload** | JSONB | Caching/debugging (stores last_inputs for skip detection) |
| createdAt, updatedAt | TIMESTAMP | Timestamps |

### Python-Managed Data Tables

#### `yougov` (YouGov BrandIndex)
| Column | Type | Description |
|--------|------|-------------|
| id | INT | Primary key |
| date | DATE | Data point date |
| **sector_label** | TEXT | Industry sector (indexed) |
| **brand_label** | TEXT | Brand name (indexed) |
| **metric** | TEXT | "adaware", "aware", "consider" |
| **score** | FLOAT | KPI value (0-100) |
| volume, positives, negatives, neutrals | FLOAT | Additional metrics |

#### `nielsen` (Nielsen WizzAd)
| Column | Type | Description |
|--------|------|-------------|
| id | INT | Primary key |
| **wirtschaftsgruppe** | TEXT | Industry (indexed) |
| konzern, firma | TEXT | Company hierarchy |
| **marke** | TEXT | Brand name (indexed) |
| produktmarke | TEXT | Product variant |
| **jahr** | INT | Year (indexed) |
| monat | TEXT | German month name |
| **mediengruppe** | TEXT | Media channel |
| **teuro** | FLOAT | Spend in thousands EUR |

### Python-Managed Run Tables (Legacy, not used in Prisma-only mode)

#### `runs`
| Column | Type | Description |
|--------|------|-------------|
| id | INT | Primary key |
| session_token | TEXT | User session |
| customer_name, industry, brand_kpi | TEXT | User inputs |
| total_budget | DECIMAL | Budget in EUR |
| status | TEXT | Run status |
| confirmed_competitors | JSON | Confirmed brands |
| input_hash | TEXT | For caching |

#### `allocation_results`
| Column | Type | Description |
|--------|------|-------------|
| run_id | INT | Foreign key to runs |
| allocations | JSON | Channel allocations |
| summary | TEXT | LLM summary |
| confidence_score | FLOAT | 0-1 confidence |
| raw_response | TEXT | Raw LLM output |

#### `chat_history`
| Column | Type | Description |
|--------|------|-------------|
| run_id | INT | Foreign key to runs |
| message_type | TEXT | "warning", "alert", "summary", "recommendation" |
| severity | TEXT | "info", "warning", "error" |
| title, content | TEXT | Message text |
| extra_data | JSON | Metadata |

---

## Configuration Flags

| Flag | Default | Environment Variable | Description |
|------|---------|---------------------|-------------|
| `database_url` | `postgresql+asyncpg://...` | `DATABASE_URL` | PostgreSQL connection string |
| `openai_api_key` | `""` | `OPENAI_API_KEY` | OpenAI API key (required) |
| `app_env` | `"development"` | `APP_ENV` | Environment: development, staging, production |
| `debug` | `True` | `DEBUG` | Enable debug mode |
| `log_level` | `"INFO"` | `LOG_LEVEL` | Logging level |
| `stage1_debug_mode` | `True` | `STAGE1_DEBUG_MODE` | Save debug files to /debug_output |
| `rate_limit_generations_per_hour` | `20` | `RATE_LIMIT_GENERATIONS_PER_HOUR` | Per-user rate limit |
| `llm_timeout_seconds` | `45` | `LLM_TIMEOUT_SECONDS` | LLM request timeout |
| `llm_max_retries` | `3` | `LLM_MAX_RETRIES` | LLM retry attempts |
| `result_cache_ttl_seconds` | `3600` | `RESULT_CACHE_TTL_SECONDS` | Result cache TTL |
| `bypass_competitor_confirmation` | `False` | `BYPASS_COMPETITOR_CONFIRMATION` | Auto-approve competitors after Stage 1 |
| `chat_rerun_creates_new` | `True` | `CHAT_RERUN_CREATES_NEW` | True=new run on rerun, False=update existing |
| `chat_agent_mode` | `True` | `CHAT_AGENT_MODE` | True=full agent mode, False=Q&A only |

---

## LLM Calls

### Stage 1: Competitor Discovery (GPT-4o-mini)

| Call # | Name | When | Input | Output |
|--------|------|------|-------|--------|
| #1 | Industry Resolution | Always | User industry, available YouGov sectors, Nielsen wirtschaftsgruppen | `{yougov_sectors: [], nielsen_sectors: [], confidence}` |
| #2 | Brand Resolution | Always | User brand name, available YouGov brands, Nielsen marken | `{yougov_brand, nielsen_brand, match_type, confidence}` |
| #3 | Web Enrichment | Fallback only (brand not found) | Company name | `{size_tier, revenue_range, market_position}` |
| #4 | Proxy Scoring | Fallback only | Candidate brands, web enrichment data | `{candidates: [{brand_label, score, reasoning}]}` |
| #5 | Competitor Suggestion | Always | Confirmed brand, industry, available brands | `{suggested_competitors: []}` |
| #6 | Produktmarke Filter | Per competitor | Brand, available produktmarke list | `{relevant: [], excluded: []}` |

### Stage 2: Allocation Generation (GPT-4o)

| Call # | Name | When | Input | Output |
|--------|------|------|-------|--------|
| #7 | Allocation Generation | Always | System prompt (media planning expert), User prompt (client info, competitor data, channels) | JSON: `{channels: [{name, percentage, amount, rationale}], totalBudgetEur, kpiProjection, summary, confidence}` |

### LLM Client Features (`src/services/llm_gateway/client.py`)
- **JSON Mode**: Ensures structured JSON output
- **Retry**: 3 attempts with exponential backoff
- **Circuit Breaker**: Opens after 3 failures in 10 minutes
- **Timeout**: 45 seconds per request
- **Token Tracking**: Logs prompt_tokens, completion_tokens, total_tokens

---

## Background Tasks

### `run_full_pipeline_background(external_run_id, prisma_ai_run_id)`
**Trigger**: POST /runs when full Stage 1-4 required
**Location**: `src/api/v1/runs.py:322`
**Process**:
1. Stage 1: Competitor discovery (AI calls #1-6)
2. Store competitorSnapshot
3. If `bypass_competitor_confirmation=False`: Stop at "awaiting_confirmation"
4. If `bypass_competitor_confirmation=True`: Auto-confirm and continue
5. Stage 2: Allocation generation (AI call #7)
6. Stage 3: Parse LLM response
7. Stage 4: Store result in allocationResult, set status="completed"

### `_run_stages_2_to_4_pipeline(prisma_ai_run_id, external_run_id)`
**Trigger**:
- POST /runs when Stage 1 can be skipped (only preference fields changed)
- POST /competitors/confirm when user confirms competitors
**Location**: `src/api/v1/runs.py:803`
**Process**:
1. Read confirmedCompetitors from DB
2. Map YouGov names to Nielsen names using competitorSnapshot
3. Stage 2: Allocation generation
4. Stage 3: Parse response
5. Stage 4: Store result, set status="completed"

---

## Data Flow

### Complete Flow: User Input to Final Result

```
1. JS Backend creates ProjectVersionAiRun with externalRunId
   └── Saves campaign inputs to ProjectVersion

2. Frontend calls POST /api/v1/runs {run_id, action: "start"}
   └── Python looks up ProjectVersionAiRun by externalRunId
   └── Extracts inputs from ProjectVersion (customer, industry, brandKpi, mediaChannels, goalMode, goalText)
   └── Checks if Stage 1 can be skipped (only preference fields changed)

3. Stage 1: Competitor Discovery (if not skipped)
   ├── AI Call #1: Resolve industry → yougov_sectors, nielsen_sectors
   ├── AI Call #2: Resolve brand → yougov_brand, nielsen_brand
   ├── Query YouGov: Get brands in sector
   ├── Query Nielsen: Get brands in wirtschaftsgruppe
   ├── AI Call #5: Suggest competitors from available brands
   ├── AI Call #6: Filter produktmarke per competitor
   └── Store competitorSnapshot in ProjectVersionAiRun

4. Competitor Confirmation Gate
   ├── If bypass_competitor_confirmation=True: Auto-confirm, continue
   └── If bypass_competitor_confirmation=False:
       ├── Set status="awaiting_confirmation", stop
       ├── Frontend reads competitorSnapshot
       ├── User confirms/edits competitors
       ├── Frontend saves confirmedCompetitors to DB
       └── Frontend calls POST /competitors/confirm {action: "approved"}

5. Stage 2: Allocation Generation
   ├── Query YouGov: Get KPI scores for confirmed competitors
   ├── Query Nielsen: Get channel spend for confirmed competitors
   ├── Build relationship table: brand + channel + spend + KPI uplift
   ├── Assemble prompt with competitor data citations
   ├── AI Call #7: Generate allocation (GPT-4o, JSON mode)
   └── Save debug files (if STAGE1_DEBUG_MODE=True)

6. Stage 3: Parse Response
   ├── Parse JSON from LLM response
   ├── Normalize percentages to 100%
   ├── Add missing user-selected channels (5% minimum)
   ├── Calculate budget amounts if total_budget provided
   └── Validate structure

7. Stage 4: Store Result
   ├── Store allocationResult in ProjectVersionAiRun
   ├── Set status="completed", progressPct=100
   ├── Create debug ZIP
   └── Frontend polls GET /status, then GET /result
```

---

## Key Design Decisions

### 1. Prisma-Only Mode
**Decision**: No Python-managed run/result tables. All state in Prisma tables.
**Why**:
- Single source of truth (JS Backend owns schema)
- No migration conflicts between Python and Prisma
- Simpler deployment (no alembic migrations for run tables)

### 2. YouGov First Search Order
**Decision**: Search YouGov database before Nielsen for brand resolution.
**Why**:
- YouGov has perception data (KPIs) which is primary for allocation
- Nielsen brand names often differ (uppercase, abbreviations)
- YouGov brand_label used as canonical identifier

### 3. AI-Driven Resolution (No Static Mapping Tables)
**Decision**: Use GPT-4o-mini for industry/brand resolution instead of static mapping tables.
**Why**:
- Handles new brands automatically
- Handles misspellings and variations
- No maintenance of mapping tables
- More flexible for international expansion

### 4. Competitor Confirmation Gate
**Decision**: Pause after Stage 1 for user to confirm competitors (unless bypassed).
**Why**:
- User can add/remove competitors before allocation
- User sees what data is available (YouGov vs Nielsen)
- Prevents wasted LLM calls on wrong competitors

### 5. Stage 1 Skip Detection
**Decision**: If only "preference fields" changed (goal_text, budget, channels, brand_kpi), skip Stage 1.
**Why**:
- Changing budget doesn't require re-discovering competitors
- Saves 5-6 AI calls and database queries
- Faster reruns for preference changes

### 6. Shared Connection Pool for Background Tasks
**Decision**: Background tasks use `AsyncSessionLocal` from session.py instead of creating new engines.
**Why**:
- Prevents connection pool exhaustion
- Single pool_size=5, max_overflow=10 limit
- pool_pre_ping validates connections before use

### 7. Array Response Format for Search
**Decision**: Competitor search returns `results: [...]` array instead of flat object.
**Why**:
- Future support for multiple matches
- No breaking changes when adding features
- Consistent with API best practices

---

## Known Limitations and Edge Cases

### Data Limitations
1. **YouGov data freshness**: Only as recent as last CSV import
2. **Nielsen data gaps**: Some brands have YouGov data but no Nielsen spend data
3. **German market only**: Current data is Germany-specific (wirtschaftsgruppe, German month names)

### Technical Limitations
1. **Rate limit**: 20 runs per hour per session (HTTP 429 after)
2. **LLM timeout**: 45 seconds per call (circuit opens after 3 failures)
3. **Debug files**: Only saved when `STAGE1_DEBUG_MODE=True`
4. **Connection pool**: Max 15 connections (pool_size=5 + max_overflow=10)

### Edge Cases
1. **Brand not found**: Falls back to proxy brand scoring (AI calls #3-4)
2. **Empty competitor list**: Stage 1 fails if no competitors found
3. **Missing Nielsen data**: Competitor included with has_nielsen_data=False warning
4. **Rerun with same inputs**: Stage 1 skipped, uses cached competitors
5. **Concurrent runs**: Each run gets own background task, shares connection pool
6. **Session cache**: `lru_cache` on settings can cause stale config values (restart server after .env changes)

### Channel Mapping
```python
NIELSEN_TO_UI_CHANNEL = {
    "FERNSEHEN": "TV",
    "ONLINE": "Digital",
    "PLAKAT": "OOH",
    "RADIO": "Radio",
    "ZEITUNGEN": "Print",
    "ZEITSCHRIFTEN": "Print",
    "AT-RETAIL-MEDIA": "Retail Media",
    "KINO": "Cinema",
    "MOBILE": "Digital",
    "ADDRESSABLE TV": "TV",
    "AUDIO": "Radio",
}
```

### Status Transitions
```
pending → matching → awaiting_confirmation → generating → parsing → completing → completed
                 ↓                                    ↓              ↓
              failed ←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←
                 ↓
            cancelled (from awaiting_confirmation only)
```

### Progress Percentages
| Status | Progress | Stage |
|--------|----------|-------|
| pending | 0% | - |
| matching | 10% | S1 |
| awaiting_confirmation | 30% | - |
| generating | 40% | S2 |
| parsing | 70% | S3 |
| completing | 90% | S4 |
| completed | 100% | - |

---

## Testing

### Server Startup
```bash
cd C:\Users\Mohit\allocate-ai
python -m uvicorn src.main:app --host 127.0.0.1 --port 8003 --reload
```

### Example API Calls
```bash
# Start a run
curl -X POST "http://127.0.0.1:8003/api/v1/runs" \
  -H "Content-Type: application/json" \
  -H "X-Session-Token: test-session-12345" \
  -d '{"run_id": 1, "action": "start"}'

# Poll status
curl "http://127.0.0.1:8003/api/v1/runs/1/status" \
  -H "X-Session-Token: test-session-12345"

# Confirm competitors
curl -X POST "http://127.0.0.1:8003/api/v1/runs/1/competitors/confirm" \
  -H "Content-Type: application/json" \
  -H "X-Session-Token: test-session-12345" \
  -d '{"run_id": 1, "action": "approved"}'

# Get result
curl "http://127.0.0.1:8003/api/v1/runs/1/result" \
  -H "X-Session-Token: test-session-12345"

# Search competitor
curl "http://127.0.0.1:8003/api/v1/runs/1/competitors/search?q=Muller" \
  -H "X-Session-Token: test-session-12345"
```

### Debug Files
When `STAGE1_DEBUG_MODE=True`, debug files are saved to `debug_output/run_{id}.zip`:
- `FINAL_filtered_data.json` - Combined competitor data
- `S2_prompt.txt` - Full LLM prompt
- `S2_llm_response.txt` - Raw LLM response
- `S2_parsed_raw.json` - Parsed JSON before post-processing
- `S2_final_result.json` - Final allocation result
- `Y1_brands_for_sector.json` - YouGov brands in sector
- `Y2_nielsen_brands.json` - Nielsen brands in industry
- `N1_brand_matching.json` - Nielsen brand resolution
