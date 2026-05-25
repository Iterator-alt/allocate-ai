#!/usr/bin/env python3
"""
Manual verification script for JS Backend integration endpoints.

Run with: python scripts/test_js_backend_integration.py

Requires the server to be running on localhost:8081.
"""

import requests
import json
import sys

BASE_URL = "http://127.0.0.1:8081/api/v1"
SESSION_TOKEN = "test-session-integration"

def print_result(test_name: str, passed: bool, details: str = ""):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status}: {test_name}")
    if details:
        print(f"       {details}")


def test_start_endpoint_exists():
    """Test that POST /runs/start endpoint exists."""
    # We don't have a real run, but we can test that the endpoint accepts POST
    response = requests.post(
        f"{BASE_URL}/runs/start",
        json={"run_id": 99999, "action": "start"},
        headers={"X-Session-Token": SESSION_TOKEN, "Content-Type": "application/json"},
    )

    # Should return 200 with error message (not 405 Method Not Allowed)
    if response.status_code == 200:
        data = response.json()
        # Expect error since run doesn't exist
        passed = data.get("status") == "error" and "not found" in data.get("error_message", "").lower()
        print_result(
            "POST /runs/start endpoint exists",
            passed,
            f"Response: {json.dumps(data)}"
        )
        return passed
    else:
        print_result(
            "POST /runs/start endpoint exists",
            False,
            f"Got status {response.status_code}: {response.text}"
        )
        return False


def test_start_response_format():
    """Test that /runs/start response matches expected format."""
    response = requests.post(
        f"{BASE_URL}/runs/start",
        json={"run_id": 1, "action": "start"},
        headers={"X-Session-Token": SESSION_TOKEN, "Content-Type": "application/json"},
    )

    if response.status_code == 200:
        data = response.json()
        has_run_id = "run_id" in data
        has_status = "status" in data
        has_error_message = "error_message" in data

        passed = has_run_id and has_status and has_error_message
        print_result(
            "POST /runs/start response format",
            passed,
            f"Fields: run_id={has_run_id}, status={has_status}, error_message={has_error_message}"
        )
        return passed
    else:
        print_result(
            "POST /runs/start response format",
            False,
            f"Got status {response.status_code}"
        )
        return False


def test_confirm_v2_endpoint_exists():
    """Test that POST /runs/competitors/confirm endpoint exists."""
    response = requests.post(
        f"{BASE_URL}/runs/competitors/confirm",
        json={"run_id": 99999, "action": "approved"},
        headers={"X-Session-Token": SESSION_TOKEN, "Content-Type": "application/json"},
    )

    # Should return 404 (run not found), not 405 (method not allowed)
    if response.status_code == 404:
        print_result(
            "POST /runs/competitors/confirm endpoint exists",
            True,
            "Returns 404 for non-existent run (endpoint works)"
        )
        return True
    else:
        print_result(
            "POST /runs/competitors/confirm endpoint exists",
            False,
            f"Got status {response.status_code}: {response.text}"
        )
        return False


def test_status_endpoint_fields():
    """Test that GET /runs/{id}/status returns required fields."""
    # First create a run
    create_response = requests.post(
        f"{BASE_URL}/runs",
        json={
            "customer_name": "TestBrand",
            "industry": "FMCG",
            "brand_kpi": "adaware",
        },
        headers={"X-Session-Token": SESSION_TOKEN, "Content-Type": "application/json"},
    )

    if create_response.status_code != 201:
        print_result(
            "GET /runs/{id}/status fields",
            False,
            f"Could not create run: {create_response.text}"
        )
        return False

    run_id = create_response.json()["id"]

    # Get status
    response = requests.get(
        f"{BASE_URL}/runs/{run_id}/status",
        headers={"X-Session-Token": SESSION_TOKEN},
    )

    if response.status_code == 200:
        data = response.json()

        # Check required fields for JS Backend
        required_fields = ["id", "status", "stage", "progress_pct", "progress"]
        missing = [f for f in required_fields if f not in data]

        passed = len(missing) == 0
        print_result(
            "GET /runs/{id}/status fields",
            passed,
            f"Fields present: {list(data.keys())}. Missing: {missing}" if missing else f"All required fields present"
        )

        # Additional check: stage and progress_pct values
        if passed:
            stage = data.get("stage")
            progress_pct = data.get("progress_pct")
            print(f"       stage={stage}, progress_pct={progress_pct}")

        return passed
    else:
        print_result(
            "GET /runs/{id}/status fields",
            False,
            f"Got status {response.status_code}"
        )
        return False


def test_full_flow():
    """Test the complete JS Backend integration flow."""
    print("\n--- Full Integration Flow Test ---")

    # Step 1: Create run
    create_response = requests.post(
        f"{BASE_URL}/runs",
        json={
            "customer_name": "IntegrationTestBrand",
            "industry": "Automotive",
            "brand_kpi": "adaware",
            "total_budget": 500000,
        },
        headers={"X-Session-Token": f"{SESSION_TOKEN}-flow", "Content-Type": "application/json"},
    )

    if create_response.status_code != 201:
        print_result("Step 1: Create run", False, f"Status {create_response.status_code}")
        return False

    run_id = create_response.json()["id"]
    print_result("Step 1: Create run", True, f"run_id={run_id}")

    # Step 2: Start the run
    start_response = requests.post(
        f"{BASE_URL}/runs/start",
        json={"run_id": run_id, "action": "start"},
        headers={"X-Session-Token": f"{SESSION_TOKEN}-flow", "Content-Type": "application/json"},
    )

    if start_response.status_code != 200:
        print_result("Step 2: Start run", False, f"Status {start_response.status_code}")
        return False

    start_data = start_response.json()
    passed = start_data.get("status") == "started"
    print_result("Step 2: Start run", passed, f"Response: {json.dumps(start_data)}")

    if not passed:
        return False

    # Step 3: Poll status
    status_response = requests.get(
        f"{BASE_URL}/runs/{run_id}/status",
        headers={"X-Session-Token": f"{SESSION_TOKEN}-flow"},
    )

    if status_response.status_code != 200:
        print_result("Step 3: Poll status", False, f"Status {status_response.status_code}")
        return False

    status_data = status_response.json()
    has_stage = "stage" in status_data
    has_progress_pct = "progress_pct" in status_data
    has_progress = "progress" in status_data

    print_result(
        "Step 3: Poll status",
        has_stage and has_progress_pct and has_progress,
        f"stage={status_data.get('stage')}, progress_pct={status_data.get('progress_pct')}, progress={status_data.get('progress')}"
    )

    return True


def main():
    print("=" * 60)
    print("JS Backend Integration Verification Tests")
    print("=" * 60)
    print(f"Target: {BASE_URL}")
    print()

    # Check if server is running
    try:
        requests.get(f"{BASE_URL}/runs/99999/status", headers={"X-Session-Token": "test"}, timeout=5)
    except requests.exceptions.ConnectionError:
        print("ERROR: Server not running on localhost:8081")
        print("Start the server with: uvicorn src.main:app --port 8081")
        sys.exit(1)

    results = []

    # Run tests
    print("\n--- Endpoint Existence Tests ---")
    results.append(test_start_endpoint_exists())
    results.append(test_start_response_format())
    results.append(test_confirm_v2_endpoint_exists())

    print("\n--- Response Format Tests ---")
    results.append(test_status_endpoint_fields())

    # Full flow test
    results.append(test_full_flow())

    # Summary
    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} tests passed")

    if passed == total:
        print("All JS Backend integration endpoints are working!")
        sys.exit(0)
    else:
        print("Some tests failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
