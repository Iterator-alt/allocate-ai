# API Refactor - May 27, 2026

## Summary

Refactored the API to align with frontend team expectations. Key changes:
1. `POST /runs` now accepts `{run_id, action: "start"}` format
2. Competitor confirmation is bypassed by default (auto-approve)
3. Allocation results are stored in `ProjectVersionAiRun.allocationResult`
4. Results endpoint reads from Prisma table first

---

## Changes Made

### CHANGE 1: POST /api/v1/runs - New Request Format

**Before:**
```json
{
  "customer_name": "Ehrmann Almighurt",
  "industry": "Lebensmittel",
  "brand_kpi": "adaware",
  "total_budget": 1000000,
  "input_parameters": { ... }
}
```

**After:**
```json
{
  "run_id": 1,
  "action": "start"
}
```

**Where:**
- `run_id` = `externalRunId` from `ProjectVersionAiRun` (set by JS Backend)
- `action` = must be `"start"`

**Flow:**
1. Look up `ProjectVersionAiRun` WHERE `externalRunId = run_id`
2. Get `projectVersionId` from that record
3. Look up `ProjectVersion` WHERE `id = projectVersionId`
4. Extract campaign inputs:
   - `customer` → `customer_name`
   - `industry` → `industry`
   - `brandKpi` → `brand_kpi`
   - `mediaChannels` → `channels`
   - `goalMode` → `direction`
   - `goalText` → `goal_text` (budget extracted via regex)
5. Create internal `Run` record with extracted values
6. Start Stage 1 in background

**Response:**
```json
{
  "run_id": 25,
  "status": "started",
  "error_message": null
}
```

---

### CHANGE 2: Bypass Competitor Confirmation

**Config Flag:**
```python
# src/config.py
bypass_competitor_confirmation: bool = True  # Default: True
```

**Behavior when True:**
- After Stage 1 completes, all competitors are auto-approved
- `competitorSnapshot` written to `ProjectVersionAiRun` (full objects)
- `confirmedCompetitors` written to `ProjectVersionAiRun` (string array)
- Proceeds directly to Stage 2-4 without user input

**Status Flow (bypass enabled):**
```
pending → matching (S1) → generating (S2) → parsing (S3) → feedback (S4) → completed
```

**Status Flow (bypass disabled):**
```
pending → matching (S1) → awaiting_confirmation (S1.5) → [confirm] → generating (S2) → ...
```

---

### CHANGE 3: Store Allocation Result in DB

After Stage 2-4 completes:
1. Full allocation result JSON stored in `ProjectVersionAiRun.allocationResult`
2. Format matches the API response:

```json
{
  "run_id": 25,
  "allocations": [
    {
      "channel": "TV",
      "share_pct": 40.0,
      "budget_gross_eur": 400000.00,
      "reasoning": "High reach among target demographic..."
    }
  ],
  "total_budget_eur": 1000000.00,
  "reasoning_summary": "Strategy focuses on...",
  "confidence_score": 0.85,
  "warnings": [],
  "is_cached": false,
  "created_at": "2026-05-27T10:00:00Z",
  "updated_at": "2026-05-27T10:00:00Z"
}
```

**GET /runs/{id}/result:**
- First tries to read from `ProjectVersionAiRun.allocationResult`
- Falls back to `allocation_results` table if not found

---

### CHANGE 4: Run Status Response

All fields documented and returned correctly:

```json
{
  "id": 25,
  "status": "completed",
  "stage": null,
  "progress_pct": 100,
  "started_at": "2026-05-27T10:00:00Z",
  "completed_at": "2026-05-27T10:01:30Z",
  "error_message": null,
  "progress": "Completed",
  "queue_position": null,
  "eta_seconds": null
}
```

**Status Values:**
- `pending` - After run created, before S1 starts
- `matching` - S1 in progress (competitor matching)
- `awaiting_confirmation` - S1.5 (bypassed when flag is True)
- `generating` - S2 in progress (LLM allocation)
- `parsing` - S3 in progress (parsing LLM response)
- `feedback` - S4 in progress (generating chat feedback)
- `completed` - All stages done
- `failed` - Any stage errored
- `cancelled` - User cancelled

---

### CHANGE 5: Endpoint Summary

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/runs` | POST | Start run with `{run_id, action: "start"}` |
| `/api/v1/runs/{id}/status` | GET | Poll run status |
| `/api/v1/runs/{id}/result` | GET | Get allocation result |
| `/api/v1/runs/{id}/competitors` | GET | Get matched competitors |
| `/api/v1/runs/{id}/competitors/confirm` | POST | Manual confirm (bypass disabled) |
| `/api/v1/runs/competitors/confirm` | POST | V2 confirm with run_id in body |
| `/api/v1/runs/{id}/stop` | POST | Cancel run |
| `/api/v1/runs/{id}/chat` | GET | Get feedback cards |
| `/api/v1/chat/message` | POST | Chat agent interaction |
| `/health` | GET | Health check |

---

## Files Modified

| File | Changes |
|------|---------|
| `src/config.py` | Added `bypass_competitor_confirmation` flag |
| `src/api/v1/runs.py` | Refactored POST /runs, added auto-approval logic, Prisma writes |
| `src/api/v1/results.py` | Read from ProjectVersionAiRun.allocationResult first |
| `src/api/v1/competitors.py` | Pass prisma_ai_run_id to AI generation |
| `src/api/schemas/runs.py` | Updated StartRunRequest docs |
| `src/db/models/prisma_tables.py` | NEW - SQLAlchemy models for Prisma tables |
| `src/db/models/__init__.py` | Export Prisma models |
| `AllocateAI_Ehrmann_Test_Collection.json` | Updated Postman collection |

---

## Budget Extraction

Budget is extracted from `goalText` using regex. Supported formats:
- `"€2M budget"` → 2,000,000
- `"2M EUR"` → 2,000,000
- `"€500K"` → 500,000
- `"500000 euros"` → 500,000
- `"budget of 1,000,000"` → 1,000,000

If no budget found, `total_budget` is set to `null` and Stage 2 handles it.

---

## Environment Variables

```bash
# Enable/disable competitor confirmation bypass
BYPASS_COMPETITOR_CONFIRMATION=true  # Default: true

# Existing vars
DATABASE_URL=postgresql+asyncpg://...
OPENAI_API_KEY=sk-...
```

---

## Testing

1. Ensure `ProjectVersion` and `ProjectVersionAiRun` tables exist in the database
2. Create a `ProjectVersionAiRun` record with `externalRunId` set
3. Call `POST /runs` with `{"run_id": <externalRunId>, "action": "start"}`
4. Poll `/runs/{id}/status` until `completed`
5. Get results from `/runs/{id}/result`

**Example:**
```bash
# Start a run
curl -X POST http://127.0.0.1:8082/api/v1/runs \
  -H "Content-Type: application/json" \
  -H "X-Session-Token: test-session" \
  -d '{"run_id": 1, "action": "start"}'

# Poll status
curl http://127.0.0.1:8082/api/v1/runs/25/status \
  -H "X-Session-Token: test-session"

# Get result
curl http://127.0.0.1:8082/api/v1/runs/25/result \
  -H "X-Session-Token: test-session"
```
