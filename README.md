# Job Radar

An automation that runs daily on GitHub Actions and:

1. Pulls open postings from company career boards using the public
   Greenhouse and Lever job board APIs (no scraping, no auth).
2. Filters to titles matching your target roles (GTM, Growth, Product
   Marketing, Business Development, Strategy).
3. Scores every new posting against `profile.md` using the Claude API,
   across five signals: title/seniority, skills/keyword, domain fit,
   track record, and eligibility (visa sponsorship).
4. For any posting scoring 70+, generates a tailored resume and a
   specific cover letter — grounded only in your real experience.
5. Writes a ranked report to `results/latest.md` and a connection
   health report to `results/connection_health.json`, and commits both
   back to the repo.

## Setup

1. **Push this folder to a new PRIVATE GitHub repo.** Private matters —
   `profile.md` has your resume in it.

2. **Get an Anthropic API key** from console.anthropic.com (separate
   from Claude Pro; add ~$5 credit). Add it as a repo secret:
   Settings → Secrets and variables → Actions → New repository secret.
   Name it exactly `ANTHROPIC_API_KEY`.

3. **First run — verify the company list.** Go to the Actions tab →
   "Job Radar" → "Run workflow". When it finishes, open
   `results/connection_health.json` (or the Actions run summary). It
   lists every company that connected and every one that failed. Remove
   the failed tokens from `companies.json` and commit — now your list is
   fully verified.

4. **From then on it runs daily at 13:00 UTC automatically.** Check
   `results/latest.md` each morning.

## Adding companies

Each entry in `companies.json` is:
```json
{ "name": "Stripe", "ats": "greenhouse", "token": "stripe" }
```
The token is the slug in the company's job board URL:
`job-boards.greenhouse.io/<token>` or `jobs.lever.co/<token>`.

## Cost

Scoring a posting is a fraction of a cent. A 70+ posting that gets a
resume + cover letter adds a few cents. Realistically well under $5/month.
Switch `JOB_RADAR_MODEL` to a cheaper model or raise
`JOB_RADAR_TAILOR_THRESHOLD` to reduce spend.
