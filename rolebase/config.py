"""Configuration, constants, regexes, and lookup tables for RoleBase."""
import re
from pathlib import Path

# Paths
DIR = Path("data/jobs")  # One JSONL per board (e.g., data/jobs/greenhouse_stripe.jsonl)
COMPANIES = Path("companies.json")  # {"greenhouse": {"stripe": "Stripe"}, ...} slug -> display name
HEALTH = Path("data/health.json")  # Consecutive failures and health metadata per board
KEEP_CLOSED_DAYS = 90  # Closed jobs older than 90 days are pruned to git history

# Network & Concurrency
TIMEOUT = (10, 45)  # (connect_timeout, read_timeout) in seconds
PATIENCE = 2  # Max consecutive empty/dropped responses allowed before trusting board closure
RUN_BUDGET = 16 * 60  # Seconds allowed for a shard scrape before graceful shutdown

# Job level hierarchy (first match wins)
LEVELS = [
    ("intern", r"\bintern(ship)?s?\b|\bco-?op\b|\bworking student\b|\bwerkstudent|\bpraktik|\bazubi\b|\bausbildung"),
    ("junior", r"\bjunior\b|\bjr\b|\bentry[- ]level\b|\bgraduate\b|\bnew grad\b|\bapprentice|\bassociate\b(?! (director|vp|vice))"),
    ("staff+", r"\bstaff\b|\bprincipal\b|\bdistinguished\b|\bfellow\b"),
    ("manager+", r"\bdirector\b|\bhead of\b|\bvp\b|\bvice president\b|\bchief\b|\bgeneral manager\b|\b(engineering|software|data|design|research|sales) manager\b|\bmanager of\b"),
    ("lead", r"\blead\b"),
    ("senior", r"\bsenior\b|\bsr\b"),
]

# Industry and domain categories (first match wins)
CATEGORIES = [
    ("sales", r"\bsales\b|account (executive|manager)|business development|\bbdr\b|\bsdr\b|solutions? (engineer|architect|consultant)|pre-?sales|partnership|revenue|go-to-market|account (partner|representative|associate|specialist|director)"),
    ("design", r"design(?! engineer)|\bux\b|\bui\b|creative"),
    ("data", r"data (scien|analy|engineer)|machine learning|\bml\b|\bai\b|research scientist|applied scientist|analytics|business intelligence|\bbi\b"),
    ("product", r"product (manager|owner|lead)|program manager|\bpm\b|head of product"),
    ("protective", r"guard\b|ochron|vekter|sicherheitsdienst|sicherheitsmitarbeiter|beveilig|doorman|patrol|security (officer|agent|guard)"),
    ("security", r"security|infosec|appsec|penetration|threat"),
    ("engineering", r"engineer|developer|software|devops|\bsre\b|site reliability|back-?end|front-?end|full.?stack|mobile|\bios\b|android|platform|infrastructure|architect|programmer|\bqa\b|quality assurance|sdet|firmware|embedded|technical"),
    ("marketing", r"marketing|growth|\bseo\b|content|communications|brand|social media|community|copywrit"),
    ("support", r"support|customer (success|service|experience)|technical account|help ?desk|implementation|onboarding"),
    ("finance", r"financ|account(ing|ant)|payroll|\btax\b|treasury|audit|controller|fp&a|procurement|banker|teller|underwrit|\bloan|insuranc|adjuster|actuar|fraud|kredit|steuer|\bbank|risk|claims|credit|invest|portfolio|wealth"),
    ("legal", r"legal|counsel|attorney|compliance|paralegal|privacy"),
    ("people", r"recruit|talent|\bpeople\b|\bhr\b|human resources|workplace|\btrainer\b|learning (and|&) development|training (specialist|manager|coordinator)"),
    ("healthcare", r"nurs(e|ing)|physician|clinic(al|ian)|pharmac|medical|therapist|patient|dental|dentist|surgeon|radiolog|caregiver|health ?care|\brn\b|paramedic|phlebotom|\bcna\b|veterinar|psychiat|psycholog|mental health|neurolog|hospital"),
    ("education", r"teacher|professor|instructor|lecturer|tutor|faculty|curriculum|academic|educator|school|teaching"),
    ("science", r"scientist|laborator|chemist|biolog|\blab\b|research (associate|assistant|fellow)|geolog"),
    ("retail", r"\bstore\b|retail|personal train|fitness|\battendant\b|detailer|car wash|lifeguard|cashier|barista|\bcook\b|\bchef\b|kitchen|bartender|hotel|guest (service|experience)|concierge|housekeep|front desk|merchandis|stylist|restaurant|\bcrew\b|\bshift\b|food service|catering|cleaner|reinigung|janitor|arrumadeira|verk.ufer|cafeteria|barber|salon|k.chenhilfe|k.che\b|einzelhandel|kaufmann|gar.om|vendedor|ayudante|hostess|waiter|waitress|meat|grocery|stocker"),
    ("manufacturing", r"painter|mechatronik|mechatronic|manufactur|production (operator|associate|supervisor|technician|worker)|assembl|machinist|weld|electrician|plumb|forklift|fabricat|\bplant\b|\bmill\b|maintenance|operator|operat.r|monteur|fertigung|toolmaker|produktion|produktie|construction|quality|inspector|\bnde\b|operario|op.rateur|lead hand"),
    ("logistics", r"logist|warehouse|\bdelivery (driver|associate|partner)|parts (associate|clerk|specialist)|material handler|\bdriver\b|\blkw\b|fahrer|courier|picker|\bpacker\b|shipping|dispatch|freight"),
    ("administrative", r"assistant|receptionist|secretar|\bclerk|recepcion|data entry|coordinator|administrat"),
    ("social", r"social work|case manag|caseworker|counsel+or|youth|outreach|advocate|community (health|support)"),
    ("consulting", r"consultant|consulting|strategist|advisory|engagement manager|business partner"),
    ("operations", r"operations|\bops\b|supply|logistics|business (analyst|operations)|chief of staff|strategy|facilit|office|warehouse|driver|technician|mechanic|buyer|sourcing|category manager|planner|planning|inventory|distribution|materials|supervisor"),
]

# Tracked technical and domain skills
SKILLS = ("python java javascript typescript rust c++ c# ruby php swift kotlin scala sql nosql react vue angular node.js django "
          "flask spring-boot aws azure gcp kubernetes docker terraform postgres mysql mongodb redis kafka spark snowflake "
          "databricks dbt airflow pytorch tensorflow llm ml graphql linux git ci/cd figma tableau salesforce hubspot jira sap").split()
ALIAS = dict(golang="go", postgresql="postgres", nodejs="node.js", node="node.js", k8s="kubernetes", reactjs="react",
             **{"react.js": "react", "machine learning": "ml", "spring boot": "spring-boot"}, js="javascript", ts="typescript")
SKILLS += ("powerpoint quickbooks erp crm pmp agile scrum budgeting forecasting gaap ifrs payroll procurement negotiation seo copywriting "
           "photoshop illustrator autocad solidworks revit cpr bls acls ehr emr hipaa phlebotomy forklift osha cdl welding haccp hris bilingual spanish").split()
ALIAS.update({"microsoft excel": "excel", "ms excel": "excel", "advanced excel": "excel", "excel spreadsheets": "excel", "microsoft office": "ms_office",
              "ms office": "ms_office", "customer service": "customer_service", "project management": "project_management",
              "six sigma": "six_sigma", "financial modeling": "financial_modeling", "financial modelling": "financial_modeling",
              "accounts payable": "accounts_payable", "accounts receivable": "accounts_receivable", "data entry": "data_entry",
              "supply chain": "supply_chain", "social media": "social_media", "google analytics": "google_analytics",
              "adobe creative suite": "adobe_creative_suite", "patient care": "patient_care", "electronic health records": "ehr",
              "medical coding": "medical_coding", "preventive maintenance": "preventive_maintenance", "food safety": "food_safety",
              "cash handling": "cash_handling", "inventory management": "inventory_management", "lesson planning": "lesson_planning",
              "talent acquisition": "talent_acquisition", "employee relations": "employee_relations", "public speaking": "public_speaking",
              "time management": "time_management", "forklift operator": "forklift"})

SKILL_RX = re.compile(r"(?<![\w+#.])(" + "|".join(sorted(map(re.escape, SKILLS + list(ALIAS)), key=len, reverse=True)) + r")(?![\w+#])", re.I)
EMP = dict(fulltime="full_time", permanent="full_time", vollzeit="full_time", parttime="part_time", teilzeit="part_time",
           contract="contract", contractor="contract", temporary="contract", freelance="contract",
           intern="internship", internship="internship", praktikum="internship")
YEARS_RX = re.compile(r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b", re.I)
PAY_RX = re.compile(r"([$£€])\s?(\d{2,3}(?:,\d{3})+|\d{2,3}\s?[kK])\s*(?:-|–|—|to)\s*[$£€]?\s?(\d{2,3}(?:,\d{3})+|\d{2,3}\s?[kK])")
LEGAL_RX = re.compile(r"[,\s]+(?:inc|llc|ltd|limited|gmbh|ag|corp|corporation|plc|bv|pty|pvt|llp|lp|co)\.?$", re.I)

RE_LEVELS = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in LEVELS]
RE_CATEGORIES = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in CATEGORIES]

# Ghost job heuristic patterns
EVERGREEN_RX = re.compile(r"talent (community|network|pool|pipeline)|general application|future (opportunit|role)|evergreen|always hiring"
                          r"|expression of interest|join our (talent|network)", re.I)
HIDDEN_RX = re.compile(r"confidential|stealth|undisclosed|anonymous|our client|staffing|recruit(er|ing|ment)|talent (solutions|partners)", re.I)

# Geographic dictionaries
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia", "PR": "Puerto Rico", "GU": "Guam", "VI": "Virgin Islands"
}

CA_PROVINCES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "NT": "Northwest Territories",
    "NU": "Nunavut", "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "YT": "Yukon"
}

AU_STATES = {
    "NSW": "New South Wales", "VIC": "Victoria", "QLD": "Queensland", "WA": "Western Australia",
    "SA": "South Australia", "TAS": "Tasmania", "ACT": "Australian Capital Territory", "NT": "Northern Territory"
}

COUNTRY_MAP = {
    "united states": "United States", "usa": "United States", "u.s.a.": "United States", "u.s.": "United States", "us": "United States",
    "united kingdom": "United Kingdom", "uk": "United Kingdom", "u.k.": "United Kingdom", "great britain": "United Kingdom",
    "england": "United Kingdom", "scotland": "United Kingdom", "wales": "United Kingdom", "northern ireland": "United Kingdom",
    "canada": "Canada", "australia": "Australia", "germany": "Germany", "deutschland": "Germany",
    "india": "India", "france": "France", "netherlands": "Netherlands", "holland": "Netherlands",
    "spain": "Spain", "espana": "Spain", "españa": "Spain", "italy": "Italy", "italia": "Italy",
    "brazil": "Brazil", "brasil": "Brazil", "japan": "Japan", "singapore": "Singapore",
    "ireland": "Ireland", "switzerland": "Switzerland", "sweden": "Sweden", "poland": "Poland",
    "mexico": "Mexico", "israel": "Israel", "united arab emirates": "United Arab Emirates", "uae": "United Arab Emirates",
    "south africa": "South Africa", "new zealand": "New Zealand", "belgium": "Belgium", "austria": "Austria",
    "denmark": "Denmark", "norway": "Norway", "finland": "Finland", "portugal": "Portugal",
    "greece": "Greece", "czech republic": "Czech Republic", "czechia": "Czech Republic",
    "romania": "Romania", "hungary": "Hungary", "philippines": "Philippines", "indonesia": "Indonesia",
    "malaysia": "Malaysia", "thailand": "Thailand", "vietnam": "Vietnam", "south korea": "South Korea",
    "korea": "South Korea", "argentina": "Argentina", "chile": "Chile", "colombia": "Colombia",
    "peru": "Peru", "nigeria": "Nigeria", "kenya": "Kenya", "egypt": "Egypt", "saudi arabia": "Saudi Arabia",
    "turkey": "Turkey", "türkiye": "Turkey", "ukraine": "Ukraine", "pakistan": "Pakistan",
    "bangladesh": "Bangladesh", "taiwan": "Taiwan", "hong kong": "Hong Kong", "luxembourg": "Luxembourg"
}

MAJOR_CITIES = {
    "london": "United Kingdom", "manchester": "United Kingdom", "birmingham": "United Kingdom", "edinburgh": "United Kingdom",
    "paris": "France", "lyon": "France", "marseille": "France",
    "berlin": "Germany", "munich": "Germany", "frankfurt": "Germany", "hamburg": "Germany", "cologne": "Germany",
    "tokyo": "Japan", "osaka": "Japan", "kyoto": "Japan",
    "toronto": "Canada", "vancouver": "Canada", "montreal": "Canada", "ottawa": "Canada", "calgary": "Canada",
    "sydney": "Australia", "melbourne": "Australia", "brisbane": "Australia", "perth": "Australia", "adelaide": "Australia",
    "dublin": "Ireland", "cork": "Ireland",
    "amsterdam": "Netherlands", "rotterdam": "Netherlands", "the hague": "Netherlands",
    "bengaluru": "India", "bangalore": "India", "mumbai": "India", "delhi": "India", "new delhi": "India",
    "hyderabad": "India", "pune": "India", "chennai": "India", "gurgaon": "India", "gurugram": "India", "noida": "India",
    "madrid": "Spain", "barcelona": "Spain", "valencia": "Spain",
    "rome": "Italy", "milan": "Italy",
    "zurich": "Switzerland", "geneva": "Switzerland", "basel": "Switzerland",
    "stockholm": "Sweden", "gothenburg": "Sweden",
    "warsaw": "Poland", "krakow": "Poland", "wroclaw": "Poland",
    "tel aviv": "Israel", "jerusalem": "Israel", "herzliya": "Israel",
    "dubai": "United Arab Emirates", "abu dhabi": "United Arab Emirates",
    "são paulo": "Brazil", "sao paulo": "Brazil", "rio de janeiro": "Brazil",
    "mexico city": "Mexico", "guadalajara": "Mexico", "monterrey": "Mexico",
    "buenos aires": "Argentina", "santiago": "Chile", "bogota": "Colombia", "lima": "Peru",
    "seoul": "South Korea", "singapore": "Singapore", "hong kong": "Hong Kong"
}

RE_US_STATE_END = re.compile(r'(?:,\s*|\s+)([A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?(?:\s*;\s*|\s*$)', re.IGNORECASE)
RE_COUNTRY_SPLIT = re.compile(r'[,;/|\-]+')
RE_COUNTRY_MAP = [(re.compile(r'\b' + re.escape(k) + r'\b', re.IGNORECASE), v) for k, v in COUNTRY_MAP.items()]
RE_US_STATES = [(re.compile(r'\b' + re.escape(state_name.lower()) + r'\b', re.IGNORECASE), "United States") for code, state_name in US_STATES.items()]
RE_CA_PROVINCES = [(re.compile(r'\b' + re.escape(prov_name.lower()) + r'\b', re.IGNORECASE), "Canada") for code, prov_name in CA_PROVINCES.items()]
RE_MAJOR_CITIES = [(re.compile(r'\b' + re.escape(city) + r'\b', re.IGNORECASE), cntry) for city, cntry in MAJOR_CITIES.items()]

# URL templates for ATS direct apply links
URLS = dict(
    greenhouse="https://job-boards.greenhouse.io/{s}/jobs/{r}",
    lever="https://jobs.lever.co/{s}/{r}/apply",
    ashby="https://jobs.ashbyhq.com/{s}/{r}/application",
    smartrecruiters="https://jobs.smartrecruiters.com/{s}/{r}",
    workday="https://{s}{r}",
    microsoft="https://apply.careers.microsoft.com/careers/job/{r}",
    apple="https://jobs.apple.com/en-us/details/{r}",
    pinpoint="https://{s}.pinpointhq.com/en/postings/{r}",
    breezy="https://{s}.breezy.hr/p/{r}",
    bamboohr="https://{s}.bamboohr.com/careers/{r}",
    rippling="https://ats.rippling.com/{s}/jobs/{r}",
    join="https://join.com/companies/{s}/jobs/{r}",
    adp="https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={s}&jobId={r}",
    jazzhr="https://{s}.applytojob.com/apply/{r}",
    jobvite="https://jobs.jobvite.com/{s}/job/{r}",
    trakstar_hire="https://{s}.hire.trakstar.com/jobs/{r}",
    jobscore="https://careers.jobscore.com/jobs/{s}/{r}",
    zoho="https://{s}/jobs/Careers/{r}",
    icims="https://{s}/jobs/{r}/job",
    ultipro="https://recruiting2.ultipro.com/{s}/OpportunityDetail?opportunityId={r}",
    dayforce="https://jobs.dayforcehcm.com/en-US/{s}/jobs/{r}/apply",
    paylocity="https://recruiting.paylocity.com/recruiting/jobs/Details/{r}",
    paycom="https://www.paycomonline.net/v4/ats/web.php/portal/{s}/jobs/{r}",
    applicantpro="https://{s}.applicantpro.com/jobs/{r}",
    isolved="https://{s}.isolvedhire.com/jobs/{r}",
    betterteam="https://{s}/{r}",
    gohire="https://jobs.gohire.io/{s}/{r}",
    freshteam="https://{s}.freshteam.com/jobs/{r}",
    gem="https://jobs.gem.com/{s}/{r}",
    recooty="https://careerspage.io/{s}/{r}",
    csod="https://{s}.csod.com/ux/ats/careersite/4/home/requisition/{r}?c={s}",
    taleo="https://{s}.taleo.net/careersection/ex/jobdetail.ftl?job={r}",
    bullhorn="https://careers.bullhornstaffing.com/{s}/jobs/{r}",
    successfactors="https://career{s}.successfactors.com/job/{r}",
    avature="https://{s}.avature.net/careers/JobDetail/{r}",
    recruiterflow="https://recruiterflow.com/{s}/jobs/{r}",
    manatal="https://www.careers-page.com/{s}/job/{r}",
    loxo="https://app.loxo.co/job/{r}",
    careerplug="https://{s}.careerplug.com/jobs/{r}",
    comeet="https://www.comeet.com/jobs/{s}/{r}",
    crelate="https://jobs.crelate.com/portal/{s}/job/{r}",
    clearcompany="https://apply.clearcompany.com/jobs?jobId={r}",
    pageup="http://careers.pageuppeople.com/{s}/caw/en/job/{r}",
    catsone="https://{s}.catsone.com/careers/{r}",
    talentlyft="https://{s}.talentlyft.com/o/{r}",
    jobadder="https://clientapps.jobadder.com/{s}/{r}",
    jobsoid="https://{s}.jobsoid.com/apply/{r}",
    tribepad="https://{s}.tribepad.com/members/modules/job/detail.php?record={r}",
    applicantstack="https://{s}.applicantstack.com/x/detail/{r}",
    factorial="https://{s}.factorial.com/job_posting/{r}"
)

DEFAULTS = dict(company="", closed_at=None, location="", country="", team="", remote=False, years=None, skills=[], pay=None, employment="", posted=None, reposts=0)

# Capped / windowed feeds where missing postings cannot be assumed closed
CAPPED = {"workday", "eightfold", "himalayas", "jobicy", "jobtech", "usajobs", "oracle", "phenom", "apple", "themuse", "aidevboard"}

# Public feeds skipped during Kaggle export
SKIP = {"remotive", "remoteok", "himalayas", "jobicy", "weworkremotely", "getonbrd", "workingnomads", "cryptojobs", "themuse", "aidevboard", "larajobs", "fourdayweek", "nodesk", "remotepython"}

# Lane concurrency defaults (boards fetched in parallel per provider)
LANE_OF = {"microsoft": "eightfold"}
LANES = {
    "greenhouse": 24, "lever": 16, "ashby": 16, "smartrecruiters": 12, "workday": 32, "teamtailor": 4, "oracle": 8, "workable": 2,
    "personio": 12, "recruitee": 2, "phenom": 4, "apple": 1, "pinpoint": 8, "breezy": 8, "bamboohr": 16, "rippling": 8, "join": 16,
    "adp": 16, "jazzhr": 16, "jobvite": 8, "trakstar_hire": 8, "jobscore": 8, "zoho": 16, "icims": 8, "ultipro": 16, "dayforce": 16,
    "paylocity": 16, "paycom": 16, "applicantpro": 8, "isolved": 8, "betterteam": 8, "gohire": 8, "freshteam": 4, "gem": 4,
    "recooty": 4, "csod": 4, "taleo": 2, "bullhorn": 8, "successfactors": 8, "avature": 8, "recruiterflow": 8, "manatal": 8,
    "loxo": 8, "careerplug": 8, "comeet": 8, "crelate": 8, "clearcompany": 8, "pageup": 8,
    "catsone": 8, "talentlyft": 8, "jobadder": 8, "jobsoid": 8, "tribepad": 8, "applicantstack": 8,
    "factorial": 4, "aidevboard": 2, "larajobs": 2, "fourdayweek": 2, "nodesk": 2, "remotepython": 2
}

# Crawl limits
PH_CAP = 10000
EF_CAP = 3000
ORA_CAP = 2000
HIM_CAP = 1000
MUSE_CAP = 1000
APPLE_CAP = 2000
JT_CAP = 1000
AIDEV_CAP = 500

PERSONIO_LEVEL = {
    "entry-level": "junior", "trainee": "junior", "working student": "intern",
    "internship": "intern", "experienced": "mid", "senior": "senior",
    "lead": "lead", "management": "manager+"
}
