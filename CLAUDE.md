# Project Notes for Claude Code

## Temp/scratch files

- When creating temporary scripts or output files (DB queries, API tests, debugging):
  - Put them in `C:\tmp\` (outside the repo) whenever possible.
  - If a scratch file must live inside the repo, add it to `.gitignore` immediately.
  - Delete all scratch files once the task is done.

## Environment

- Server runs on port 8003: `STAGE1_DEBUG_MODE=True python -m uvicorn src.main:app --host 127.0.0.1 --port 8003 > server.log 2>&1 &` (no --reload; restart after code changes).
- Kill processes with `taskkill //F //PID <pid> //T` (pkill does not work in git-bash on Windows).
- API auth header: `X-Session-Token: test-session-12345`.
- DB scripts need `sys.path.insert(0, r"C:\Users\Mohit\allocate-ai")` and `await engine.dispose()` in a finally block (engine pool is bound to the first event loop).
- Do not modify DB rows without asking the user first.
