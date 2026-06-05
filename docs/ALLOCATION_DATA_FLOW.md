# Allocation Result Data Flow

## Complete Flow: From LLM Response to API Response

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         STAGE 2: LLM GENERATION                              │
│                                                                              │
│   OpenAI GPT-4 generates JSON:                                               │
│   {                                                                          │
│     "allocations": [                                                         │
│       {"channel": "TV", "percentage": 40, "rationale": "High reach..."},     │
│       {"channel": "Digital", "percentage": 30, "rationale": "Targeting..."}  │
│     ],                                                                       │
│     "summary": "Strategy focuses on...",                                     │
│     "confidence": 0.85                                                       │
│   }                                                                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    STAGE 3: OUTPUT PARSING SERVICE                           │
│                    src/services/mediamix/output_parsing.py                   │
│                                                                              │
│   OutputParsingService.parse_and_store():                                    │
│   1. Parse JSON from LLM response                                            │
│   2. Validate each allocation (channel name, percentage, amount)             │
│   3. Check percentages sum to 100%                                           │
│   4. Extract summary and confidence                                          │
│   5. Collect validation issues (errors/warnings)                             │
│   6. Convert to storage format                                               │
│   7. Save to database via AllocationResultRepository                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         DATABASE: allocation_results                         │
│                         src/db/models/run.py                                 │
│                                                                              │
│   Table: allocation_results                                                  │
│   ┌──────────────────┬────────────────────────────────────────────────────┐ │
│   │ Column           │ Value Example                                      │ │
│   ├──────────────────┼────────────────────────────────────────────────────┤ │
│   │ id               │ 1                                                  │ │
│   │ run_id           │ 24 (FK to runs table)                              │ │
│   │ allocations      │ JSON (see below)                                   │ │
│   │ summary          │ "The strategy prioritizes high-reach..."          │ │
│   │ confidence_score │ 0.85                                               │ │
│   │ raw_response     │ Full LLM JSON string                               │ │
│   │ is_valid         │ true                                               │ │
│   │ validation_errors│ JSON with any issues                               │ │
│   │ created_at       │ 2026-05-26T17:06:30Z                               │ │
│   └──────────────────┴────────────────────────────────────────────────────┘ │
│                                                                              │
│   allocations JSON structure:                                                │
│   {                                                                          │
│     "channels": [                                                            │
│       {                                                                      │
│         "name": "TV",                                                        │
│         "percentage": 40.0,                                                  │
│         "amount": 400000.00,      // Calculated from budget                  │
│         "rationale": "High reach among target demographic..."               │
│       },                                                                     │
│       {                                                                      │
│         "name": "Digital",                                                   │
│         "percentage": 30.0,                                                  │
│         "amount": 300000.00,                                                 │
│         "rationale": "Precision targeting capabilities..."                  │
│       }                                                                      │
│     ],                                                                       │
│     "total_percentage": 100.0                                                │
│   }                                                                          │
│                                                                              │
│   validation_errors JSON structure (if any):                                 │
│   {                                                                          │
│     "issues": [                                                              │
│       {                                                                      │
│         "field": "allocations[2].channel",                                   │
│         "message": "Unknown channel 'Out-of-home'",                          │
│         "severity": "warning"                                                │
│       }                                                                      │
│     ]                                                                        │
│   }                                                                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         API ENDPOINT: GET /runs/{id}/result                  │
│                         src/api/v1/results.py                                │
│                                                                              │
│   1. Fetch run from runs table                                               │
│   2. Verify session/ownership                                                │
│   3. Check status == 'completed'                                             │
│   4. Fetch AllocationResult from allocation_results table                    │
│   5. Parse allocations JSON → ChannelAllocation objects                      │
│   6. Extract warnings from validation_errors                                 │
│   7. Return AllocationResultResponse                                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         API RESPONSE                                         │
│                                                                              │
│   {                                                                          │
│     "run_id": 24,                                                            │
│     "allocations": [                                                         │
│       {                                                                      │
│         "channel": "TV",                                                     │
│         "share_pct": 40.0,                                                   │
│         "budget_gross_eur": 400000.00,                                       │
│         "reasoning": "High reach among target demographic..."               │
│       },                                                                     │
│       {                                                                      │
│         "channel": "Digital",                                                │
│         "share_pct": 30.0,                                                   │
│         "budget_gross_eur": 300000.00,                                       │
│         "reasoning": "Precision targeting capabilities..."                  │
│       }                                                                      │
│     ],                                                                       │
│     "total_budget_eur": 1000000.00,                                          │
│     "reasoning_summary": "The strategy prioritizes...",                      │
│     "confidence_score": 0.85,                                                │
│     "warnings": ["Unknown channel 'Out-of-home'"],                           │
│     "is_cached": false,                                                      │
│     "created_at": "2026-05-26T17:06:30Z",                                    │
│     "updated_at": "2026-05-26T17:06:30Z"                                     │
│   }                                                                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                   JS BACKEND CACHES IN ProjectVersionAiRun                   │
│                   (Prisma model)                                             │
│                                                                              │
│   ProjectVersionAiRun.allocationResult = {                                   │
│     // Copy of the entire API response                                       │
│   }                                                                          │
│                                                                              │
│   This allows JS Backend to serve cached results without calling Python.     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Database Tables Summary

### Python Tables (SQLAlchemy)

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `runs` | Tracks run lifecycle | id, status, customer_name, industry, confirmed_competitors |
| `allocation_results` | Stores AI output | run_id (FK), allocations (JSON), summary, confidence_score |
| `chat_history` | Feedback & chat | run_id (FK), message_type, content, extra_data |
| `prompt_traces` | LLM debugging | run_id (FK), prompt, response, tokens |

### Prisma Tables (JS Backend)

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `users` | User accounts | id (UUID), email, name, role |
| `projects` | Campaign groups | id (UUID), name, currentVersionId |
| `project_versions` | Version inputs | id (UUID), customer, industry, brandKpi, mediaChannels |
| `project_version_ai_runs` | Bridge + cache | externalRunId (→ runs.id), allocationResult (JSON cache) |

---

## Data Storage Locations

### Where is the allocation stored?

```
PRIMARY STORAGE (Python):
  allocation_results table
    └── allocations (JSON column)
        └── {channels: [{name, percentage, amount, rationale}]}

CACHE/SNAPSHOT (Prisma):
  project_version_ai_runs table
    └── allocationResult (JSON column)
        └── Full copy of GET /runs/{id}/result response
```

### Where are competitors stored?

```
PRIMARY STORAGE (Python):
  runs table
    └── confirmed_competitors (JSON column)
        ├── stage1_result: {yougov_sectors, nielsen_sectors, competitors}
        ├── confirmed_brands: ["Brand1", "Brand2"]
        └── brands: ["Brand1", "Brand2"]  // Updated by chat agent

CACHE/SNAPSHOT (Prisma):
  project_version_ai_runs table
    ├── confirmedCompetitors: ["Brand1", "Brand2"]
    └── competitorSnapshot: Full competitor data with metrics
```

### Where is chat/feedback stored?

```
PRIMARY STORAGE (Python):
  chat_history table
    ├── Feedback cards (message_type: warning, summary, recommendation)
    └── Interactive chat (message_type: chat, extra_data: {role, tool_used, changes_made})

CACHE/SNAPSHOT (Prisma):
  project_version_ai_runs table
    └── chatSnapshot: Full copy of GET /runs/{id}/chat response
```

---

## Code File Locations

| Component | File |
|-----------|------|
| LLM Client | `src/services/llm_gateway/client.py` |
| Output Parsing | `src/services/mediamix/output_parsing.py` |
| Result Repository | `src/repositories/run.py` (AllocationResultRepository) |
| Result Model | `src/db/models/run.py` (AllocationResult class) |
| Result API | `src/api/v1/results.py` |
| Response Schema | `src/api/schemas/result.py` |

---

## Validation Rules

The OutputParsingService validates:

1. **Schema**: Response must be valid JSON with `allocations` array
2. **Channel names**: Warns if unknown channel (doesn't reject)
3. **Percentages**: Must sum to 100% (±1% tolerance, auto-adjusted)
4. **Percentage range**: Each allocation 0-100%
5. **Count**: Minimum 1 channel, maximum 20 channels
6. **Confidence**: Must be 0-1 range
7. **Amounts**: Calculated from budget × percentage if not provided

Valid channels:
```
TV, Digital, Print, Radio, OOH, Cinema,
Social Media, Search, Display, Video, Audio,
Programmatic, Native, Influencer, Sponsorship,
Direct Mail, Email, Mobile, Connected TV, Podcast
```
