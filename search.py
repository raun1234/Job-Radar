"""
Job Radar — Complete Agentic Job Search System
-----------------------------------------------
Fetches postings from Greenhouse + Lever APIs, filters by role and seniority,
scores against your profile using Haiku (cheap, accurate), generates tailored
Word doc resumes + cover letters using Sonnet (better writing) for 70+ scores.

Run manually:
    ANTHROPIC_API_KEY=sk-ant-... python -u search.py

Scheduled daily via GitHub Actions.
"""

import json, os, re, subprocess, sys, time
from datetime import datetime, timezone
import requests

ROOT         = os.path.dirname(os.path.abspath(__file__))
PROFILE_PATH = os.path.join(ROOT, "profile.md")
COMPANIES    = os.path.join(ROOT, "companies.json")
SEEN_PATH    = os.path.join(ROOT, "seen_jobs.json")
RESULTS_DIR  = os.path.join(ROOT, "results")
ALL_PATH     = os.path.join(RESULTS_DIR, "all_results.json")
REPORT_PATH  = os.path.join(RESULTS_DIR, "latest.md")
GEN_RESUME   = os.path.join(ROOT, "generate_resume.js")

API_KEY      = os.environ.get("ANTHROPIC_API_KEY", "").strip()
SCORE_MODEL  = os.environ.get("JOB_RADAR_SCORE_MODEL",  "claude-haiku-4-5-20251001")
TAILOR_MODEL = os.environ.get("JOB_RADAR_TAILOR_MODEL", "claude-sonnet-4-6")
THRESHOLD    = int(os.environ.get("JOB_RADAR_TAILOR_THRESHOLD", "70"))
MAX_SPEND    = float(os.environ.get("JOB_RADAR_MAX_SPEND", "1.00"))
AUTH_STATUS  = os.environ.get("AUTH_STATUS", "sponsorship")

AUTH_LABELS = {
    "sponsorship": "Will need visa sponsorship (OPT now, H1B later)",
    "authorized":  "Authorized to work in the US without sponsorship",
    "na":          "Outside the US — not applicable",
}

# Cost per token (conservative estimates)
COSTS = {
    "haiku":  {"input": 1.00/1e6, "output": 5.00/1e6},
    "sonnet": {"input": 3.00/1e6, "output": 15.00/1e6},
}
estimated_spend = 0.0

# ── ROLE FILTERS ──────────────────────────────────────────────────────────────
# Title must contain at least one INCLUDE phrase (multi-word = more precise)
INCLUDE = [
    # GTM
    "gtm strategy", "gtm manager", "gtm lead", "gtm strategist",
    "go-to-market manager", "go-to-market strategy", "go-to-market lead",
    "go to market manager", "go to market strategy",
    # Growth
    "growth strategy manager", "growth strategy lead",
    "growth marketing manager", "growth marketing lead",
    "growth manager", "growth lead",
    # Product Marketing
    "product marketing manager", "product marketing lead",
    "senior product marketing", "associate product marketing",
    # Business Development
    "business development manager", "business development lead",
    "senior business development", "bd manager", "bd lead",
    # Partnerships
    "partnerships manager", "partnerships lead",
    "strategic partnerships", "alliance manager",
    "channel partnerships", "partner manager",
    # Market Development
    "market development manager", "market development lead",
    "market expansion manager",
    # Commercial
    "commercial strategy", "commercial manager", "commercial lead",
    # Revenue / Demand
    "demand generation manager", "demand gen manager",
    "revenue operations manager", "revenue marketing manager",
    "revenue strategy manager",
    # Enablement / Launch
    "sales enablement manager", "sales enablement lead",
    "launch manager", "product launch manager",
    "marketing strategy manager",
    # Other relevant
    "integrated marketing manager", "customer marketing manager",
    "market intelligence manager", "segment marketing manager",
    "monetization manager", "marketing operations manager",
    # AI-specific
    "ai gtm", "ai go-to-market", "ai product marketing",
    "ai growth manager", "ai partnerships", "ai commercialization",
    "generative ai gtm", "enterprise ai gtm", "ai solutions marketing",
    "ai market", "ai adoption manager",
]

# Title containing any EXCLUDE word/phrase → skipped before any API call
EXCLUDE = [
    "director", "vice president", " vp ", "vp,", "vp-", "svp", "evp",
    "chief", "cmo", "cro", "coo", "ceo", "c-suite",
    "principal", " staff ", "distinguished", "fellow",
    "intern", "internship", "apprentice", "trainee",
    "counsel", "legal", "attorney", "paralegal",
    "recruiter", "recruiting", "talent acquisition", "sourcer",
    " hr ", "human resources", "people business partner", "people partner",
    "finance", "financial", "accounting", "controller", "fp&a",
    "data scientist", "data engineer", "data analyst",
    "software engineer", "ml engineer", "machine learning engineer",
    "devops", "backend", "frontend", "full stack", "fullstack",
    "gtm systems", "gtm operations", "gtm enablement",
    "gtm finance", "gtm engineer", "gtm architect",
    "solutions architect", "sales engineer", "presales",
    "representative", " bdr", " sdr", "account executive",
    "customer success manager", "customer success lead",
    "consultant", "associate consultant",
    "head of growth", "head of gtm", "head of marketing",
    "head of product marketing",
]


# ── HELPERS ───────────────────────────────────────────────────────────────────
def load(path, default):
    return json.load(open(path)) if os.path.exists(path) else default

def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(data, open(path, "w"), indent=2)

def strip_html(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()

def slugify(t):
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")

def title_ok(title):
    t = title.lower()
    if any(e in t for e in EXCLUDE):
        return False
    return any(k in t for k in INCLUDE)


# ── FETCHERS ──────────────────────────────────────────────────────────────────
def extract_salary(text):
    patterns = [
        r'\$[\d,]+\s*[-to]+\s*\$[\d,]+\s*(?:per year|annually|\/yr|\/year)?',
        r'\$[\d,]+[Kk]\s*[-to]+\s*\$[\d,]+[Kk]',
        r'[\d,]+\s*[-to]+\s*[\d,]+\s*(?:USD|usd)',
        r'salary[:\s]+\$[\d,]+',
        r'compensation[:\s]+\$[\d,]+',
        r'\$[\d,]+\s*(?:per year|annually|\/yr)',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return ""

def extract_work_type(title, location, description):
    t = (title + " " + location + " " + description[:500]).lower()
    if "remote" in t:
        return "Remote"
    if "hybrid" in t:
        return "Hybrid"
    if "onsite" in t or "on-site" in t or "in office" in t or "in-office" in t:
        return "On-site"
    return ""

def extract_seniority(title):
    t = title.lower()
    if any(x in t for x in ["senior", "sr.", "sr "]):
        return "Senior"
    if any(x in t for x in ["associate", "junior", "jr "]):
        return "Associate"
    if any(x in t for x in ["lead", "principal"]):
        return "Lead"
    if any(x in t for x in ["manager", "mgr"]):
        return "Manager"
    return ""

def fetch_greenhouse(token):
    r = requests.get(
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
        timeout=10)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        desc = strip_html(j.get("content", ""))
        loc  = (j.get("location") or {}).get("name", "")
        title = j["title"]
        out.append({
            "id": f"gh:{token}:{j['id']}",
            "company": token,
            "title": title,
            "location": loc,
            "url": j["absolute_url"],
            "description": desc,
            "salary": extract_salary(desc),
            "work_type": extract_work_type(title, loc, desc),
            "seniority": extract_seniority(title),
            "posted_at": j.get("updated_at", ""),
        })
    return out

def fetch_lever(token):
    r = requests.get(
        f"https://api.lever.co/v0/postings/{token}?mode=json", timeout=10)
    r.raise_for_status()
    out = []
    for j in r.json():
        desc = strip_html(j.get("descriptionPlain") or j.get("description") or "")
        loc  = (j.get("categories") or {}).get("location", "")
        title = j["text"]
        import datetime
        posted = ""
        if j.get("createdAt"):
            try:
                posted = datetime.datetime.fromtimestamp(j["createdAt"]/1000, tz=datetime.timezone.utc).isoformat()
            except Exception:
                pass
        out.append({
            "id": f"lv:{token}:{j['id']}",
            "company": token,
            "title": title,
            "location": loc,
            "url": j["hostedUrl"],
            "description": desc,
            "salary": extract_salary(desc),
            "work_type": extract_work_type(title, loc, desc),
            "seniority": extract_seniority(title),
            "posted_at": posted,
        })
    return out

FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever}


# ── CLAUDE CALLS ──────────────────────────────────────────────────────────────
SCORE_PROMPT = """You are an expert recruiter. Given a CANDIDATE PROFILE and JOB POSTING, output ONLY a JSON object — no markdown, no preamble.

{
  "overall_score": 0-100,
  "verdict": "Strong Fit"|"Good Fit"|"Stretch"|"Weak Fit",
  "verdict_note": "one sentence",
  "breakdown": [
    {"category": "Title & Seniority Match", "score": 0-100, "note": "short"},
    {"category": "Skills & Keyword Match",  "score": 0-100, "note": "short"},
    {"category": "Domain & Industry Fit",   "score": 0-100, "note": "short"},
    {"category": "Track Record Fit",        "score": 0-100, "note": "short"},
    {"category": "Eligibility Fit",         "score": 0-100, "note": "short"}
  ],
  "missing_keywords": ["up to 5 key JD terms not in profile"],
  "top_signal": "strongest reason candidate stands out",
  "biggest_gap": "biggest risk or gap",
  "tailored_bullets": ["3 bullets rewritten from REAL experience — never invent"]
}

Target band: Manager / Senior Manager level. Be honest — do not inflate scores.
Eligibility: candidate needs H1B sponsorship. Penalise small startups unless sponsorship stated."""

RESUME_PROMPT = """Rewrite the candidate's resume bullets for this specific posting. Output ONLY JSON:

{
  "summary_line": "tailored summary under 30 words",
  "experience": [
    {"title": "actual title", "company": "actual company",
     "dates": "actual dates", "bullets": ["3-5 rewritten bullets"]}
  ],
  "skills_to_surface": ["5-8 most relevant skills in priority order"]
}

NEVER invent numbers, titles, or experience. Only reframe what is actually in the profile.
Mirror the posting's exact terminology where the candidate's experience genuinely maps to it."""

COVER_PROMPT = """Write a strong, specific cover letter for this role. Output ONLY JSON:

{"cover_letter": "250-350 words, ready to send"}

Rules:
- Open with something specific to this company/role — never 'I am writing to apply'
- Reference 1-2 concrete things from the actual posting
- Connect 2 real achievements with real numbers to what this role needs
- Address the obvious gap briefly and confidently if relevant
- End with genuine specificity about why this company
- NEVER invent facts not in the profile
- No clichés: no 'passionate about', 'team player', 'fast-paced environment', 'I believe'"""

def context(profile, job):
    return (f"CANDIDATE PROFILE:\n{profile}\n\n"
            f"WORK AUTHORIZATION: {AUTH_LABELS.get(AUTH_STATUS)}\n\n"
            f"JOB POSTING:\nTitle: {job['title']}\nCompany: {job['company']}\n"
            f"Location: {job['location']}\n\n{job['description'][:5000]}")

def claude(system, user, max_tokens, model):
    global estimated_spend
    tier = "haiku" if "haiku" in model else "sonnet"
    in_t = (len(system) + len(user)) // 4
    cost = in_t * COSTS[tier]["input"] + max_tokens * COSTS[tier]["output"]
    if estimated_spend + cost > MAX_SPEND:
        raise RuntimeError(
            f"Spend cap ${MAX_SPEND} reached (${estimated_spend:.4f} + ~${cost:.4f}). "
            "Stopping. Raise JOB_RADAR_MAX_SPEND to continue.")
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": max_tokens, "system": system,
              "messages": [{"role": "user", "content": user}]},
        timeout=60)
    resp.raise_for_status()
    estimated_spend += cost
    text = next((b["text"] for b in resp.json().get("content", [])
                 if b.get("type") == "text"), None)
    if not text:
        return None
    clean = re.sub(r"^```(json)?", "", text.strip()).strip()
    return json.loads(re.sub(r"```$", "", clean).strip())


# ── KIT GENERATION ────────────────────────────────────────────────────────────
def generate_kit(profile, job, result):
    folder = os.path.join(RESULTS_DIR, "applications",
                          slugify(f"{job['company']}-{job['title']}"))
    os.makedirs(folder, exist_ok=True)
    save(os.path.join(folder, "fit_score.json"), result)

    # Resume
    resume = claude(RESUME_PROMPT, context(profile, job), 1200, TAILOR_MODEL)
    if resume:
        # Generate Word doc via generate_resume.js
        resume_data = {**resume,
                       "job_title": job["title"],
                       "job_company": job["company"]}
        input_path  = os.path.join(folder, "_resume_input.json")
        output_path = os.path.join(folder, "resume.docx")
        save(input_path, resume_data)
        if os.path.exists(GEN_RESUME):
            result_gen = subprocess.run(
                ["node", GEN_RESUME, input_path, output_path],
                capture_output=True, text=True)
            if result_gen.returncode != 0:
                print(f"  [warn] Word doc generation failed: {result_gen.stderr[:200]}")
                # Fallback to markdown
                lines = [f"# Resume — {job['title']} @ {job['company'].title()}",
                         "", resume.get("summary_line", ""), ""]
                for role in resume.get("experience", []):
                    lines.append(f"## {role.get('title')} — {role.get('company')} ({role.get('dates')})")
                    for b in role.get("bullets", []):
                        lines.append(f"- {b}")
                    lines.append("")
                lines += ["## Skills", ", ".join(resume.get("skills_to_surface", []))]
                open(os.path.join(folder, "resume.md"), "w").write("\n".join(lines))
        else:
            print("  [warn] generate_resume.js not found — saving resume.md instead")
            open(os.path.join(folder, "resume.md"), "w").write(
                "\n".join([resume.get("summary_line", "")] +
                          [b for r in resume.get("experience", []) for b in r.get("bullets", [])]))

    # Cover letter
    cover = claude(COVER_PROMPT, context(profile, job), 700, TAILOR_MODEL)
    if cover:
        open(os.path.join(folder, "cover_letter.md"), "w").write(
            cover.get("cover_letter", ""))

    return folder


# ── REPORT ────────────────────────────────────────────────────────────────────
def entry_md(entry, short=False):
    j, r = entry["job"], entry["result"]
    lines = [
        f"### {r.get('overall_score')} — {j['title']} @ {j['company'].title()} ({j['location']})",
        f"**{r.get('verdict')}** — {r.get('verdict_note')}",
        f"[View posting]({j['url']})",
    ]
    if entry.get("kit_folder"):
        lines.append(f"📄 Kit: `{os.path.relpath(entry['kit_folder'], RESULTS_DIR)}/`")
    if not short:
        lines += ["",
                  f"- Top signal: {r.get('top_signal')}",
                  f"- Biggest gap: {r.get('biggest_gap')}"]
        if r.get("missing_keywords"):
            lines.append(f"- Missing keywords: {', '.join(r['missing_keywords'])}")
    lines.append("")
    return lines

def write_report(all_results, new_results, health):
    lines = ["# Job Radar", "",
             f"Run: {datetime.now(timezone.utc).isoformat()}", "",
             f"**Companies:** {health['total']} checked · {health['connected']} connected · {health['failed']} failed",
             f"**Estimated spend this run:** ${estimated_spend:.4f}", ""]
    if new_results:
        lines += [f"## New this run ({len(new_results)})", ""]
        for e in sorted(new_results, key=lambda x: x["result"].get("overall_score",0), reverse=True):
            lines.extend(entry_md(e))
    else:
        lines += ["## No new matching postings this run.", ""]
    lines += ["## Top 15 all-time", ""]
    for e in sorted(all_results, key=lambda x: x["result"].get("overall_score",0), reverse=True)[:15]:
        lines.extend(entry_md(e, short=True))
    os.makedirs(RESULTS_DIR, exist_ok=True)
    open(REPORT_PATH, "w").write("\n".join(lines))


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    global estimated_spend
    if not API_KEY:
        sys.exit("ANTHROPIC_API_KEY is not set.")

    print(f"Job Radar — {datetime.now(timezone.utc).isoformat()}")
    print(f"Score model:  {SCORE_MODEL}")
    print(f"Tailor model: {TAILOR_MODEL}")
    print(f"Spend cap:    ${MAX_SPEND}")
    print(f"Threshold:    {THRESHOLD}")
    print("---")

    profile     = open(PROFILE_PATH).read()
    companies   = load(COMPANIES, [])
    seen        = load(SEEN_PATH, {})
    all_results = load(ALL_PATH, [])
    new_results = []
    connected, failed = [], []

    for c in companies:
        fetcher = FETCHERS.get(c.get("ats"))
        if not fetcher:
            failed.append({"name": c["name"], "token": c["token"], "reason": "unknown ats"})
            continue
        try:
            jobs = fetcher(c["token"])
            connected.append({"name": c["name"], "token": c["token"],
                              "ats": c["ats"], "jobs_found": len(jobs)})
        except Exception as e:
            failed.append({"name": c["name"], "token": c["token"], "reason": str(e)})
            print(f"[fail] {c['name']} ({c['token']}): {e}")
            continue

        for job in jobs:
            if job["id"] in seen:
                continue
            if not title_ok(job["title"]):
                seen[job["id"]] = {"skipped": True}
                continue

            print(f"[score] {job['title']} @ {job['company']}")
            try:
                result = claude(SCORE_PROMPT, context(profile, job), 900, SCORE_MODEL)
            except RuntimeError as e:
                print(f"\n[SPEND CAP] {e}")
                break
            except Exception as e:
                print(f"  [warn] scoring failed: {e}")
                continue
            if not result:
                seen[job["id"]] = {"scored": False}
                continue

            score = result.get("overall_score", 0)
            print(f"  score: {score} — {result.get('verdict')}")

            kit_folder = None
            if score >= THRESHOLD:
                print(f"  -> generating kit")
                try:
                    kit_folder = generate_kit(profile, job, result)
                    print(f"  -> kit saved: {kit_folder}")
                except RuntimeError as e:
                    print(f"\n[SPEND CAP] {e}")
                except Exception as e:
                    print(f"  [warn] kit generation failed: {e}")

            entry = {"job": job, "result": result, "kit_folder": kit_folder,
                     "found_at": datetime.now(timezone.utc).isoformat()}
            new_results.append(entry)
            all_results.append(entry)
            seen[job["id"]] = {"scored": True, "score": score}
            time.sleep(0.5)

    save(SEEN_PATH, seen)
    save(ALL_PATH, all_results)

    health = {"total": len(companies), "connected": len(connected), "failed": len(failed),
              "rate": f"{round(len(connected)/max(len(companies),1)*100)}%"}
    save(os.path.join(RESULTS_DIR, "connection_health.json"), {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "summary": health,
        "connected": sorted(connected, key=lambda x: x["jobs_found"], reverse=True),
        "failed": failed,
    })
    write_report(all_results, new_results, health)

    print(f"\n{'='*60}")
    print(f"Companies:    {len(companies)} checked · {len(connected)} connected · {len(failed)} failed")
    print(f"New scored:   {len(new_results)}")
    print(f"Spend:        ${estimated_spend:.4f}")
    print(f"{'='*60}")
    if failed:
        print("\nFailed tokens (remove from companies.json):")
        for f in failed:
            print(f"  - {f['name']} ({f['token']}): {f['reason'][:80]}")

if __name__ == "__main__":
    main()
