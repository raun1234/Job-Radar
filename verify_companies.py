"""
verify_companies.py — one-time company token verifier
------------------------------------------------------
Tests every candidate token against the real Greenhouse / Lever APIs and
writes companies.json containing ONLY tokens that actually work.

Makes ZERO Claude API calls. Costs nothing. Uses only free HTTP requests.

Run via GitHub Actions (see .github/workflows/verify-companies.yml)
or locally:  python -u verify_companies.py
"""

import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT       = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = os.path.join(ROOT, "candidates.json")
OUTPUT     = os.path.join(ROOT, "companies.json")
REPORT     = os.path.join(ROOT, "results", "verification_report.json")

TIMEOUT = 8
WORKERS = 12   # parallel checks — keeps the run to a couple of minutes


def check(entry):
    """Return (entry, ok, job_count, reason)."""
    token, ats = entry["token"], entry["ats"]
    try:
        if ats == "greenhouse":
            url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        elif ats == "ashby":
            url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
        else:
            url = f"https://api.lever.co/v0/postings/{token}?mode=json"

        r = requests.get(url, timeout=TIMEOUT)

        if r.status_code == 404:
            return entry, False, 0, "404 board not found"
        if r.status_code != 200:
            return entry, False, 0, f"HTTP {r.status_code}"

        data = r.json()
        if ats == "greenhouse":
            jobs = data.get("jobs", [])
        elif ats == "ashby":
            jobs = data.get("jobs", []) if isinstance(data, dict) else data
        else:
            jobs = data
        count = len(jobs)

        if count == 0:
            return entry, False, 0, "board exists but has 0 open jobs"
        return entry, True, count, "ok"

    except requests.exceptions.Timeout:
        return entry, False, 0, "timeout"
    except Exception as e:
        return entry, False, 0, str(e)[:60]


def main():
    candidates = json.load(open(CANDIDATES))
    print(f"Verifying {len(candidates)} candidate tokens...")
    print(f"Workers: {WORKERS} | Timeout: {TIMEOUT}s")
    print("This makes ZERO Claude API calls and costs nothing.\n")

    verified, dead = [], []
    done = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(check, c): c for c in candidates}
        for fut in as_completed(futures):
            entry, ok, count, reason = fut.result()
            done += 1
            if ok:
                entry["jobs_at_verify"] = count
                verified.append(entry)
                print(f"[{done}/{len(candidates)}] OK    {entry['name']:<28} {count} jobs")
            else:
                dead.append({**entry, "reason": reason})
                print(f"[{done}/{len(candidates)}] DEAD  {entry['name']:<28} {reason}")

    # Sort verified by job count (most active first)
    verified.sort(key=lambda x: x.get("jobs_at_verify", 0), reverse=True)

    # Write clean companies.json (strip the verify metadata)
    clean = [{"name": v["name"], "ats": v["ats"], "token": v["token"]} for v in verified]
    json.dump(clean, open(OUTPUT, "w"), indent=2)

    # Write full report
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    json.dump({
        "checked": len(candidates),
        "verified": len(verified),
        "dead": len(dead),
        "rate": f"{round(len(verified)/max(len(candidates),1)*100)}%",
        "verified_companies": verified,
        "dead_companies": dead,
    }, open(REPORT, "w"), indent=2)

    total_jobs = sum(v.get("jobs_at_verify", 0) for v in verified)
    print(f"\n{'='*62}")
    print(f"  Checked:        {len(candidates)}")
    print(f"  VERIFIED:       {len(verified)}  ({round(len(verified)/max(len(candidates),1)*100)}%)")
    print(f"  Dead/removed:   {len(dead)}")
    print(f"  Total open jobs across verified boards: {total_jobs:,}")
    print(f"{'='*62}")
    print(f"\ncompanies.json now contains {len(clean)} VERIFIED companies.")
    print("Every one of these returned real jobs from the live API.")


if __name__ == "__main__":
    main()
