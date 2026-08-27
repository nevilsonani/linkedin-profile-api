#!/usr/bin/env python
"""Exercise a running instance end to end and print a readable report.

    python scripts/smoke_test.py                          # localhost
    python scripts/smoke_test.py --url https://x.onrender.com
    python scripts/smoke_test.py --profile williamhgates

Reads API_KEYS from .env unless --key is given. Exits non-zero if any check
fails, so it is usable as a post-deploy gate in CI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx
from dotenv import load_dotenv

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
OK, FAIL, WARN = f"{GREEN}PASS{RESET}", f"{RED}FAIL{RESET}", f"{YELLOW}WARN{RESET}"

failures = 0
warnings = 0


def report(status: str, label: str, detail: str = "") -> None:
    global failures, warnings
    if status is FAIL:
        failures += 1
    elif status is WARN:
        warnings += 1
    print(f"  [{status}] {label}")
    if detail:
        for line in detail.splitlines():
            print(f"         {DIM}{line}{RESET}")


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def main() -> int:
    load_dotenv()

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="Base URL of the API.")
    ap.add_argument("--key", default=None, help="API key (defaults to first in API_KEYS).")
    ap.add_argument(
        "--profile",
        default="williamhgates",
        help="Profile to scrape for the live check.",
    )
    ap.add_argument("--save", metavar="PATH", help="Write the scraped profile to a file.")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    key = args.key or (os.getenv("API_KEYS", "").split(",")[0].strip() or None)
    headers = {"X-API-Key": key} if key else {}

    print(f"Target: {base}")
    print(f"API key: {'supplied' if key else 'none (assuming open API)'}")

    client = httpx.Client(timeout=60, follow_redirects=True)

    # -- reachability ----------------------------------------------------
    section("Service")
    try:
        r = client.get(f"{base}/health")
    except httpx.RequestError as exc:
        report(FAIL, "Reachable", f"{type(exc).__name__}: {exc}")
        print(f"\n{RED}Cannot reach the API. Is it running?{RESET}")
        print(f"  {DIM}uvicorn app.main:app --reload{RESET}")
        return 1

    if r.status_code != 200:
        report(FAIL, "GET /health", f"HTTP {r.status_code}")
        return 1

    health = r.json()
    report(OK, "GET /health", f"version {health.get('version')}")

    session_configured = health.get("linkedin_session_configured")
    report(
        OK if session_configured else WARN,
        "LinkedIn cookie configured",
        "" if session_configured else "LINKEDIN_LI_AT is empty — live scraping will fail.",
    )
    auth_required = health.get("api_key_required")
    report(
        OK if auth_required else WARN,
        "API key required",
        "" if auth_required else "API_KEYS is empty — this instance is open to anyone.",
    )

    # -- auth ------------------------------------------------------------
    if auth_required:
        section("Authentication")
        r = client.get(f"{base}/api/v1/profile", params={"url": "williamhgates"})
        report(
            OK if r.status_code == 401 else FAIL,
            "Request without a key is rejected",
            f"expected 401, got {r.status_code}",
        )
        r = client.get(
            f"{base}/api/v1/profile",
            params={"url": "williamhgates"},
            headers={"X-API-Key": "definitely-wrong"},
        )
        report(
            OK if r.status_code == 403 else FAIL,
            "Request with a bad key is rejected",
            f"expected 403, got {r.status_code}",
        )

    # -- validation ------------------------------------------------------
    section("Input validation")
    for bad_url, label in [
        ("https://www.linkedin.com/company/google", "Company URL rejected"),
        ("https://example.com/in/someone", "Non-LinkedIn host rejected"),
        ("https://linkedin.com.evil.tld/in/x", "Lookalike domain rejected"),
    ]:
        r = client.get(f"{base}/api/v1/profile", params={"url": bad_url}, headers=headers)
        code = r.json().get("error", {}).get("code") if r.status_code >= 400 else None
        report(
            OK if code == "INVALID_PROFILE_URL" else FAIL,
            label,
            f"HTTP {r.status_code}, code={code}",
        )

    # -- session ---------------------------------------------------------
    section("LinkedIn session")
    r = client.get(f"{base}/health/linkedin", headers=headers)
    info = r.json()
    if info.get("status") == "ok":
        report(OK, "Session is live", f"authenticated as '{info.get('as_public_id')}'")
    else:
        report(
            WARN,
            "Session is not usable",
            f"reason: {info.get('reason')}\n{info.get('hint') or ''}",
        )
        print(f"\n{YELLOW}Skipping the live scrape — no working session.{RESET}")
        return _summary()

    # -- live scrape -----------------------------------------------------
    section(f"Live scrape: {args.profile}")
    r = client.get(
        f"{base}/api/v1/profile",
        params={"url": args.profile, "use_cache": "false"},
        headers=headers,
    )

    if r.status_code != 200:
        err = r.json().get("error", {})
        report(
            FAIL,
            "Scrape succeeded",
            f"HTTP {r.status_code} {err.get('code')}\n{err.get('message')}\n{err.get('hint') or ''}",
        )
        return _summary()

    body = r.json()
    data, meta = body["data"], body["meta"]
    report(OK, "Scrape succeeded", f"{meta['duration_ms']} ms via {meta['source']}")

    # Which sections actually came back with content?
    print()
    for label, value in [
        ("name", data.get("full_name")),
        ("headline", data.get("headline")),
        ("location", (data.get("location") or {}).get("text")),
        ("about", f"{len(data['about'])} chars" if data.get("about") else None),
        ("profile picture", (data.get("profile_picture") or {}).get("url")),
    ]:
        shown = (value[:68] + "…") if isinstance(value, str) and len(value) > 68 else value
        report(OK if value else WARN, f"{label:<16}", "" if value else "empty")
        if value:
            print(f"         {DIM}{shown}{RESET}")

    print()
    for field in [
        "experience",
        "education",
        "skills",
        "certifications",
        "languages",
        "projects",
        "publications",
        "honors",
        "volunteer_experience",
        "courses",
    ]:
        items = data.get(field) or []
        report(OK if items else WARN, f"{field:<22} {len(items):>3} item(s)")

    if meta.get("endpoints_failed"):
        print()
        report(WARN, "Some endpoints failed", ", ".join(meta["endpoints_failed"]))
    for w in meta.get("warnings", []):
        print(f"         {YELLOW}! {w}{RESET}")

    # -- caching ---------------------------------------------------------
    section("Caching")
    r2 = client.get(f"{base}/api/v1/profile", params={"url": args.profile}, headers=headers)
    cached = r2.json()["meta"]["cached"]
    report(
        OK if cached else WARN,
        "Second request served from cache",
        "" if cached else "cache may be disabled (CACHE_TTL_SECONDS=0)",
    )

    if args.save:
        with open(args.save, "w", encoding="utf-8") as fh:
            json.dump(body, fh, indent=2, ensure_ascii=False)
        print(f"\nSaved full response to {args.save}")

    return _summary()


def _summary() -> int:
    print()
    if failures:
        print(f"{RED}{failures} check(s) failed{RESET}", end="")
        print(f", {warnings} warning(s)" if warnings else "")
        return 1
    if warnings:
        print(f"{GREEN}All checks passed{RESET}, {YELLOW}{warnings} warning(s){RESET}")
        return 0
    print(f"{GREEN}All checks passed.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
