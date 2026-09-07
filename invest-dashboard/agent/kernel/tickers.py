"""Master ticker map for the portfolio.

Each entry:
  key           — canonical short label used internally
  yf            — Yahoo Finance symbol (None if MOEX — use moex_id instead)
  moex_id       — MOEX SECID for ISS API (only for RU stocks)
  currency      — quote currency (USD/HKD/GBp/NOK/RUB)
  name          — human-readable name
  group         — investment group / thesis
  research_type — drives sector-specific Perplexity questions:
                  default | ai_infra | oil_gas | litigation_finance |
                  gse | mining | healthcare | fintech | consumer |
                  telecom_media | chemicals | reit | russia
  has_stakes    — optional, True if the company holds separately-valuable
                  stakes/spin-offs a plain operating DCF wouldn't capture
                  (drives method_router.py → 'sotp'). Absent/False by default.
"""

# Sector-specific extra questions added to the Perplexity research prompt
RESEARCH_TYPE_EXTRA = {
    "ai_infra": """
For AI infrastructure companies additionally provide:
- GPU cluster capacity added (H100/H200/B200 count, MW, locations)
- Data center openings, expansions, lease signings
- Major enterprise/hyperscaler customer contracts (name, $ value, duration)
- Capacity utilization and waitlist status
- Nvidia/AMD allocation and delivery schedule""",

    "oil_gas": """
For oil & gas / offshore drilling companies additionally provide:
- Rig contract awards, extensions, cancellations (day-rate, duration, operator)
- Fleet utilization and backlog in rig-years and $
- Oil/gas price sensitivity and hedging levels
- OPEC decisions and their impact on deepwater demand
- Well results (production rates, reserve adds)""",

    "litigation_finance": """
For litigation finance companies additionally provide:
- Court rulings, judgments, appeals, reversals — exact court, date, $ amount
- Case wins/losses and their carrying value impact
- Enforcement actions and collection progress
- New case investments and portfolio composition changes
- Regulatory changes to arbitration/enforcement framework""",

    "gse": """
For government-sponsored enterprises (Fannie/Freddie) additionally provide:
- Congressional bills, votes, committee actions on GSE reform/privatization
- FHFA director statements and capital rule changes
- Treasury Department / White House policy signals
- Court decisions on GSE-related lawsuits
- Conservatorship exit timeline updates""",

    "mining": """
For mining companies additionally provide:
- Quarterly production (oz, tonnes, grades) vs guidance
- All-in sustaining cost (AISC) per oz/tonne vs prior period
- Mine permit approvals, suspensions, environmental rulings
- Reserve/resource estimate updates
- Commodity price hedging levels""",

    "healthcare": """
For managed care / healthcare companies additionally provide:
- CMS rate announcements (Medicare Advantage, Medicaid)
- Medical loss ratio (MLR) trends
- Membership enrollment changes
- DOJ / state AG investigations or settlements
- Drug pricing legislation impact""",

    "fintech": """
For fintech / brokerage companies additionally provide:
- GMV, TPV, active users — quarter vs quarter
- Regulatory licenses granted or revoked
- New market launches and geographic expansion
- Central bank policy changes affecting core product
- Competitive moves from incumbents (banks, Visa/MC)""",

    "consumer": """
For consumer / retail companies additionally provide:
- Same-store sales (SSS) and comparable transaction growth
- Inventory levels and markdown risk
- Store opening/closure plans
- Brand health metrics and pricing power signals
- Tariff / supply chain cost impacts""",

    "telecom_media": """
For telecom / media / streaming companies additionally provide:
- Subscriber additions/losses and ARPU trend
- Churn rate and retention spend
- Content spending commitments and ROI signals
- Spectrum / infrastructure investment plans
- Competitive pricing moves""",

    "chemicals": """
For specialty chemicals companies additionally provide:
- Channel inventory levels (distributors, farmers)
- Crop protection pricing vs prior season
- Active ingredient cost trends
- Generic competition entry or patent events
- Agricultural season outlook (acreage, weather)""",

    "reit": """
For REITs additionally provide:
- Occupancy rate and lease renewal spreads
- New development pipeline and cap rates
- Same-store NOI growth
- Balance sheet leverage and debt maturity profile
- Cap rate environment and transaction comps""",

    "russia": """
For Russian-listed companies additionally provide:
- CBR key rate decisions and macroeconomic backdrop
- Sanctions updates affecting the business or dividends
- Dividend decisions and payout ratios
- Government ownership changes or directives
- Currency (RUB) impact on financials""",

    "default": "",
}

GROUP_ORDER = [
    "AI & Semiconductors",
    "Нефть & Газ",
    "Финансы & Fintech",
    "Healthcare",
    "Металлы & Горнодобыча",
    "Россия",
    "Китай / HK",
    "Growth / Consumer",
    "Value & Special Situations",
]

TICKERS = [
    # --- AI & Semiconductors ---
    {"key": "NBIS",  "yf": "NBIS",     "currency": "USD", "name": "Nebius Group",             "group": "AI & Semiconductors",    "research_type": "ai_infra", "has_stakes": True},
    {"key": "CRDO",  "yf": "CRDO",     "currency": "USD", "name": "Credo Technology",          "group": "AI & Semiconductors",    "research_type": "ai_infra"},
    {"key": "MRVL",  "yf": "MRVL",     "currency": "USD", "name": "Marvell Technology",        "group": "AI & Semiconductors",    "research_type": "ai_infra"},
    {"key": "MU",    "yf": "MU",       "currency": "USD", "name": "Micron Technology",         "group": "AI & Semiconductors",    "research_type": "ai_infra"},

    # --- Нефть & Газ ---
    {"key": "RIG",   "yf": "RIG",      "currency": "USD", "name": "Transocean",                "group": "Нефть & Газ",            "research_type": "oil_gas"},
    {"key": "SDRL",  "yf": "SDRL",     "currency": "USD", "name": "Seadrill",                  "group": "Нефть & Газ",            "research_type": "oil_gas"},
    {"key": "VAL",   "yf": "VAL",      "currency": "USD", "name": "Valaris",                   "group": "Нефть & Газ",            "research_type": "oil_gas"},
    {"key": "KOS",   "yf": "KOS",      "currency": "USD", "name": "Kosmos Energy",             "group": "Нефть & Газ",            "research_type": "oil_gas"},
    {"key": "PTAL",  "yf": "PTAL.L",   "currency": "GBp", "name": "PetroTal",                  "group": "Нефть & Газ",            "research_type": "oil_gas"},

    # --- Финансы & Fintech ---
    {"key": "KSPI",  "yf": "KSPI",     "currency": "USD", "name": "Kaspi.kz (Nasdaq)",         "group": "Финансы & Fintech",      "research_type": "fintech"},
    {"key": "TIGR",  "yf": "TIGR",     "currency": "USD", "name": "UP Fintech (Tiger Brokers)", "group": "Финансы & Fintech",     "research_type": "fintech"},
    {"key": "BUR",   "yf": "BUR",      "currency": "USD", "name": "Burford Capital",           "group": "Финансы & Fintech",      "research_type": "litigation_finance"},
    {"key": "FNMA",  "yf": "FNMA",     "currency": "USD", "name": "Fannie Mae",                "group": "Финансы & Fintech",      "research_type": "gse"},
    {"key": "FMCC",  "yf": "FMCC",     "currency": "USD", "name": "Freddie Mac",               "group": "Финансы & Fintech",      "research_type": "gse"},
    {"key": "MRX",   "yf": "MRX",      "currency": "USD", "name": "Marex Group",               "group": "Финансы & Fintech",      "research_type": "fintech"},
    {"key": "PYPL",  "yf": "PYPL",     "currency": "USD", "name": "PayPal Holdings",           "group": "Финансы & Fintech",      "research_type": "fintech"},
    {"key": "FISV",  "yf": "FISV",     "currency": "USD", "name": "Fiserv",                    "group": "Финансы & Fintech",      "research_type": "fintech"},

    # --- Healthcare ---
    {"key": "UNH",   "yf": "UNH",      "currency": "USD", "name": "UnitedHealth",              "group": "Healthcare",             "research_type": "healthcare"},
    {"key": "MOH",   "yf": "MOH",      "currency": "USD", "name": "Molina Healthcare",         "group": "Healthcare",             "research_type": "healthcare"},
    {"key": "ZTS",   "yf": "ZTS",      "currency": "USD", "name": "Zoetis",                    "group": "Healthcare",             "research_type": "healthcare"},
    {"key": "HCA",   "yf": "HCA",      "currency": "USD", "name": "HCA Healthcare",            "group": "Healthcare",             "research_type": "healthcare"},

    # --- Металлы & Горнодобыча ---
    {"key": "BTG",   "yf": "BTG",      "currency": "USD", "name": "B2Gold",                    "group": "Металлы & Горнодобыча",  "research_type": "mining"},
    {"key": "ERO",   "yf": "ERO",      "currency": "USD", "name": "Ero Copper",                "group": "Металлы & Горнодобыча",  "research_type": "mining"},
    {"key": "SBSW",  "yf": "SBSW",     "currency": "USD", "name": "Sibanye Stillwater (ADR)",  "group": "Металлы & Горнодобыча",  "research_type": "mining"},
    {"key": "ALRS",  "yf": None, "moex_id": "ALRS", "currency": "RUB", "name": "АЛРОСА",       "group": "Металлы & Горнодобыча",  "research_type": "mining", "paused": True},

    # --- Россия (на паузе: исключены из авто-обновления и ревью, см. ACTIVE_KEYS) ---
    {"key": "SBER",  "yf": None, "moex_id": "SBER", "currency": "RUB", "name": "Сбербанк",     "group": "Россия",                 "research_type": "russia", "paused": True},
    {"key": "T",     "yf": None, "moex_id": "T",    "currency": "RUB", "name": "Т-Технологии", "group": "Россия",                 "research_type": "russia", "paused": True},
    {"key": "UPRO",  "yf": None, "moex_id": "UPRO", "currency": "RUB", "name": "Юнипро",       "group": "Россия",                 "research_type": "russia", "paused": True},

    # --- Китай / HK ---
    {"key": "1810.HK", "yf": "1810.HK", "currency": "HKD", "name": "Xiaomi",                  "group": "Китай / HK",              "research_type": "consumer"},
    {"key": "0001.HK", "yf": "0001.HK", "currency": "HKD", "name": "CK Hutchison",            "group": "Китай / HK",              "research_type": "default"},
    {"key": "2318.HK", "yf": "2318.HK", "currency": "HKD", "name": "Ping An Insurance",       "group": "Китай / HK",              "research_type": "fintech"},

    # --- Growth / Consumer ---
    {"key": "NFLX",  "yf": "NFLX",     "currency": "USD", "name": "Netflix",                  "group": "Growth / Consumer",       "research_type": "telecom_media"},
    {"key": "CRM",   "yf": "CRM",      "currency": "USD", "name": "Salesforce",               "group": "Growth / Consumer",       "research_type": "default"},
    {"key": "LULU",  "yf": "LULU",     "currency": "USD", "name": "Lululemon Athletica",      "group": "Growth / Consumer",       "research_type": "consumer"},
    {"key": "CHTR",  "yf": "CHTR",     "currency": "USD", "name": "Charter Communications",   "group": "Growth / Consumer",       "research_type": "telecom_media"},
    {"key": "LINE",  "yf": "LINE",     "currency": "USD", "name": "Lineage, Inc.",             "group": "Growth / Consumer",       "research_type": "reit"},
    {"key": "MELI",  "yf": "MELI",     "currency": "USD", "name": "MercadoLibre",             "group": "Growth / Consumer",       "research_type": "fintech"},
    {"key": "ADBE",  "yf": "ADBE",     "currency": "USD", "name": "Adobe Inc.",               "group": "Growth / Consumer",       "research_type": "default"},

    # --- Value & Special Situations ---
    {"key": "DELL",  "yf": "DELL",     "currency": "USD", "name": "Dell Technologies",        "group": "Value & Special Situations", "research_type": "ai_infra"},
    {"key": "GME",   "yf": "GME",      "currency": "USD", "name": "GameStop",                 "group": "Value & Special Situations", "research_type": "consumer"},
    {"key": "WHR",   "yf": "WHR",      "currency": "USD", "name": "Whirlpool",                "group": "Value & Special Situations", "research_type": "consumer"},
    {"key": "FMC",   "yf": "FMC",      "currency": "USD", "name": "FMC Corporation",          "group": "Value & Special Situations", "research_type": "chemicals"},
    {"key": "ATKR",  "yf": "ATKR",     "currency": "USD", "name": "Atkore",                   "group": "Value & Special Situations", "research_type": "default"},
    {"key": "AT.L",  "yf": "AT.L",     "currency": "GBp", "name": "Ashtead Technology",       "group": "Value & Special Situations", "research_type": "oil_gas"},
    {"key": "NOL.OL", "yf": "NOL.OL",  "currency": "NOK", "name": "NOL (Oslo)",               "group": "Value & Special Situations", "research_type": "default"},
    {"key": "FLY",   "yf": "FLY",      "currency": "USD", "name": "Firefly Aerospace",        "group": "Value & Special Situations", "research_type": "default"},
]

# Aliases from the source notation in "Фундаментал" → canonical key
ALIAS_TO_KEY = {
    "kaspi": "KSPI",
    "kspi": "KSPI",
    "1810.hk": "1810.HK",
    "nbis": "NBIS",
    "transocean": "RIG",
    "rig": "RIG",
    "tigr": "TIGR",
    "b2gold": "BTG",
    "btg": "BTG",
    "ptal": "PTAL",
    "molina healthcare": "MOH",
    "molina": "MOH",
    "moh": "MOH",
    "ero": "ERO",
    "fmc": "FMC",
    "fannie mae": "FNMA",
    "fnma": "FNMA",
    "fmcc": "FMCC",
    "fisv": "FISV",
    "fiserv": "FISV",
    "fi": "FISV",
    "dell": "DELL",
    "dell technologies": "DELL",
    "gamestop": "GME",
    "gme": "GME",
    "united health": "UNH",
    "unitedhealth": "UNH",
    "unh": "UNH",
    "kos": "KOS",
    "bur": "BUR",
    "ashtead technology holdings plc": "AT.L",
    "ashtead technology": "AT.L",
    "at.l": "AT.L",
    "sdrl": "SDRL",
    "val": "VAL",
    "nol": "NOL.OL",
    "nol.ol": "NOL.OL",
    "lululemon athletica": "LULU",
    "lululemon": "LULU",
    "lulu": "LULU",
    "crm": "CRM",
    "sbsw": "SBSW",
    "whr": "WHR",
    "crdo": "CRDO",
    "mrvl": "MRVL",
    "line": "LINE",
    "lineage": "LINE",
    "lineage, inc.": "LINE",
    "atkr": "ATKR",
    "atkore": "ATKR",
    "1:hk": "0001.HK",
    "0001.hk": "0001.HK",
    "ck hutchison": "0001.HK",
    "2318.hk": "2318.HK",
    "ping an": "2318.HK",
    "mrx": "MRX",
    "marex": "MRX",
    "marex group": "MRX",
    "chtr": "CHTR",
    "charter communications": "CHTR",
    "сбербанк": "SBER",
    "сбер": "SBER",
    "sber": "SBER",
    "тбанк": "T",
    "т-технологии": "T",
    "t": "T",
    "юнипро": "UPRO",
    "upro": "UPRO",
    "nflx": "NFLX",
    "netflix": "NFLX",
    "mu": "MU",
    "micron": "MU",
    "алроса": "ALRS",
    "alrs": "ALRS",
    "fannie": "FNMA",
    "freddie": "FMCC",
    "valaris": "VAL",
    "seadrill": "SDRL",
    "credo": "CRDO",
    "marvell": "MRVL",
    "whirlpool": "WHR",
    "xiaomi": "1810.HK",
    "petrotal": "PTAL",
    "meli": "MELI",
    "mercadolibre": "MELI",
    "mercado libre": "MELI",
    "zts": "ZTS",
    "zoetis": "ZTS",
    "hca": "HCA",
    "hca healthcare": "HCA",
    "pypl": "PYPL",
    "paypal": "PYPL",
    "adbe": "ADBE",
    "adobe": "ADBE",
    "burford": "BUR",
    "burford capital": "BUR",
    "kaspi.kz": "KSPI",
    "salesforce": "CRM",
    "kosmos": "KOS",
    "kosmos energy": "KOS",
    "fly": "FLY",
    "firefly": "FLY",
    "firefly aerospace": "FLY",
    "ero copper": "ERO",
    "sibanye": "SBSW",
    "sibanye stillwater": "SBSW",
}

TICKERS_BY_KEY = {t["key"]: t for t in TICKERS}

# Тикеры, активные для авто-обновления (--all) и месячного ревью.
# Сейчас на паузе русские MOEX-бумаги (SBER/T/UPRO/ALRS) — нерелевантны.
# Пауза снимается удалением "paused": True из записи; ручной recheck по тикеру
# (update_scenarios.py SBER) работает и для paused-бумаг.
ACTIVE_KEYS = [t["key"] for t in TICKERS if not t.get("paused")]

def resolve(label: str):
    """Return ticker dict by canonical key or alias; None if unknown."""
    if not label:
        return None
    k = label.strip().lower()
    if k in ALIAS_TO_KEY:
        return TICKERS_BY_KEY[ALIAS_TO_KEY[k]]
    if label in TICKERS_BY_KEY:
        return TICKERS_BY_KEY[label]
    return None
