# AllocateAI Data Model: Prisma (JS Backend) ↔ Python (AI Backend)

## Overview

The system has TWO backends sharing ONE database:
- **JS Backend (Prisma)** - Handles user management, projects, and version tracking
- **Python Backend (SQLAlchemy)** - Handles AI runs, allocation results, and chat

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND (React)                                │
└─────────────────────────────────────────────────────────────────────────────┘
                    │                                    │
                    ▼                                    ▼
┌──────────────────────────────┐     ┌──────────────────────────────────────┐
│      JS BACKEND (Prisma)     │     │     PYTHON AI BACKEND (SQLAlchemy)   │
│                              │     │                                      │
│  • User authentication       │     │  • Stage 1: Competitor matching      │
│  • Project CRUD              │     │  • Stage 2-4: AI allocation          │
│  • Version management        │     │  • Chat agent                        │
│  • UI state                  │     │  • Feedback generation               │
└──────────────────────────────┘     └──────────────────────────────────────┘
                    │                                    │
                    └──────────────┬─────────────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │     PostgreSQL Database       │
                    │                              │
                    │  • users (Prisma)            │
                    │  • projects (Prisma)         │
                    │  • project_versions (Prisma) │
                    │  • project_version_ai_runs   │
                    │    (Prisma - links to runs)  │
                    │  • runs (Python)             │
                    │  • allocation_results (Py)   │
                    │  • chat_history (Python)     │
                    │  • yougov (Python data)      │
                    │  • nielsen (Python data)     │
                    └──────────────────────────────┘
```

---

## Entity Relationship Diagram

```
┌─────────────┐
│    User     │
│  (Prisma)   │
└─────────────┘
       │ 1
       │
       │ creates
       ▼ *
┌─────────────┐
│   Project   │
│  (Prisma)   │
└─────────────┘
       │ 1
       │
       │ has many
       ▼ *
┌─────────────────┐       currentVersionId      ┌─────────────┐
│ ProjectVersion  │◄────────────────────────────│   Project   │
│    (Prisma)     │                             └─────────────┘
└─────────────────┘
       │ 1
       │
       │ has many
       ▼ *
┌─────────────────────┐
│ ProjectVersionAiRun │──────── externalRunId ──────►┌─────────────┐
│      (Prisma)       │                              │     Run     │
│                     │◄─────────────────────────────│  (Python)   │
└─────────────────────┘   project_version_id FK      └─────────────┘
                                                            │ 1
                                                            │
                              ┌──────────────────┬──────────┴──────────┐
                              │                  │                     │
                              ▼ 1                ▼ *                   ▼ *
                    ┌──────────────────┐ ┌──────────────┐    ┌──────────────┐
                    │ AllocationResult │ │ ChatHistory  │    │ PromptTrace  │
                    │     (Python)     │ │   (Python)   │    │   (Python)   │
                    └──────────────────┘ └──────────────┘    └──────────────┘
```

---

## Table Details

### 1. User (Prisma - JS Backend)

| Field     | Type     | Description |
|-----------|----------|-------------|
| id        | UUID     | Primary key |
| email     | String   | Unique email |
| password  | String   | Hashed password |
| name      | String   | Display name |
| role      | Enum     | member, owner, super_admin |
| createdAt | DateTime | Created timestamp |
| updatedAt | DateTime | Updated timestamp |

**Roles:**
- `member` - Regular user
- `owner` - Can manage team
- `super_admin` - Full access

---

### 2. Project (Prisma - JS Backend)

| Field            | Type     | Description |
|------------------|----------|-------------|
| id               | UUID     | Primary key |
| name             | String   | Project name (e.g., "Ehrmann Q1 Campaign") |
| status           | Enum     | active, deleted |
| currentVersionId | UUID?    | FK to current ProjectVersion |
| createdById      | UUID?    | FK to User |
| createdAt        | DateTime | Created timestamp |

**Purpose:** Groups multiple versions of a campaign together.

---

### 3. ProjectVersion (Prisma - JS Backend)

| Field         | Type     | Description |
|---------------|----------|-------------|
| id            | UUID     | Primary key |
| projectId     | UUID     | FK to Project |
| versionNumber | Int      | Auto-incrementing (1, 2, 3...) |
| versionName   | String   | Display name (e.g., "v1", "Budget Increase") |
| customer      | String   | Brand name (e.g., "Ehrmann Almighurt") |
| industry      | String   | Industry (e.g., "Lebensmittel") |
| brandKpi      | Enum     | adaware, aware, consider |
| mediaChannels | String[] | Selected channels ["TV", "Digital", "OOH"] |
| goalMode      | Enum     | budget, goal |
| goalText      | String   | Goal description |
| status        | Enum     | active, superseded, deleted |

**Purpose:** Stores campaign inputs. Each version can have multiple AI runs.

**Version Status:**
- `active` - Current working version
- `superseded` - Old version (new one created)
- `deleted` - Soft deleted

---

### 4. ProjectVersionAiRun (Prisma - JS Backend)

**THE BRIDGE TABLE** - Links Prisma's ProjectVersion to Python's Run.

| Field                | Type     | Description |
|----------------------|----------|-------------|
| id                   | UUID     | Primary key |
| projectVersionId     | UUID     | FK to ProjectVersion |
| **externalRunId**    | Int?     | **FK to Python's runs.id** |
| status               | String   | Mirrors Python run status |
| stage                | String?  | S1, S1.5, S2, S3, S4 |
| progressPct          | Int?     | 0-100 |
| progressMessage      | String?  | Human-readable progress |
| queuePosition        | Int?     | Position in queue |
| etaSeconds           | Int?     | Estimated time |
| startedAt            | DateTime?| When run started |
| completedAt          | DateTime?| When run completed |
| errorMessage         | String?  | Error if failed |
| confirmedCompetitors | String[] | Snapshot of confirmed competitors |
| competitorSnapshot   | Json?    | Full competitor data snapshot |
| allocationResult     | Json?    | Snapshot of final allocation |
| chatSnapshot         | Json?    | Snapshot of feedback cards |
| traceSnapshot        | Json?    | Snapshot of LLM traces |
| statusPayload        | Json?    | Raw status response |
| rawPayload           | Json?    | Raw result response |

**Purpose:**
1. Links a ProjectVersion to a Python Run via `externalRunId`
2. Caches/snapshots results from Python backend
3. Tracks run progress for UI

---

### 5. Run (Python - AI Backend)

| Field                | Type     | Description |
|----------------------|----------|-------------|
| id                   | Int      | Primary key (auto-increment) |
| session_token        | String   | Session identifier |
| user_id              | Int?     | FK to users (Prisma) |
| **project_id**       | Int?     | FK to projects (Prisma) |
| **project_version_id** | Int?   | FK to project_versions (Prisma) |
| customer_name        | String   | Brand name |
| industry             | String   | Industry |
| brand_kpi            | String   | adaware, aware, consider |
| total_budget         | Decimal? | Total budget in EUR |
| input_parameters     | JSON?    | {channels, direction, goal_text} |
| status               | String   | pending → matching → awaiting_confirmation → generating → completed |
| confirmed_competitors| JSON?    | Stage 1 results + confirmed brands |
| error_message        | String?  | Error if failed |

**Status Flow:**
```
pending → matching → awaiting_confirmation → generating → parsing → feedback → completed
                                                                              ↓
                                                                           failed
```

---

### 6. AllocationResult (Python - AI Backend)

| Field            | Type    | Description |
|------------------|---------|-------------|
| id               | Int     | Primary key |
| run_id           | Int     | FK to runs (unique - one result per run) |
| allocations      | JSON    | {channels: [{name, percentage, amount, rationale}]} |
| summary          | String? | Strategy summary |
| confidence_score | Decimal?| 0.00-1.00 |
| raw_response     | String? | Raw LLM JSON response |
| is_valid         | Bool    | Validation passed |
| validation_errors| JSON?   | {issues: [{field, message, severity}]} |

---

### 7. ChatHistory (Python - AI Backend)

| Field        | Type    | Description |
|--------------|---------|-------------|
| id           | Int     | Primary key |
| run_id       | Int     | FK to runs |
| message_type | String  | warning, alert, summary, recommendation, info, **chat** |
| severity     | String  | info, warning, error |
| title        | String  | Message title |
| content      | String  | Message body |
| extra_data   | JSON?   | {role, tool_used, changes_made} for chat messages |
| display_order| Int     | Sort order |

**Message Types:**
- `warning` - Data quality notices (yellow cards)
- `alert` - Critical issues (red cards)
- `summary` - Allocation strategy summary
- `recommendation` - Actionable suggestions
- `info` - General info
- `chat` - Interactive chat agent messages

**For Chat Messages (extra_data):**
```json
{
    "role": "user" | "agent",
    "tool_used": "competitor_add" | "competitor_remove" | "edit_input" | "rerun" | null,
    "changes_made": [
        {"type": "competitor_add", "brand": "Müller"},
        {"type": "edit", "field": "total_budget", "old": 100000, "new": 500000}
    ]
}
```

---

## How They Connect: Complete Flow

### 1. User Creates Project (JS Backend)
```
User → creates → Project → creates → ProjectVersion (v1)
                              ↓
                    Stores: customer, industry, brandKpi,
                           mediaChannels, goalMode, goalText
```

### 2. User Starts AI Run (JS → Python)
```
JS Backend:
1. Creates ProjectVersionAiRun record
2. Calls Python: POST /api/v1/runs
   - Passes: customer_name, industry, brand_kpi, total_budget, channels, etc.
   - Returns: run_id (Python's integer ID)
3. Saves run_id as externalRunId in ProjectVersionAiRun
```

### 3. Python Processes Run
```
Python Run goes through stages:
1. MATCHING (Stage 1) - Find competitors in YouGov/Nielsen
2. AWAITING_CONFIRMATION - User reviews competitors
3. GENERATING (Stage 2) - LLM generates allocation
4. PARSING (Stage 3) - Parse and validate LLM output
5. FEEDBACK (Stage 4) - Generate feedback cards
6. COMPLETED - Done!
```

### 4. JS Backend Polls Status
```
JS Backend periodically:
1. Calls: GET /api/v1/runs/{externalRunId}/status
2. Updates ProjectVersionAiRun with:
   - status, stage, progressPct, progressMessage
3. When completed, fetches:
   - GET /api/v1/runs/{id}/result → saves to allocationResult
   - GET /api/v1/runs/{id}/chat → saves to chatSnapshot
   - GET /api/v1/runs/{id}/competitors → saves to competitorSnapshot
```

### 5. User Makes Changes via Chat
```
1. POST /api/v1/chat/message with run_id
2. Python Chat Agent:
   - Loads context from run + chat_history
   - Executes intent (add competitor, edit budget, rerun)
   - Updates run.confirmed_competitors
   - Saves message to chat_history with extra_data
3. If rerun triggered:
   - Creates NEW run OR updates existing
   - JS Backend creates new ProjectVersionAiRun
```

### 6. User Creates New Version
```
1. User clicks "Save as New Version"
2. JS Backend:
   - Creates new ProjectVersion (v2) with modified inputs
   - Creates new ProjectVersionAiRun
   - Calls Python to start new run
3. Old version status → superseded
4. Project.currentVersionId → new version
```

---

## Key Relationships Summary

| From (Prisma)       | To (Prisma/Python)       | Relationship | Via |
|---------------------|--------------------------|--------------|-----|
| User                | Project                  | 1:many       | createdById |
| Project             | ProjectVersion           | 1:many       | projectId |
| Project             | ProjectVersion (current) | 1:1          | currentVersionId |
| ProjectVersion      | ProjectVersionAiRun      | 1:many       | projectVersionId |
| **ProjectVersionAiRun** | **Run (Python)**     | **1:1**      | **externalRunId** |
| Run (Python)        | AllocationResult         | 1:1          | run_id |
| Run (Python)        | ChatHistory              | 1:many       | run_id |
| Run (Python)        | PromptTrace              | 1:many       | run_id |

---

## What JS Backend Needs from Python Backend

### API Endpoints Used

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/runs` | POST | Create new run (returns run_id) |
| `/api/v1/runs/start` | POST | Start existing run by ID |
| `/api/v1/runs/{id}/status` | GET | Poll run status |
| `/api/v1/runs/{id}` | GET | Get full run details |
| `/api/v1/runs/{id}/competitors` | GET | Get matched competitors |
| `/api/v1/runs/{id}/competitors/confirm` | POST | Confirm competitors |
| `/api/v1/runs/competitors/confirm` | POST | Alt confirm (run_id in body) |
| `/api/v1/runs/{id}/result` | GET | Get allocation result |
| `/api/v1/runs/{id}/chat` | GET | Get feedback cards + chat |
| `/api/v1/chat/message` | POST | Interactive chat |

### Data to Cache in ProjectVersionAiRun

1. **confirmedCompetitors** - List of brand names after Stage 1.5
2. **competitorSnapshot** - Full competitor data with metrics
3. **allocationResult** - Final channel allocations
4. **chatSnapshot** - All feedback cards and chat messages
5. **traceSnapshot** - LLM prompts and responses (for debugging)

---

## Version Control Strategy

### When to Create New Version
1. User explicitly clicks "Save as New Version"
2. Major input changes (budget > 20% change, different KPI, etc.)
3. User wants to compare different scenarios

### When to Rerun Same Version
1. Minor tweaks via chat (add/remove competitor)
2. Small budget adjustments
3. Goal text refinements

### Chat History Sharing
- Chat history is tied to `run_id`
- For same project, context can be loaded across versions via `project_id`
- Context loader pulls last 20 messages from all runs in same project

---

## Manager Summary

**For Manager/PM:**

1. **Projects** = Campaigns (e.g., "Ehrmann Q1 2026")
2. **Versions** = Iterations of a campaign with different inputs
3. **AI Runs** = Each time the AI generates an allocation
4. **Competitors** = Brands used for benchmarking (from YouGov/Nielsen data)
5. **Allocations** = Final budget recommendations per channel

**Key Metrics in ProjectVersionAiRun:**
- `progressPct` - How far along is the AI?
- `stage` - Which pipeline stage?
- `confirmedCompetitors` - Who are we comparing against?
- `allocationResult` - The final recommendations

**Status Values:**
- `pending` - Queued
- `matching` - Finding competitors
- `awaiting_confirmation` - User needs to approve competitors
- `generating` - AI working
- `completed` - Done!
- `failed` - Error occurred
