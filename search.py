"""
Job Radar - Scoring Engine (v3)
--------------------------------
Fetches postings from Greenhouse, Lever, and Ashby, filters by role,
seniority, US location, posting age, required experience, and hard
requirements, then scores survivors against your profile using Claude
Haiku. No kit generation -- tailoring happens manually in chat per
posting, since a live conversation beats a one-shot API call.

Run manually:
    ANTHROPIC_API_KEY=sk-ant-... python -u search.py

Scheduled daily via GitHub Actions.
"""

import json, os, re, time
from datetime import datetime, timezone, timedelta
import requests

ROOT         = os.path.dirname(os.path.abspath(__file__))
PROFILE_PATH = os.path.join(ROOT, "profile.md")
COMPANIES    = os.path.join(ROOT, "companies.json")
SEEN_PATH    = os.path.join(ROOT, "seen_jobs.json")
RESULTS_DIR  = os.path.join(ROOT, "results")
ALL_PATH     = os.path.join(RESULTS_DIR, "all_results.json")
REPORT_PATH  = os.path.join(RESULTS_DIR, "latest.md")
HEALTH_PATH  = os.path.join(RESULTS_DIR, "connection_health.json")

API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "").strip()
SCORE_MODEL = os.environ.get("JOB_RADAR_SCORE_MODEL", "claude-haiku-4-5-20251001")
THRESHOLD   = int(os.environ.get("JOB_RADAR_TOP_THRESHOLD", "75"))
MAX_SPEND   = float(os.environ.get("JOB_RADAR_MAX_SPEND", "0.15"))
AUTH_STATUS = os.environ.get("AUTH_STATUS", "sponsorship")
MAX_AGE_DAYS = int(os.environ.get("JOB_RADAR_MAX_AGE_DAYS", "21"))
MAX_YEARS_REQUIRED = int(os.environ.get("JOB_RADAR_MAX_YEARS", "8"))

AUTH_LABELS = {
    "sponsorship": "Will need visa sponsorship (OPT now, H1B later)",
    "authorized":  "Authorized to work in the US without sponsorship",
    "na":          "Outside the US -- not applicable",
}

COSTS = {"haiku": {"input": 1.00/1e6, "output": 5.00/1e6}}
estimated_spend = 0.0

INCLUDE = [
    "gtm strategy", "gtm manager", "gtm lead", "gtm strategist",
    "go-to-market manager", "go-to-market strategy", "go-to-market lead",
    "go to market manager", "go to market strategy",
    "growth strategy manager", "growth strategy lead",
    "growth marketing manager", "growth marketing lead",
    "growth manager", "growth lead",
    "product marketing manager", "product marketing lead",
    "senior product marketing", "associate product marketing",
    "business development manager", "business development lead",
    "senior business development", "bd manager", "bd lead",
    "partnerships manager", "partnerships lead",
    "strategic partnerships", "alliance manager",
    "channel partnerships", "partner manager",
    "market development manager", "market development lead",
    "market expansion manager",
    "commercial strategy", "commercial manager", "commercial lead",
    "demand generation manager", "demand gen manager",
    "revenue operations manager", "revenue marketing manager",
    "revenue strategy manager",
    "sales enablement manager", "sales enablement lead",
    "launch manager", "product launch manager",
    "marketing strategy manager",
    "integrated marketing manager", "customer marketing manager",
    "market intelligence manager", "segment marketing manager",
    "monetization manager", "marketing operations manager",
    "ai gtm", "ai go-to-market", "ai product marketing",
    "ai growth manager", "ai partnerships", "ai commercialization",
    "generative ai gtm", "enterprise ai gtm", "ai solutions marketing",
    "ai market", "ai adoption manager",
    "solutions marketing lead", "solutions marketing manager",
    "vertical marketing manager", "industry marketing manager",
    "lifecycle marketing manager", "lifecycle marketing lead",
    "gtm alliances manager", "strategic alliances manager",
    "channel partner manager", "channel manager",
    "ecosystem partner manager", "ecosystem manager",
]

EXCLUDE_PATTERNS = [
    r"\bdirector\b", r"\bvice president\b", r"\bvp\b", r"\bsvp\b", r"\bevp\b",
    r"\bchief\b", r"\bcmo\b", r"\bcro\b", r"\bcoo\b", r"\bceo\b", r"c-suite",
    r"\bprincipal\b", r"\bstaff\b", r"\bdistinguished\b", r"\bfellow\b",
    r"\bleader\b",
    r"\bintern\b", r"internship", r"\bapprentice\b", r"\btrainee\b",
    r"\bcounsel\b", r"\blegal\b", r"\battorney\b", r"\bparalegal\b",
    r"\blaw\b", r"law firm",
    r"\brecruiter\b", r"recruiting", r"talent acquisition", r"\bsourcer\b",
    r"\bhr\b", r"human resources", r"people business partner", r"people partner",
    r"\bfinance\b", r"financial", r"accounting", r"\bcontroller\b", r"fp&a",
    r"\bcpa\b",
    r"data scientist", r"data engineer", r"\bdata analyst\b",
    r"software engineer", r"ml engineer", r"machine learning engineer",
    r"\bdevops\b", r"\bbackend\b", r"\bfrontend\b", r"full.?stack",
    r"gtm systems", r"gtm operations", r"gtm enablement",
    r"gtm finance", r"gtm engineer", r"gtm architect",
    r"solutions architect", r"sales engineer", r"pre.?sales",
    r"\brepresentative\b", r"\bbdr\b", r"\bsdr\b", r"account executive",
    r"customer success", r"\bconsultant\b", r"associate consultant",
    r"head of growth", r"head of gtm", r"head of marketing",
    r"head of product marketing", r"\bhead of\b",
]
EXCLUDE_RE = re.compile("|".join(EXCLUDE_PATTERNS), re.IGNORECASE)

NON_US_REGION_PATTERNS = [
    r"\bindia\b", r"\bsouth east asia\b", r"\bsoutheast asia\b", r"\bapac\b",
    r"\bemea\b", r"\blatam\b", r"\blatin america\b",
    r"\buk\b", r"\bunited kingdom\b", r"\bireland\b",
    r"\bgermany\b", r"\bfrance\b", r"\bspain\b", r"\bitaly\b", r"\bnetherlands\b",
    r"\bsingapore\b", r"\bjapan\b", r"\bkorea\b", r"\bchina\b", r"\bhong kong\b",
    r"\baustralia\b", r"\bnew zealand\b",
    r"\bcanada\b", r"\btoronto\b", r"\bvancouver\b",
    r"\bmexico\b", r"\bbrazil\b",
    r"\bmiddle east\b", r"\buae\b", r"\bdubai\b",
    r"\bafrica\b", r"\bnigeria\b", r"\bsouth africa\b",
]
NON_US_RE = re.compile("|".join(NON_US_REGION_PATTERNS), re.IGNORECASE)

US_STATE_ABBR = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC",
}

HARD_REQ_PATTERNS = [
    r"security clearance", r"\btop secret\b", r"\bts/sci\b",
    r"\bcpa required\b", r"\bcpa license\b",
    r"\bjd required\b", r"\bjuris doctor\b", r"\bbar admission\b", r"licensed attorney",
    r"active nursing license", r"\brn license\b", r"\bmd required\b",
    r"professional engineer license", r"\bpe license\b",
    r"series 7", r"series 63", r"series 65",
]
HARD_REQ_RE = re.compile("|".join(HARD_REQ_PATTERNS), re.IGNORECASE)

DEPRIORITIZE_TOKENS = {
    "databricks", "snowflake", "confluent", "hashicorp", "cockroachlabs",
    "mongodb", "elastic", "redis", "neo4j", "starburst", "clickhouse",
    "crowdstrike", "sentinelone", "wizinc", "wiz", "snyk", "zscaler",
    "rapid7", "tenable", "lacework", "orca", "orcasecurity", "cyberark",
    "recordedfuture", "hackerone", "axonius", "armis", "abnormalsecurity",
    "huntress", "verkada", "materialsecurity", "obsidiansecurity",
    "netskope", "illumio", "dragos", "exabeam", "sumologic",
    "cloudflare", "fastly", "vercel", "netlify", "render", "railway",
    "temporal", "kong", "postman", "sourcegraph", "circleci",
}

YEARS_RE = re.compile(
    r"(\d{1,2})\+?\s*(?:to|-|\u2013)?\s*(\d{0,2})?\s*\+?\s*years?\s*(?:of\s*)?(?:relevant\s*)?experience",
    re.IGNORECASE,
)


def title_ok(title):
    if EXCLUDE_RE.search(title):
        return False
    t = title.lower()
    return any(k in t for k in INCLUDE)


def location_ok(job):
    title = job.get("title", "")
    loc   = (job.get("location") or "").strip()
    if NON_US_RE.search(title):
        return False
    if loc and NON_US_RE.search(loc):
        return False
    if not loc:
        return True
    ll = loc.lower()
    if "united states" in ll or ", usa" in ll or " usa" in ll or "u.s." in ll:
        return True
    if "remote" in ll and not NON_US_RE.search(loc):
        return True
    m = re.search(r",\s*([A-Z]{2})\b", loc)
    if m and m.group(1) in US_STATE_ABBR:
        return True
    return False


def posting_age_ok(job):
    posted = job.get("posted_at") or job.get("found_at", "")
    if not posted:
        return True
    try:
        posted_dt = datetime.fromisoformat(posted.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - posted_dt
        return age <= timedelta(days=MAX_AGE_DAYS)
    except Exception:
        return True


def years_required_ok(description):
    matches = YEARS_RE.findall(description[:4000])
    if not matches:
        return True
    for lo, hi in matches:
        top = int(hi) if hi else int(lo)
        if top > MAX_YEARS_REQUIRED + 2:
            return False
    return True


def hard_requirements_ok(description):
    return not HARD_REQ_RE.search(description[:6000])


def deprioritized(company_token):
    return company_token.lower() in DEPRIORITIZE_TOKENS



def strip_html(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()

def extract_salary(text):
    patterns = [
        r"\$[\d,]+\s*[-to]+\s*\$[\d,]+\s*(?:per year|annually|/yr|/year)?",
        r"\$[\d,]+[Kk]\s*[-to]+\s*\$[\d,]+[Kk]",
        r"salary[:\s]+\$[\d,]+", r"compensation[:\s]+\$[\d,]+",
        r"\$[\d,]+\s*(?:per year|annually|/yr)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return ""

def extract_work_type(title, location, description):
    t = (title + " " + location + " " + description[:500]).lower()
    if "remote" in t: return "Remote"
    if "hybrid" in t: return "Hybrid"
    if "onsite" in t or "on-site" in t or "in office" in t: return "On-site"
    return ""

def extract_seniority(title):
    t = title.lower()
    if "senior" in t or "sr." in t or "sr " in t: return "Senior"
    if "associate" in t or "junior" in t or "jr " in t: return "Associate"
    if "lead" in t: return "Lead"
    if "manager" in t or "mgr" in t: return "Manager"
    return ""

def fetch_greenhouse(token):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true", timeout=10)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        desc = strip_html(j.get("content", ""))
        loc = (j.get("location") or {}).get("name", "")
        title = j["title"]
        out.append({"id": f"gh:{token}:{j['id']}", "company": token, "title": title,
                     "location": loc, "url": j["absolute_url"], "description": desc,
                     "salary": extract_salary(desc), "work_type": extract_work_type(title, loc, desc),
                     "seniority": extract_seniority(title), "posted_at": j.get("updated_at", "")})
    return out

def fetch_lever(token):
    r = requests.get(f"https://api.lever.co/v0/postings/{token}?mode=json", timeout=10)
    r.raise_for_status()
    out = []
    for j in r.json():
        desc = strip_html(j.get("descriptionPlain") or j.get("description") or "")
        loc = (j.get("categories") or {}).get("location", "")
        title = j["text"]
        posted = ""
        if j.get("createdAt"):
            try:
                posted = datetime.fromtimestamp(j["createdAt"]/1000, tz=timezone.utc).isoformat()
            except Exception:
                pass
        salary = ""
        sr = j.get("salaryRange") or {}
        if sr.get("min") and sr.get("max"):
            cur = sr.get("currency", "USD")
            sym = "$" if cur == "USD" else f"{cur} "
            salary = f"{sym}{int(sr['min']):,} - {sym}{int(sr['max']):,}"
        if not salary:
            salary = extract_salary(desc)
        out.append({"id": f"lv:{token}:{j['id']}", "company": token, "title": title,
                     "location": loc, "url": j["hostedUrl"], "description": desc,
                     "salary": salary, "work_type": extract_work_type(title, loc, desc),
                     "seniority": extract_seniority(title), "posted_at": posted})
    return out

def fetch_ashby(token):
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true", timeout=10)
    r.raise_for_status()
    data = r.json()
    postings = data.get("jobs", []) if isinstance(data, dict) else data
    out = []
    for j in postings:
        title = j.get("title", "")
        loc = j.get("location", "") or ""
        desc = strip_html(j.get("descriptionPlain") or j.get("descriptionHtml") or "")
        comp = j.get("compensation") or {}
        salary = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary") or extract_salary(desc)
        wt = j.get("workplaceType", "") or ("Remote" if j.get("isRemote") else extract_work_type(title, loc, desc))
        out.append({"id": f"ab:{token}:{j.get('id','')}", "company": token, "title": title,
                     "location": loc, "url": j.get("jobUrl") or j.get("applyUrl", ""), "description": desc,
                     "salary": salary, "work_type": wt, "seniority": extract_seniority(title),
                     "posted_at": j.get("publishedAt", "") or j.get("updatedAt", "")})
    return out

def fetch_workable(token):
    r = requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{token}", timeout=10)
    r.raise_for_status()
    data = r.json()
    postings = data.get("jobs", [])
    out = []
    for j in postings:
        title = j.get("title", "")
        loc = j.get("location", {}) or {}
        loc_str = ", ".join(filter(None, [loc.get("city",""), loc.get("region",""), loc.get("country","")]))
        desc = strip_html(j.get("description", ""))
        url = j.get("url") or f"https://apply.workable.com/{token}/j/{j.get('shortcode','')}"
        out.append({"id": f"wk:{token}:{j.get('shortcode', j.get('id',''))}", "company": token, "title": title,
                     "location": loc_str, "url": url, "description": desc,
                     "salary": extract_salary(desc), "work_type": extract_work_type(title, loc_str, desc),
                     "seniority": extract_seniority(title), "posted_at": j.get("published_on", "") or j.get("created_at", "")})
    return out

def fetch_recruitee(token):
    r = requests.get(f"https://{token}.recruitee.com/api/offers/", timeout=10)
    r.raise_for_status()
    data = r.json()
    postings = data.get("offers", [])
    out = []
    for j in postings:
        title = j.get("title", "")
        loc = ", ".join(filter(None, [j.get("city",""), j.get("country","")]))
        desc = strip_html(j.get("description", "") or j.get("requirements", ""))
        out.append({"id": f"rc:{token}:{j.get('id','')}", "company": token, "title": title,
                     "location": loc, "url": j.get("careers_url", ""), "description": desc,
                     "salary": extract_salary(desc), "work_type": extract_work_type(title, loc, desc),
                     "seniority": extract_seniority(title), "posted_at": j.get("published_at", "") or j.get("created_at", "")})
    return out

def fetch_personio(token):
    r = requests.get(f"https://{token}.jobs.personio.de/xml", timeout=10)
    r.raise_for_status()
    import xml.etree.ElementTree as ET
    root = ET.fromstring(r.content)
    out = []
    for j in root.findall(".//position"):
        def gt(tag):
            el = j.find(tag)
            return el.text.strip() if el is not None and el.text else ""
        title = gt("name")
        loc = gt("office")
        desc_parts = [gt("jobDescriptions/jobDescription/name") or "",
                      gt("jobDescriptions/jobDescription/jobDescriptionValue") or ""]
        desc = strip_html(" ".join(desc_parts))
        job_id = gt("id")
        out.append({"id": f"pr:{token}:{job_id}", "company": token, "title": title,
                     "location": loc, "url": f"https://{token}.jobs.personio.de/job/{job_id}" if job_id else "",
                     "description": desc, "salary": extract_salary(desc),
                     "work_type": extract_work_type(title, loc, desc),
                     "seniority": extract_seniority(title), "posted_at": gt("createdAt")})
    return out

FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby,
            "workable": fetch_workable, "recruitee": fetch_recruitee, "personio": fetch_personio}


SCORE_PROMPT = """You are an expert recruiter scoring how well a candidate fits a specific posting. Output ONLY a JSON object, no markdown, no preamble.

{
  "overall_score": 0-100,
  "verdict": "Top Choice"|"Strong Fit"|"Worth Applying"|"Stretch"|"Weak Fit",
  "verdict_note": "one sentence, specific to this posting, not generic",
  "breakdown": [
    {"category": "Title & Seniority Match", "score": 0-100, "note": "short"},
    {"category": "Skills & Keyword Match",  "score": 0-100, "note": "short"},
    {"category": "Domain & Industry Fit",   "score": 0-100, "note": "short"},
    {"category": "Track Record Fit",        "score": 0-100, "note": "short"},
    {"category": "Eligibility Fit",         "score": 0-100, "note": "short"}
  ],
  "missing_keywords": ["up to 5 key JD terms not in profile"],
  "top_signal": "strongest reason candidate stands out",
  "biggest_gap": "biggest risk or gap"
}

SCORING RUBRIC - use this to justify overall_score, do not pick a number that just feels right:
  90-100  Top Choice.       Nearly every stated requirement is met with direct, quantified proof from the profile. No material gap.
  75-89   Strong Fit.       Core requirements are met. At most one moderate gap (e.g. adjacent domain, one missing tool) that a strong track record offsets.
  60-74   Worth Applying.   Real overlap in skills and level, but a genuine gap exists - wrong domain depth, missing a named requirement, or a title/seniority mismatch of one tier.
  40-59   Stretch.          Meaningful mismatch in more than one dimension. Candidate could make a case but it is not the obvious fit.
  0-39    Weak Fit.         Fundamental mismatch in function, seniority, or domain. Applying would not be a good use of time.

CRITICAL - avoid score clustering: do not default to "safe" round numbers like 72, 75, or 78 out of habit. Two different postings with two different real gaps must not receive the same score just because they feel similarly qualified. Let the actual specifics of THIS posting (which requirements are met, which are not, how deep the gap is) produce a number that could plausibly differ by 1-15 points from the last posting you scored, even within the same verdict band. Use the full 0-100 range across a batch of postings, not just the 60-80 window.

Target band: Manager / Senior Manager level. Keep every note under 12 words, and make each note specific to this posting - never a generic phrase that could apply to any GTM/growth/PMM role.
Eligibility: candidate needs H1B sponsorship. Score this higher for large, established employers and lower for small startups unless sponsorship is explicitly stated in the posting.
Be honest, do not inflate scores. Return ONLY the JSON object, nothing after the closing brace."""


def extract_json(text):
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    start = s.find("{")
    if start == -1:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if esc: esc = False; continue
        if c == "\\": esc = True; continue
        if c == '"': in_str = not in_str; continue
        if in_str: continue
        if c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try: return json.loads(s[start:i+1])
                except json.JSONDecodeError: break
    frag = s[start:]
    if in_str: frag += '"'
    frag = re.sub(r",\s*$", "", frag)
    frag += "]" * max(0, frag.count("[") - frag.count("]"))
    frag += "}" * max(0, frag.count("{") - frag.count("}"))
    try: return json.loads(frag)
    except json.JSONDecodeError: return None


def claude(system, user, max_tokens):
    global estimated_spend
    in_t = (len(system) + len(user)) // 4
    cost = in_t * COSTS["haiku"]["input"] + max_tokens * COSTS["haiku"]["output"]
    if estimated_spend + cost > MAX_SPEND:
        raise RuntimeError(f"Spend cap ${MAX_SPEND} reached (${estimated_spend:.4f} + ~${cost:.4f}).")
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": SCORE_MODEL, "max_tokens": max_tokens, "system": system,
              "messages": [{"role": "user", "content": user}]},
        timeout=60)
    resp.raise_for_status()
    estimated_spend += cost
    text = next((b["text"] for b in resp.json().get("content", []) if b.get("type") == "text"), None)
    return extract_json(text) if text else None


def context(profile, job):
    return (f"CANDIDATE PROFILE:\n{profile}\n\n"
            f"WORK AUTHORIZATION: {AUTH_LABELS.get(AUTH_STATUS)}\n\n"
            f"JOB POSTING:\nTitle: {job['title']}\nCompany: {job['company']}\n"
            f"Location: {job['location']}\n\n{job['description'][:5000]}")


def load(path, default):
    return json.load(open(path)) if os.path.exists(path) else default

def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(data, open(path, "w"), indent=2)

def slugify(t):
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def entry_md(entry, short=False):
    j, r = entry["job"], entry["result"]
    top = " [TOP MATCH]" if r.get("overall_score", 0) >= 88 else ""
    dep = " (deprioritized company)" if entry.get("deprioritized") else ""
    lines = [
        f"### {r.get('overall_score')} -- {j['title']} @ {j['company'].title()} ({j['location']}){top}{dep}",
        f"**{r.get('verdict')}** -- {r.get('verdict_note')}",
        f"[View posting]({j['url']})",
    ]
    if not short:
        lines += ["", f"- Top signal: {r.get('top_signal')}", f"- Biggest gap: {r.get('biggest_gap')}"]
        if r.get("missing_keywords"):
            lines.append(f"- Missing keywords: {', '.join(r['missing_keywords'])}")
    lines.append("")
    return lines


def write_report(all_results, new_results, health):
    lines = ["# Job Radar", "",
             f"Run: {datetime.now(timezone.utc).isoformat()}", "",
             f"Companies: {health['total']} checked, {health['connected']} connected, {health['failed']} failed",
             f"Estimated spend this run: ${estimated_spend:.4f}", ""]
    if new_results:
        lines += [f"## New this run ({len(new_results)})", ""]
        for e in sorted(new_results, key=lambda x: x["result"].get("overall_score", 0), reverse=True):
            lines.extend(entry_md(e))
    else:
        lines += ["## No new matching postings this run.", ""]
    lines += ["## Top 15 all-time", ""]
    for e in sorted(all_results, key=lambda x: x["result"].get("overall_score", 0), reverse=True)[:15]:
        lines.extend(entry_md(e, short=True))
    os.makedirs(RESULTS_DIR, exist_ok=True)
    open(REPORT_PATH, "w").write("\n".join(lines))


def write_csv(all_results):
    """Writes results/tracker.csv -- every posting ever scored, one row each,
    sorted newest first. Designed to be pulled into Google Sheets via
    IMPORTDATA() so the sheet updates itself on every run, at zero cost."""
    import csv as csv_mod
    path = os.path.join(RESULTS_DIR, "tracker.csv")
    cols = ["date_found", "score", "verdict", "company", "title", "location",
            "work_type", "seniority", "salary", "sponsorship_likely",
            "posted_age_days", "deprioritized", "top_signal", "biggest_gap",
            "missing_keywords", "posting_url"]

    def sponsor_likely(result):
        elig = next((b for b in result.get("breakdown", []) if "Eligibility" in b.get("category", "")), None)
        return "Yes" if elig and elig.get("score", 0) >= 65 else ""

    def posted_days(job, found_at):
        posted = job.get("posted_at") or found_at
        try:
            dt = datetime.fromisoformat(posted.replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - dt).days
        except Exception:
            return ""

    rows = []
    for e in sorted(all_results, key=lambda x: x.get("found_at", ""), reverse=True):
        j, r = e["job"], e["result"]
        rows.append([
            e.get("found_at", "")[:10],
            r.get("overall_score", ""),
            r.get("verdict", ""),
            j.get("company", "").replace("-", " ").title(),
            j.get("title", ""),
            j.get("location", ""),
            j.get("work_type", ""),
            j.get("seniority", ""),
            j.get("salary", ""),
            sponsor_likely(r),
            posted_days(j, e.get("found_at", "")),
            "Yes" if e.get("deprioritized") else "",
            r.get("top_signal", ""),
            r.get("biggest_gap", ""),
            "; ".join(r.get("missing_keywords", [])),
            j.get("url", ""),
        ])

    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv_mod.writer(f)
        w.writerow(cols)
        w.writerows(rows)


def main():
    global estimated_spend
    if not API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY is not set.")

    print(f"Job Radar starting -- {datetime.now(timezone.utc).isoformat()}")
    print(f"Score model:  {SCORE_MODEL}")
    print(f"Spend cap:    ${MAX_SPEND}")
    print(f"Top tier:     {THRESHOLD}+ (display only, no kit generation)")
    print(f"Max age:      {MAX_AGE_DAYS} days")
    print(f"Max years:    {MAX_YEARS_REQUIRED}")
    print("---")

    profile     = open(PROFILE_PATH).read()
    companies   = load(COMPANIES, [])
    seen        = load(SEEN_PATH, {})
    all_results = load(ALL_PATH, [])
    new_results = []
    connected, failed = [], []
    cap_hit = False
    skip_counts = {"title": 0, "location": 0, "age": 0, "years": 0, "hard_req": 0}

    for c in companies:
        if cap_hit:
            break
        fetcher = FETCHERS.get(c.get("ats"))
        if not fetcher:
            failed.append({"name": c["name"], "token": c["token"], "reason": "unknown ats"})
            continue
        try:
            jobs = fetcher(c["token"])
            connected.append({"name": c["name"], "token": c["token"], "ats": c["ats"], "jobs_found": len(jobs)})
        except Exception as e:
            failed.append({"name": c["name"], "token": c["token"], "reason": str(e)})
            print(f"[fail] {c['name']} ({c['token']}): {e}")
            continue

        for job in jobs:
            if job["id"] in seen:
                continue

            if not title_ok(job["title"]):
                seen[job["id"]] = {"skipped": True, "reason": "title"}
                skip_counts["title"] += 1
                continue
            if not location_ok(job):
                seen[job["id"]] = {"skipped": True, "reason": "non-US location"}
                skip_counts["location"] += 1
                continue
            if not posting_age_ok(job):
                seen[job["id"]] = {"skipped": True, "reason": "too old"}
                skip_counts["age"] += 1
                continue
            if not years_required_ok(job["description"]):
                seen[job["id"]] = {"skipped": True, "reason": "years required too high"}
                skip_counts["years"] += 1
                continue
            if not hard_requirements_ok(job["description"]):
                seen[job["id"]] = {"skipped": True, "reason": "hard requirement mismatch"}
                skip_counts["hard_req"] += 1
                continue

            print(f"[score] {job['title']} @ {job['company']}")
            try:
                result = claude(SCORE_PROMPT, context(profile, job), 700)
            except RuntimeError as e:
                print(f"\n[SPEND CAP] {e}")
                print(f"Stopping the entire run here -- {len(new_results)} postings scored so far.")
                cap_hit = True
                break
            except Exception as e:
                print(f"  [warn] scoring failed: {e}")
                continue
            if not result:
                seen[job["id"]] = {"scored": False}
                continue

            score = result.get("overall_score", 0)
            dep = deprioritized(job["company"])
            print(f"  score: {score} -- {result.get('verdict')}{' [deprioritized company]' if dep else ''}")

            entry = {"job": job, "result": result, "deprioritized": dep,
                     "found_at": datetime.now(timezone.utc).isoformat()}
            new_results.append(entry)
            all_results.append(entry)
            seen[job["id"]] = {"scored": True, "score": score}
            time.sleep(0.4)

    save(SEEN_PATH, seen)
    save(ALL_PATH, all_results)

    health = {
        "total": len(companies), "connected": len(connected), "failed": len(failed),
        "rate": f"{round(len(connected)/max(len(companies),1)*100)}%",
        "estimated_spend": round(estimated_spend, 4),
        "run_at": datetime.now(timezone.utc).isoformat(),
        "skip_counts": skip_counts,
    }
    save(HEALTH_PATH, {
        "run_at": health["run_at"], "summary": health,
        "connected": sorted(connected, key=lambda x: x["jobs_found"], reverse=True),
        "failed": failed,
    })
    write_report(all_results, new_results, health)
    write_csv(all_results)

    print(f"\n{'='*60}")
    print(f"Companies:    {len(companies)} checked, {len(connected)} connected, {len(failed)} failed")
    print(f"New scored:   {len(new_results)}")
    print(f"Skipped:      {skip_counts}")
    print(f"Spend:        ${estimated_spend:.4f}")
    print(f"{'='*60}")
    if failed:
        print("\nFailed tokens:")
        for f in failed:
            print(f"  - {f['name']} ({f['token']}): {f['reason'][:80]}")

if __name__ == "__main__":
    main()
