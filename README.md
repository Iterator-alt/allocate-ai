# Allocate.AI Backend

AI-powered media budget allocation using OpenAI GPT-4o.

## Quick Setup

### 1. Prerequisites
- Python 3.11+
- PostgreSQL
- OpenAI API key

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Environment
```bash
cp .env.example .env
```

Edit `.env`:
```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/allocate_ai
OPENAI_API_KEY=sk-your-key-here
```

### 4. Setup Database
```bash
# Create database
psql -U postgres -c "CREATE DATABASE allocate_ai;"

# Run migrations
alembic upgrade head
```

### 5. Start Server
```bash
uvicorn src.main:app --host 0.0.0.0 --port 8002
```

Server runs at: http://localhost:8002

---

## API Testing (Postman)

### Import Collection
Import `postman/Allocate_AI_API_Collection.postman_collection.json`

### Test Flow

**Step 1: Health Check**
```
GET http://localhost:8002/health
```

**Step 2: Create Run**
```
POST http://localhost:8002/api/v1/runs
Header: X-Session-Token: my-test-session-123
Body:
{
  "customer_name": "BMW Group",
  "industry": "Automotive",
  "brand_kpi": "adaware",
  "total_budget": 4000000
}
```
Note the `id` in response.

**Step 3: Process with AI**
```
POST http://localhost:8002/api/v1/runs/{id}/process-ai
Header: X-Session-Token: my-test-session-123
```
Wait 5-15 seconds for AI response.

**Step 4: Get Results**
```
GET http://localhost:8002/api/v1/runs/{id}/result
Header: X-Session-Token: my-test-session-123
```

**Step 5: Get Feedback**
```
GET http://localhost:8002/api/v1/runs/{id}/chat
Header: X-Session-Token: my-test-session-123
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /health | Health check |
| POST | /api/v1/runs | Create new run |
| GET | /api/v1/runs/{id}/status | Check run status |
| POST | /api/v1/runs/{id}/process-ai | Process with real AI |
| GET | /api/v1/runs/{id}/result | Get allocation result |
| GET | /api/v1/runs/{id}/chat | Get feedback cards |
| GET | /api/v1/runs/{id}/trace | Get prompt traces (owner only) |

---

## Run Tests
```bash
pytest
```
