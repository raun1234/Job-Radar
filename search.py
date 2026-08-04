"""
Job Radar
---------
Pulls open postings from company career boards (public Greenhouse and Lever
job board APIs — no auth, intended for exactly this use), filters by title
keywords, scores new postings against your profile using the Claude API,
and — for strong matches — generates a tailored resume and cover letter.

Run manually:
    ANTHROPIC_API_KEY=sk-ant-... python search.py

Run on a schedule via GitHub Actions (see .github/workflows/job-radar.yml).
"""

import json
import os
import re
import time
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
PROFILE_PATH = os.path.join(ROOT, "profile.md")
COMPANIES_PATH = os.path.join(ROOT, "companies.json")
SEEN_PATH = os.path.join(ROOT, "seen_jobs.json")
RESULTS_DIR = os.path.join(ROOT, "results")
ALL_RESULTS_PATH = os.path.join(RESULTS_DIR, "all_results.json")
LATEST_REPORT_PATH = os.path.join(RESULTS_DIR, "latest.md")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = os.environ.get("JOB_RADAR_MODEL", "claude-sonnet-4-6")

# Controls which titles get scored at all. Keep broad — the AI scoring step
# is the real filter. This just controls API spend.
KEYWORDS = [
    "gtm", "go-to-market", "go to market",
    "growth strategy", "growth marketing", "growth manager", "growth lead",
    "head of growth", "product marketing", "marketing strategy",
    "business development", "business strategy", "bd manager",
    "strategic partnerships", "partnerships manager", "market development",
    "commercial strategy", "revenue strategy", "market expansion",
]

AUTH_LABELS = {
    "sponsorship": "Will need visa sponsorship (OPT now, H1B later)",
    "authorized": "Authorized to work in the US without sponsorship",
    "na": "Searching outside the US — eligibility not US-visa-dependent",
}
AUTH_STATUS = os.environ.get("AUTH_STATUS", "sponsorship")

# Postings scoring at/above this get a full tailored resume + cover letter.
TAILOR_THRESHOLD = int(os.environ.get("JOB_RADAR_TAILOR_THRESHOLD", "70"))

SYSTEM_PROMPT = """You are an expert recruiter and ATS analyst. Given a CANDIDATE PROFILE and a JOB POSTING, output ONLY a single JSON object, no markdown fences, no preamble, no commentary — just the JSON.

Schema:
{
  "overall_score": integer 0-100,
  "verdict": one of "Strong Fit" | "Good Fit" | "Stretch" | "Weak Fit",
  "verdict_note": one sentence,
  "breakdown": [
    {"category": "Title & Seniority Match", "score": integer 0-100, "note": short sentence},
    {"category": "Skills & Keyword Match", "score": integer 0-100, "note": short sentence},
    {"category": "Domain & Industry Fit", "score": integer 0-100, "note": short sentence},
    {"category": "Track Record Fit", "score": integer 0-100, "note": short sentence},
    {"category": "Eligibility Fit", "score": integer 0-100, "note": short sentence}
  ],
  "missing_keywords": array of up to 6 strings (important JD terms not reflected in the profile),
  "top_signal": one sentence — the single strongest reason this candidate stands out for this role,
  "biggest_gap": one sentence — the single biggest risk or gap for this role,
  "tailored_bullets": array of exactly 3 strings — bullets rewritten from the candidate's REAL experience in the JD's language. Do not invent accomplishments, numbers, or scope.
}

Scoring rules:
- Title & Seniority: does the candidate's level and trajectory match what the posting expects?
- Skills & Keyword: literal overlap between the candidate's stated skills/tools and the JD's — approximates an ATS keyword screen.
- Domain & Industry: relevance of the candidate's industry background to this JD's industry and customer type.
- Track Record: do the candidate's quantified outcomes map to the outcomes this role asks for?
- Eligibility: factor in the candidate's work authorization. If they need sponsorship, assume small companies/startups (under ~500 employees, or any posting that doesn't read as a large established employer) are unlikely to sponsor unless stated — lower this score and reflect it in biggest_gap if it's the dominant constraint. If authorized or outside the US, eligibility should not constrain the score unless the posting states a specific blocking requirement.

Be honest and specific — do not inflate scores to be encouraging. The candidate is using this to triage where to spend application time."""

RESUME_SYSTEM_PROMPT = """You are an expert resume writer. Given a CANDIDATE PROFILE and a JOB POSTING, rewrite the candidate's resume bullets to speak directly to this posting — same real experience, same real numbers, reframed in the posting's language.

Output ONLY a single JSON object, no markdown fences, no commentary:
{
  "summary_line": "one-line professional summary tailored to this role (under 30 words)",
  "experience": [
    {"title": "their actual title", "company": "their actual company", "dates": "their actual dates", "bullets": ["3-5 bullets rewritten for this posting's language"]}
  ],
  "skills_to_surface": ["5-8 skills from their profile most relevant to this posting, in priority order"]
}

Hard rules:
- NEVER invent accomplishments, numbers, tools, or scope not already in the profile.
- Only reorder, reframe, and re-emphasize what's actually there.
- Include every role from the candidate's profile, most recent first.
- Mirror the posting's terminology without misrepresenting what was done."""

COVER_LETTER_SYSTEM_PROMPT = """You are an expert cover letter writer. Given a CANDIDATE PROFILE and a JOB POSTING, write a genuinely strong, specific cover letter — not a generic template.

Output ONLY a single JSON object, no markdown fences, no commentary:
{
  "cover_letter": "the full cover letter text, 250-350 words, ready to send"
}

Requirements:
- Open with something specific to this company/role, not "I am writing to apply for..."
- Reference 1-2 concrete things from the posting itself — show it was read.
- Connect 2 real achievements from the profile (with real numbers) to what this role needs.
- Address the obvious gap or question a reader would have briefly and confidently if relevant.
- End with genuine specificity about why this role/company.
- NEVER invent facts, numbers, or experience not in the profile.
- Plain, confident, direct prose. No clichés ("passionate about," "team player," "fast-paced environment")."""


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def strip_html(html):
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def fetch_greenhouse(token):
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        out.append({
            "id": f"greenhouse:{token}:{j['id']}",
            "company": token,
            "title": j["title"],
            "location": (j.get("location") or {}).get("name", ""),
            "url": j["absolute_url"],
            "description": strip_html(j.get("content", "")),
        })
    return out


def fetch_lever(token):
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    out = []
    for j in r.json():
        out.append({
            "id": f"lever:{token}:{j['id']}",
            "company": token,
            "title": j["text"],
            "location": (j.get("categories") or {}).get("location", ""),
            "url": j["hostedUrl"],
            "description": strip_html(j.get("descriptionPlain") or j.get("description") or ""),
        })
    return out


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever}


def title_matches(title):
    t = title.lower()
    return any(k in t for k in KEYWORDS)


def call_claude(system_prompt, user_content, max_tokens=1500):
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    text = next((b["text"] for b in data.get("content", []) if b.get("type") == "text"), None)
    if not text:
        return None
    clean = re.sub(r"^```(json)?", "", text.strip()).strip()
    clean = re.sub(r"```$", "", clean).strip()
    return json.loads(clean)


def build_job_context(profile, job):
    return (
        f"CANDIDATE PROFILE:\n{profile}\n\n"
        f"WORK AUTHORIZATION: {AUTH_LABELS.get(AUTH_STATUS, AUTH_STATUS)}\n\n"
        f"JOB POSTING:\nTitle: {job['title']}\nCompany: {job['company']}\nLocation: {job['location']}\n\n"
        f"{job['description'][:6000]}"
    )


def score_job(profile, job):
    return call_claude(SYSTEM_PROMPT, build_job_context(profile, job), max_tokens=1000)


def tailor_resume(profile, job):
    return call_claude(RESUME_SYSTEM_PROMPT, build_job_context(profile, job), max_tokens=1500)


def write_cover_letter(profile, job):
    return call_claude(COVER_LETTER_SYSTEM_PROMPT, build_job_context(profile, job), max_tokens=800)


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def save_application_kit(job, result, resume, cover_letter):
    folder = os.path.join(RESULTS_DIR, "applications", slugify(f"{job['company']}-{job['title']}"))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "fit_score.json"), "w") as f:
        json.dump(result, f, indent=2)
    if resume:
        lines = [f"# Tailored Resume — {job['title']} @ {job['company'].title()}", "", resume.get("summary_line", ""), ""]
        for role in resume.get("experience", []):
            lines.append(f"## {role.get('title')} — {role.get('company')} ({role.get('dates')})")
            for b in role.get("bullets", []):
                lines.append(f"- {b}")
            lines.append("")
        lines.append("## Skills")
        lines.append(", ".join(resume.get("skills_to_surface", [])))
        with open(os.path.join(folder, "resume.md"), "w") as f:
            f.write("\n".join(lines))
    if cover_letter:
        with open(os.path.join(folder, "cover_letter.md"), "w") as f:
            f.write(cover_letter.get("cover_letter", ""))
    return folder


def format_entry(entry, short=False):
    job, result = entry["job"], entry["result"]
    lines = [
        f"### {result.get('overall_score')} — {job['title']} @ {job['company'].title()} ({job['location']})",
        f"**{result.get('verdict')}** — {result.get('verdict_note')}",
        f"[View posting]({job['url']})",
    ]
    if entry.get("kit_folder"):
        rel = os.path.relpath(entry["kit_folder"], RESULTS_DIR)
        lines.append(f"Tailored resume + cover letter: `results/{rel}/`")
    if not short:
        lines.append("")
        lines.append(f"- Top signal: {result.get('top_signal')}")
        lines.append(f"- Biggest gap: {result.get('biggest_gap')}")
        if result.get("missing_keywords"):
            lines.append(f"- Missing keywords: {', '.join(result['missing_keywords'])}")
    lines.append("")
    return lines


def write_report(all_results, new_results):
    sorted_all = sorted(all_results, key=lambda e: e["result"].get("overall_score", 0), reverse=True)
    lines = ["# Job Radar — Latest Run", "", f"Run at: {datetime.now(timezone.utc).isoformat()}", ""]
    if new_results:
        lines.append(f"## New postings found this run ({len(new_results)})")
        lines.append("")
        for e in sorted(new_results, key=lambda e: e["result"].get("overall_score", 0), reverse=True):
            lines.extend(format_entry(e))
    else:
        lines.append("## No new matching postings this run.")
        lines.append("")
    lines.append("## Top 15 overall (all-time)")
    lines.append("")
    for e in sorted_all[:15]:
        lines.extend(format_entry(e, short=True))
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(LATEST_REPORT_PATH, "w") as f:
        f.write("\n".join(lines))


def main():
    if not ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY environment variable is not set.")

    profile = open(PROFILE_PATH).read()
    companies = load_json(COMPANIES_PATH, [])
    seen = load_json(SEEN_PATH, {})
    all_results = load_json(ALL_RESULTS_PATH, [])

    new_results = []
    connected_companies = []
    failed_companies = []

    for c in companies:
        fetcher = FETCHERS.get(c.get("ats"))
        if not fetcher:
            failed_companies.append({"name": c["name"], "token": c["token"], "reason": "unknown ats"})
            continue
        try:
            jobs = fetcher(c["token"])
            connected_companies.append({
                "name": c["name"], "token": c["token"], "ats": c["ats"], "jobs_found": len(jobs),
            })
        except Exception as e:
            failed_companies.append({"name": c["name"], "token": c["token"], "reason": str(e)})
            print(f"[fail] {c.get('name')} ({c['token']}): {e}")
            continue

        for job in jobs:
            if job["id"] in seen:
                continue
            if not title_matches(job["title"]):
                seen[job["id"]] = {"skipped": True, "title": job["title"]}
                continue

            print(f"[score] {job['title']} @ {job['company']}")
            try:
                result = score_job(profile, job)
            except Exception as e:
                print(f"[warn] scoring failed for {job['title']} @ {job['company']}: {e}")
                continue

            if result:
                kit_folder = None
                resume, cover_letter = None, None
                if result.get("overall_score", 0) >= TAILOR_THRESHOLD:
                    print(f"  -> score {result.get('overall_score')} >= {TAILOR_THRESHOLD}, generating kit")
                    try:
                        resume = tailor_resume(profile, job)
                        cover_letter = write_cover_letter(profile, job)
                        kit_folder = save_application_kit(job, result, resume, cover_letter)
                    except Exception as e:
                        print(f"[warn] tailoring failed for {job['title']} @ {job['company']}: {e}")

                entry = {
                    "job": job, "result": result, "kit_folder": kit_folder,
                    "found_at": datetime.now(timezone.utc).isoformat(),
                }
                new_results.append(entry)
                all_results.append(entry)
                seen[job["id"]] = {"scored": True, "score": result.get("overall_score")}
            else:
                seen[job["id"]] = {"scored": False}

            time.sleep(1)

    save_json(SEEN_PATH, seen)
    save_json(ALL_RESULTS_PATH, all_results)
    write_report(all_results, new_results)

    health = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_companies": len(companies),
            "connected": len(connected_companies),
            "failed": len(failed_companies),
            "connection_rate": f"{round(len(connected_companies)/max(len(companies),1)*100)}%",
        },
        "connected": sorted(connected_companies, key=lambda x: x["jobs_found"], reverse=True),
        "failed": failed_companies,
    }
    os.makedirs(RESULTS_DIR, exist_ok=True)
    save_json(os.path.join(RESULTS_DIR, "connection_health.json"), health)

    print(f"\n{'='*60}")
    print(f"Run complete")
    print(f"  Companies checked:   {len(companies)}")
    print(f"  Connected:           {len(connected_companies)} ({health['summary']['connection_rate']})")
    print(f"  Failed / bad token:  {len(failed_companies)}")
    print(f"  New postings scored: {len(new_results)}")
    print(f"{'='*60}")
    if failed_companies:
        print("\nFailed companies (remove from companies.json):")
        for f in failed_companies:
            print(f"  - {f['name']} ({f['token']}) — {f['reason'][:80]}")


if __name__ == "__main__":
    main()
