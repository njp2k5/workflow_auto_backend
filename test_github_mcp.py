#!/usr/bin/env python
"""
Test script to verify GitHub MCP server endpoints are properly exposed.
Run this after starting the GitHub MCP server in REST mode.

Usage:
    # Terminal 1: Start the GitHub MCP REST API server
    python -m github_mcp_server --mode rest
    
    # Terminal 2: Run this test script
    python test_github_mcp.py
"""

import asyncio
import json
import httpx

BASE_URL = "http://localhost:3003"


async def test_endpoints():
    """Test all GitHub MCP endpoints."""
    async with httpx.AsyncClient(timeout=30) as client:
        endpoints = [
            ("GET", "/health", "Health check"),
            ("GET", "/api/repo-info", "Repository info"),
            ("GET", "/api/contributors", "Contributors list"),
            ("GET", "/api/commits?since_days=7", "Recent commits (7 days)"),
            ("GET", "/api/commit-activity", "Weekly commit activity"),
            ("GET", "/api/pull-requests?state=all", "Pull requests"),
            ("GET", "/api/branches", "Branches"),
            ("GET", "/api/commits-summary?since_days=7", "Commits summary (LLM)"),
            ("GET", "/api/progress-report?since_days=7", "Full progress report (LLM)"),
        ]

        print("=" * 70)
        print("GitHub MCP Server - Endpoint Testing")
        print("=" * 70)
        print(f"\nBase URL: {BASE_URL}\n")

        for method, path, description in endpoints:
            url = BASE_URL + path
            print(f"Testing: {description}")
            print(f"  {method} {path}")

            try:
                if method == "GET":
                    response = await client.get(url)
                else:
                    response = await client.post(url)

                if response.status_code == 200:
                    print(f"  ✓ Status: {response.status_code}")
                    try:
                        data = response.json()
                        if isinstance(data, dict):
                            keys = list(data.keys())[:3]
                            print(f"  Response keys: {keys}...")
                        elif isinstance(data, list):
                            print(f"  Response: list with {len(data)} items")
                    except json.JSONDecodeError:
                        print(f"  Response: {response.text[:100]}...")
                else:
                    print(f"  ✗ Status: {response.status_code}")
                    print(f"  {response.text[:100]}")
            except Exception as exc:
                print(f"  ✗ Error: {exc}")

            print()

        print("=" * 70)
        print("Testing complete!")
        print("=" * 70)


if __name__ == "__main__":
    print("\nMake sure the GitHub MCP server is running:")
    print("  python -m github_mcp_server --mode rest\n")
    asyncio.run(test_endpoints())
