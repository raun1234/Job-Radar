"""
verify_companies.py -- one-time company token verifier
---------------------------------------------------------
Tests every candidate token against the real Greenhouse, Lever, Ashby,
Workable, Recruitee, and Personio APIs, then writes companies.json
containing ONLY tokens that actually returned real jobs.

Makes ZERO Claude API calls. Costs nothing -- plain HTTP requests only.

Run via GitHub Actions or locally:
    python -u verify_companies.py
"""

import json, os
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT       = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = os.path.join(ROOT, "candidates.json")
OUTPUT     = os.path.join(ROOT, "companies.json")
REPORT     = os.path.join(ROOT, "results", "verification_report.json")

TIMEOUT = 8
WORKERS = 12


def check(entry):
    token, ats = entry["token"], entry["ats"]
    try:
        if ats == "greenhouse":
            url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        elif ats == "lever":
            url = f"https://api.lever.co/v0/postings/{token}?mode=json"
        elif ats == "ashby":
            url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
        elif ats == "workable":
            url = f"https://apply.workable.com/api/v1/widget/accounts/{token}"
        elif ats == "recruitee":
            url = f"https://{token}.recruitee.com/api/offers/"
        elif ats == "personio":
            url = f"https://{token}.jobs.personio.de/xml"
        else:
            return entry, False, 0, f"unknown ats: {ats}"

        r = requests.get(url, timeout=TIMEOUT)

        if r.status_code == 404:
            return entry, False, 0, "404 board not found"
        if r.status_code != 200:
            return entry, False, 0, f"HTTP {r.status_code}"

        if ats == "personio":
            import xml.etree.ElementTree as ET
            try:
                root = ET.fromstring(r.content)
                count = len(root.findall(".//position"))
            except ET.ParseError:
                return entry, False, 0, "invalid XML"
        else:
            data = r.json()
            if ats == "greenhouse":
                count = len(data.get("jobs", []))
            elif ats == "lever":
                count = len(data)
            elif ats == "ashby":
                jobs = data.get("jobs", []) if isinstance(data, dict) else data
                count = len(jobs)
            elif ats == "workable":
                count = len(data.get("jobs", []))
            elif ats == "recruitee":
                count = len(data.get("offers", []))
            else:
                count = 0

        if count == 0:
            return entry, False, 0, "board exists but has 0 open jobs"
        return entry, True, count, "ok"

    except requests.exceptions.Timeout:
        return entry, False, 0, "timeout"
    except Exception as e:
        return entry, False, 0, str(e)[:60]


def main():
    candidates = json.load(open(CANDIDATES))
    print(f"Verifying {len(candidates)} candidate tokens across 6 ATS types...")
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
                print(f"[{done}/{len(candidates)}] OK    {entry['name']:<28} {entry['ats']:<10} {count} jobs")
            else:
                dead.append({**entry, "reason": reason})
                print(f"[{done}/{len(candidates)}] DEAD  {entry['name']:<28} {entry['ats']:<10} {reason}")

    verified.sort(key=lambda x: x.get("jobs_at_verify", 0), reverse=True)
    clean = [{"name": v["name"], "ats": v["ats"], "token": v["token"]} for v in verified]
    json.dump(clean, open(OUTPUT, "w"), indent=2)

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    json.dump({
        "checked": len(candidates),
        "verified": len(verified),
        "dead": len(dead),
        "rate": f"{round(len(verified)/max(len(candidates),1)*100)}%",
        "by_ats": {ats: sum(1 for v in verified if v["ats"] == ats)
                   for ats in ("greenhouse", "lever", "ashby", "workable", "recruitee", "personio")},
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


if __name__ == "__main__":
    main()
