/**
 * generate_resume.js
 * Generates a tailored resume .docx matching Raunak's exact resume format.
 * Called by search.py via: node generate_resume.js <input_json> <output_path>
 */

const { Document, Packer, Paragraph, TextRun, AlignmentType, BorderStyle, TabStopType, WidthType } = require('docx');
const fs = require('fs');

const inputPath  = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  console.error('Usage: node generate_resume.js <input.json> <output.docx>');
  process.exit(1);
}

const data = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const { summary_line, experience, skills_to_surface, job_title, job_company } = data;

const NAVY  = "1F3864";
const BLACK = "000000";
const GRAY  = "555555";

const hr = () => new Paragraph({
  border: { bottom: { color: NAVY, size: 6, style: BorderStyle.SINGLE } },
  spacing: { after: 60 },
});

const sectionHeader = (text) => new Paragraph({
  children: [new TextRun({ text, bold: true, size: 20, font: "Calibri", color: NAVY })],
  border: { bottom: { color: NAVY, size: 4, style: BorderStyle.SINGLE } },
  spacing: { before: 120, after: 60 },
});

const bullet = (text) => new Paragraph({
  children: [new TextRun({ text, size: 18, font: "Calibri", color: BLACK })],
  bullet: { level: 0 },
  spacing: { after: 40 },
});

const roleHeader = (title, company, dates) => [
  new Paragraph({
    children: [
      new TextRun({ text: company, bold: true, size: 19, font: "Calibri", color: BLACK }),
    ],
    spacing: { before: 100, after: 20 },
  }),
  new Paragraph({
    children: [
      new TextRun({ text: title, bold: true, size: 18, font: "Calibri", color: NAVY }),
      new TextRun({ text: "\t" + dates, size: 18, font: "Calibri", color: GRAY }),
    ],
    tabStops: [{ type: TabStopType.RIGHT, position: 9360 }],
    spacing: { after: 30 },
  }),
];

const skillLine = (label, items) => new Paragraph({
  children: [
    new TextRun({ text: label + ": ", bold: true, size: 18, font: "Calibri", color: NAVY }),
    new TextRun({ text: items, size: 18, font: "Calibri", color: BLACK }),
  ],
  spacing: { after: 40 },
});

// Static profile data
const staticSkills = {
  analytics: "GEO (Generative Engine Optimization), Funnel Analysis, CRM (Salesforce), A/B Testing, KPI Reporting, Tableau, Excel, Market Research",
  technical:  "Python (Data Analysis), R (Statistical Analysis)",
  leadership: "FIFA World Cup 2026 Volunteer | Campaign Head, Education for All Initiative",
};

const doc = new Document({
  numbering: {
    config: [{
      reference: "bullet-list",
      levels: [{
        level: 0, format: "bullet", text: "\u2022",
        alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 320, hanging: 160 } } },
      }],
    }],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 580, bottom: 580, left: 800, right: 800 },
      },
    },
    children: [
      // Name
      new Paragraph({
        children: [new TextRun({ text: "Raunak Jaiswal", bold: true, size: 32, font: "Calibri", color: NAVY })],
        alignment: AlignmentType.CENTER,
        spacing: { after: 40 },
      }),
      // Contact
      new Paragraph({
        children: [new TextRun({ text: "Boston, MA  |  857-961-9090  |  rounakrj77@gmail.com  |  linkedin.com/in/raunakrj", size: 17, font: "Calibri", color: GRAY })],
        alignment: AlignmentType.CENTER,
        spacing: { after: 80 },
      }),

      // Summary
      sectionHeader("SUMMARY"),
      new Paragraph({
        children: [new TextRun({ text: summary_line || "GTM strategist with 8+ years scaling EdTech revenue across global markets (US, UK, Middle East). Track record of 25–38% growth through funnel optimization, market positioning, and CRM-led execution. At BU Questrom (MBA + MS Digital Technology, May 2027), leading client-facing GEO research on brand discoverability across AI platforms.", size: 18, font: "Calibri", color: BLACK })],
        spacing: { after: 80 },
      }),

      // Education
      sectionHeader("EDUCATION"),
      new Paragraph({
        children: [new TextRun({ text: "Boston University, Questrom School of Business | Boston, MA", bold: true, size: 19, font: "Calibri", color: BLACK })],
        spacing: { before: 60, after: 20 },
      }),
      new Paragraph({
        children: [
          new TextRun({ text: "MBA + MS, Digital Technology", bold: true, size: 18, font: "Calibri", color: NAVY }),
          new TextRun({ text: "\tExpected May 2027", size: 18, font: "Calibri", color: GRAY }),
        ],
        tabStops: [{ type: TabStopType.RIGHT, position: 9360 }],
        spacing: { after: 30 },
      }),
      new Paragraph({
        children: [new TextRun({ text: "Coursework: Data Analytics, Product Management, Marketing Analytics, Operations Management", size: 17, font: "Calibri", color: GRAY })],
        spacing: { after: 40 },
      }),
      new Paragraph({
        children: [new TextRun({ text: "Boston University", bold: true, size: 19, font: "Calibri", color: BLACK })],
        spacing: { before: 60, after: 20 },
      }),
      new Paragraph({
        children: [
          new TextRun({ text: "Graduate Research Assistant - Questrom Consulting Lab", bold: true, size: 18, font: "Calibri", color: NAVY }),
          new TextRun({ text: "\tMay 2026 – Present", size: 18, font: "Calibri", color: GRAY }),
        ],
        tabStops: [{ type: TabStopType.RIGHT, position: 9360 }],
        spacing: { after: 30 },
      }),
      bullet("Built GEO (Generative Engine Optimization) audit framework across 7 AI platforms (ChatGPT, Claude, Gemini, Perplexity, Meltwater, Google, Bing) for a live client, identifying brand visibility gaps in AI-generated answers"),
      bullet("Benchmarked 6 competitor programs across 7 strategic dimensions; developed positioning matrices and GTM recommendations delivered to client stakeholders"),
      new Paragraph({
        children: [new TextRun({ text: "Boston University", bold: true, size: 19, font: "Calibri", color: BLACK })],
        spacing: { before: 60, after: 20 },
      }),
      new Paragraph({
        children: [
          new TextRun({ text: "Teaching Assistant - ES620 Feld Career Coaching + MK854 Graduate Marketing", bold: true, size: 18, font: "Calibri", color: NAVY }),
          new TextRun({ text: "\tJun 2026 – Present", size: 18, font: "Calibri", color: GRAY }),
        ],
        tabStops: [{ type: TabStopType.RIGHT, position: 9360 }],
        spacing: { after: 30 },
      }),
      bullet("Coach MiM candidates on job search strategy, personal brand positioning, and interview preparation"),
      bullet("Support graduate Marketing curriculum delivery covering GTM frameworks, positioning, and brand strategy"),

      // Work Experience
      sectionHeader("WORK EXPERIENCE"),

      // PlanetSpark
      ...roleHeader("Senior Program Manager - Growth & Expansion", "PlanetSpark (Winspark Innovations)", "Sep 2024 – Aug 2025"),
      ...((experience.find(e => e.company?.toLowerCase().includes("planetspark") || e.company?.toLowerCase().includes("winspark")) || { bullets: [
        "Diagnosed a leaky top-of-funnel costing 30% of leads; redesigned multi-channel GTM sequence (paid, outbound, referral), lifting customer acquisition 38% in two quarters",
        "Managed international sales pipeline across US, UK, and Middle East, increasing recurring revenue by 19%",
        "Rebuilt sales onboarding systems delivering 42% productivity uplift; surfaced a day-14 churn cliff and deployed retention intervention improving 30-day retention by 40%",
        "Implemented CRM-based lead scoring, boosting conversion rates by 27%",
      ]}).bullets.map(b => bullet(b))),

      // BYJU'S header
      new Paragraph({
        children: [new TextRun({ text: "BYJU'S (Think & Learn) | India's largest EdTech, 150M+ users", bold: true, size: 19, font: "Calibri", color: BLACK })],
        spacing: { before: 100, after: 20 },
      }),

      // AGM
      new Paragraph({
        children: [
          new TextRun({ text: "Associate General Manager - Growth Strategy", bold: true, size: 18, font: "Calibri", color: NAVY }),
          new TextRun({ text: "\tApr 2023 – Jun 2024", size: 18, font: "Calibri", color: GRAY }),
        ],
        tabStops: [{ type: TabStopType.RIGHT, position: 9360 }],
        spacing: { after: 30 },
      }),
      ...((experience.find(e => e.title?.toLowerCase().includes("general manager") || e.title?.toLowerCase().includes("agm")) || { bullets: [
        "Identified messaging-market fit gaps across 3 regions; redesigned GTM positioning, driving 25% sales growth in 6 months",
        "Ran ABM campaigns using CRM-driven segmentation to improve high-intent lead conversion across 5 business units",
        "Optimized multi-center sales operations, improving team productivity by 20% and increasing revenue throughput",
      ]}).bullets.map(b => bullet(b))),

      // SPM
      new Paragraph({
        children: [
          new TextRun({ text: "Senior Program Manager - GTM Strategy & Execution", bold: true, size: 18, font: "Calibri", color: NAVY }),
          new TextRun({ text: "\tJul 2021 – Mar 2023", size: 18, font: "Calibri", color: GRAY }),
        ],
        tabStops: [{ type: TabStopType.RIGHT, position: 9360 }],
        spacing: { after: 30 },
      }),
      ...((experience.find(e => e.title?.toLowerCase().includes("senior program manager") && e.title?.toLowerCase().includes("gtm")) || { bullets: [
        "Owned end-to-end D2C funnel from acquisition through conversion, leading a 60+ member cross-functional team",
        "Designed and scaled onboarding for 200+ monthly hires, achieving 45% conversion rate and strengthening D2C revenue",
        "Built KPI performance systems and dashboards enabling faster revenue-driven decision-making",
      ]}).bullets.map(b => bullet(b))),

      // PM BD
      new Paragraph({
        children: [
          new TextRun({ text: "Program Manager - Business Development", bold: true, size: 18, font: "Calibri", color: NAVY }),
          new TextRun({ text: "\tJul 2019 – Jun 2021", size: 18, font: "Calibri", color: GRAY }),
        ],
        tabStops: [{ type: TabStopType.RIGHT, position: 9360 }],
        spacing: { after: 30 },
      }),
      ...((experience.find(e => e.title?.toLowerCase().includes("program manager") && e.title?.toLowerCase().includes("business development") && !e.title?.toLowerCase().includes("associate") && !e.title?.toLowerCase().includes("senior")) || { bullets: [
        "Generated $5M in revenue across 6 quarters through targeted, segmented GTM campaigns",
        "Coordinated cross-functional execution across 4+ teams, improving on-time delivery by 25%",
      ]}).bullets.map(b => bullet(b))),

      // APM BD
      new Paragraph({
        children: [
          new TextRun({ text: "Associate Program Manager - Business Development", bold: true, size: 18, font: "Calibri", color: NAVY }),
          new TextRun({ text: "\tOct 2017 – Jun 2019", size: 18, font: "Calibri", color: GRAY }),
        ],
        tabStops: [{ type: TabStopType.RIGHT, position: 9360 }],
        spacing: { after: 30 },
      }),
      ...((experience.find(e => e.title?.toLowerCase().includes("associate program manager")) || { bullets: [
        "Drove 25% MoM revenue growth through KPI systems; boosted upsell conversion 22% via CRM-led segmentation",
        "Devised pricing model using demand forecasting and user segmentation, improving average revenue per user by 15%",
      ]}).bullets.map(b => bullet(b))),

      // Skills
      sectionHeader("SKILLS"),
      skillLine("GTM & Growth", skills_to_surface?.join(", ") || "Go-to-Market Strategy, Product Marketing, Growth Strategy, Market Positioning, ABM, Competitive Intelligence, D2C Funnel Optimization, Sales Enablement, Product Launch"),
      skillLine("Analytics & Tools", staticSkills.analytics),
      skillLine("Technical", staticSkills.technical),
      skillLine("Leadership & Community", staticSkills.leadership),
    ],
  }],
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(outputPath, buf);
  console.log(`Resume generated: ${outputPath}`);
});
