# Job Sources for a Scraper

Part A ranks scrapable sources by how easy and legal they are to pull from. Part B is the full directory by category. Sites that block scrapers or ban them in their terms (LinkedIn, Indeed, Glassdoor, ZipRecruiter, Monster, Naukri, Wellfound, Google for Jobs and similar) have been removed. URLs are from memory, so verify before relying on any single one, and check each site's robots.txt and terms.

---

# Part A: Scraper source tiers

## Tier 1: Official APIs and open feeds (start here)
Legal, stable, no scraping needed. Mostly plain `requests` + JSON.

| Source | Access | Coverage |
|---|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/<co>/jobs` | Thousands of tech companies |
| Lever | `api.lever.co/v0/postings/<co>?mode=json` | Thousands of tech companies |
| Ashby | `api.ashbyhq.com/posting-api/job-board/<co>` | Startups |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/<co>/postings` | Mid and large companies |
| Workable | `apply.workable.com/api/v1/widget/accounts/<co>` | SMBs |
| Recruitee, Personio, Teamtailor | Public JSON/XML feed per company | Europe |
| USAJOBS | Free API key, developer.usajobs.gov | All US federal jobs |
| Canada Job Bank | Open data, open.canada.ca | Canada |
| Arbeitnow | arbeitnow.com/api/job-board-api | Germany / EU |
| Remotive | remotive.com/api/remote-jobs | Remote |
| RemoteOK | remoteok.com/api | Remote |
| Himalayas | Public API | Remote |
| We Work Remotely | RSS feeds | Remote |
| HN "Who is hiring" | Algolia HN API | Tech |
| Adzuna | Free API key, 15+ countries | Aggregator |
| Jooble | Free API key, 70+ countries | Aggregator |
| The Muse | Public API | US |
| Reed | API key | UK |
| ReliefWeb | api.reliefweb.int | Humanitarian |
| EURES | Portal, partial API | EU |

**Best lever:** build a list of company slugs and hit the ATS endpoints. That is the largest direct-from-employer source. Find slugs via `site:boards.greenhouse.io` style searches or public lists on GitHub.

## Tier 2: Scrapable, check robots.txt and terms
- **Boards with light protection:** TimesJobs, Shine, Freshersworld, Internshala, Foundit, Instahyre, Cutshort, Hirist, Dice, Totaljobs, Reed, Seek, Bayt, Rozee.pk, Bdjobs, Jobberman.
- **Government portals:** ncs.gov.in, ssc.gov.in, upsc.gov.in, ibps.in and similar. Public pages, no login, often PDFs.
- **Niche boards:** Web3.career, CryptoJobsList, Climatebase, Idealist, HigherEdJobs, Rigzone. Simple HTML and mostly tolerant.
- **Unofficial India alert sites:** FreeJobAlert, SarkariResult, SarkariNaukri. Easy to scrape but duplicated and mixed quality. Use as leads and confirm on the official site.

## Suggested stack
1. **Ingest:** Tier 1 on a cron schedule.
2. **Dedupe:** key on `(company, normalized title, location)` plus a hash of the apply URL.
3. **Tier 2:** `httpx` + `selectolax`; Playwright only for JS-rendered pages. Respect robots.txt, rate-limit, cache with ETag / Last-Modified.
4. **Store:** SQLite or Postgres, with `first_seen` / `last_seen` to expire dead listings.

---

# Part B: Full directory by category

## 1. Global general job boards
| Site | URL | Best for |
|---|---|---|
| Jooble | jooble.org | Aggregator, global |
| Adzuna | adzuna.com | Aggregator + salary data |
| Talent.com | talent.com | Aggregator |
| Jobrapido | jobrapido.com | Aggregator |
| Recruit.net | recruit.net | Aggregator |
| Snagajob | snagajob.com | Hourly/part-time (US) |

## 2. India
| Site | URL | Notes |
|---|---|---|
| Foundit (ex-Monster India) | foundit.in | General |
| Shine | shine.com | General |
| TimesJobs | timesjobs.com | General |
| Apna | apna.co | Blue/grey collar |
| WorkIndia | workindia.in | Blue collar |
| Internshala | internshala.com | Internships/freshers |
| Instahyre | instahyre.com | Tech, curated |
| Cutshort | cutshort.io | Tech/startups |
| Hirist | hirist.tech | Tech |
| iimjobs / hirist | iimjobs.com | Management |
| Freshersworld | freshersworld.com | Freshers |
| National Career Service (Govt) | ncs.gov.in | Official govt job portal |
| Employment News | employmentnews.gov.in | Govt jobs gazette |
| Sarkari Result (unofficial) | sarkariresult.com | Govt job alerts, not official |

### India government recruitment (official)
| Body | URL |
|---|---|
| UPSC | upsc.gov.in |
| SSC | ssc.gov.in |
| IBPS | ibps.in |
| SBI Careers | sbi.co.in/web/careers |
| RBI | rbi.org.in (Opportunities) |
| RRB / Railways | indianrailways.gov.in |
| DRDO | drdo.gov.in |
| ISRO | isro.gov.in |
| Indian Army | joinindianarmy.nic.in |
| Indian Navy | joinindiannavy.gov.in |
| Indian Air Force | indianairforce.nic.in |
| LIC | licindia.in |
| FreeJobAlert (aggregator) | freejobalert.com |

## 3. Government portals (other countries)
| Country | Site | URL |
|---|---|---|
| USA | USAJOBS | usajobs.gov |
| USA | Job Bank (state-level) | careeronestop.org |
| UK | Find a Job | findajob.dwp.gov.uk |
| UK | Civil Service Jobs | civilservicejobs.service.gov.uk |
| Canada | Job Bank | jobbank.gc.ca |
| Australia | Workforce Australia | workforceaustralia.gov.au |
| Australia | APS Jobs | apsjobs.gov.au |
| EU | EURES | eures.europa.eu |
| EU | EPSO | epso.europa.eu |
| Germany | Bundesagentur für Arbeit | arbeitsagentur.de |
| France | France Travail | francetravail.fr |
| Singapore | MyCareersFuture | mycareersfuture.gov.sg |
| UAE | (Dubai Careers) | dubaicareers.ae |
| UN | UN Careers | careers.un.org |
| World Bank | Jobs | worldbank.org/en/about/careers |
| IMF | Careers | imf.org/en/About/Recruitment |
| NATO | Careers | nato.int/cps/en/natohq/recruit.htm |

## 4. Tech, startup & remote
| Site | URL |
|---|---|
| Y Combinator Work at a Startup | workatastartup.com |
| Hacker News "Who is hiring" | news.ycombinator.com/jobs |
| Dice | dice.com |
| Built In | builtin.com |
| Stack Overflow Jobs (retired) | n/a |
| Otta / Welcome to the Jungle | welcometothejungle.com |
| Levels.fyi Jobs | levels.fyi/jobs |
| We Work Remotely | weworkremotely.com |
| Remote OK | remoteok.com |
| Remote.co | remote.co |
| FlexJobs | flexjobs.com |
| Working Nomads | workingnomads.com |
| Himalayas | himalayas.app |
| Remotive | remotive.com |
| Arc | arc.dev |
| Turing | turing.com |
| Toptal | toptal.com |
| Crossover | crossover.com |
| JustRemote | justremote.co |
| Jobspresso | jobspresso.co |
| NoDesk | nodesk.co |

## 5. Freelance / gig
Upwork and Fiverr removed (scraping banned, bot-blocked).

| Site | URL |
|---|---|
| Freelancer | freelancer.com |
| PeoplePerHour | peopleperhour.com |
| Guru | guru.com |
| Contra | contra.com |
| Braintrust | usebraintrust.com |

## 6. Niche fields
| Field | Site | URL |
|---|---|---|
| Design | Dribbble Jobs | dribbble.com/jobs |
| Design | Behance Jobs | behance.net/joblist |
| Data/AI | Kaggle Jobs | kaggle.com/jobs |
| Data/AI | AI-Jobs | ai-jobs.net |
| Security | CyberSecJobs | cybersecjobs.com |
| Crypto/Web3 | Web3.career | web3.career |
| Crypto/Web3 | CryptoJobsList | cryptojobslist.com |
| Healthcare | Health eCareers | healthecareers.com |
| Academia | Academic Jobs | academicjobs.com |
| Academia | HigherEdJobs | higheredjobs.com |
| Academia | Times Higher Ed | timeshighereducation.com/unijobs |
| Academia | Nature Careers | nature.com/naturecareers |
| Nonprofit | Idealist | idealist.org |
| Nonprofit | ReliefWeb | reliefweb.int/jobs |
| Marketing | MarketingHire / Mediabistro | mediabistro.com |
| Finance | eFinancialCareers | efinancialcareers.com |
| Legal | LawCrossing | lawcrossing.com |
| Sales | SalesJobs | salesjobs.com |
| Engineering | Engineering.com / EngineerJobs | engineerjobs.com |
| Hospitality | Hcareers | hcareers.com |
| Energy | Rigzone | rigzone.com |
| Creative | Creativepool | creativepool.com |
| Video games | GameJobs / Work With Indies | workwithindies.com |
| Climate | Climatebase | climatebase.org |
| Environment | Conservation Job Board | conservationjobboard.com |
| Interns/students | WayUp | wayup.com |
| Interns/students | RippleMatch | ripplematch.com |
| Interns/students | Chegg Internships | internships.com |

## 7. Regional boards
| Region | Site | URL |
|---|---|---|
| UK | Reed | reed.co.uk |
| UK | Totaljobs | totaljobs.com |
| UK | CV-Library | cv-library.co.uk |
| UK | Jobsite | jobsite.co.uk |
| Germany | StepStone | stepstone.de |
| Netherlands | Nationale Vacaturebank | nationalevacaturebank.nl |
| Ireland | IrishJobs | irishjobs.ie |
| Canada | Workopolis | workopolis.com |
| Australia/NZ | Seek | seek.com.au / seek.co.nz |
| Singapore | JobStreet | jobstreet.com.sg |
| SE Asia | JobStreet | jobstreet.com |
| Philippines | JobStreet / Kalibrr | kalibrr.com |
| Japan | Daijob / GaijinPot | gaijinpot.com |
| China | Zhaopin / 51job | zhaopin.com / 51job.com |
| Middle East | Bayt | bayt.com |
| Middle East | GulfTalent | gulftalent.com |
| Middle East | Naukrigulf | naukrigulf.com |
| Africa | Jobberman (Nigeria) | jobberman.com |
| Africa | BrighterMonday | brightermonday.com |
| Africa | PNet (S. Africa) | pnet.co.za |
| LatAm | Computrabajo | computrabajo.com |
| LatAm | Catho (Brazil) | catho.com.br |
| Pakistan | Rozee.pk | rozee.pk |
| Bangladesh | Bdjobs | bdjobs.com |
| Sri Lanka | TopJobs | topjobs.lk |

## 8. Applicant-tracking portals (company career pages)
Most large employers post on their own site first. Search by company, or use these ATS hosts:

| ATS | URL pattern |
|---|---|
| Greenhouse | boards.greenhouse.io/<company> |
| Lever | jobs.lever.co/<company> |
| Workday | <company>.wd1.myworkdayjobs.com |
| Ashby | jobs.ashbyhq.com/<company> |
| SmartRecruiters | jobs.smartrecruiters.com/<company> |
| iCIMS | careers-<company>.icims.com |
| BambooHR | <company>.bamboohr.com/careers |
| Taleo (Oracle) | <company>.taleo.net |

### Big-tech career pages
| Company | URL |
|---|---|
| Google | google.com/about/careers/applications |
| Microsoft | careers.microsoft.com |
| Amazon | amazon.jobs |
| Apple | jobs.apple.com |
| Meta | metacareers.com |
| Netflix | jobs.netflix.com |
| Nvidia | nvidia.com/en-us/about-nvidia/careers |
| Tesla | tesla.com/careers |
| OpenAI | openai.com/careers |
| Anthropic | anthropic.com/careers |
| Salesforce | salesforce.com/company/careers |
| Adobe | adobe.com/careers |
| IBM | ibm.com/careers |
| Oracle | oracle.com/careers |
| TCS | tcs.com/careers |
| Infosys | infosys.com/careers |
| Wipro | wipro.com/careers |
| HCLTech | hcltech.com/careers |
| Accenture | accenture.com/careers |
| Deloitte | deloitte.com/careers |

## Tips
- Set up alerts on 3–4 boards, not 40.
- Apply on the company's own page when possible; aggregator links often go stale.
- Gov't jobs: only trust `.gov`, `.gov.in`, `.nic.in`, `.gc.ca` etc., not alert blogs.
