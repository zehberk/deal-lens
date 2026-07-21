import re

from datetime import timedelta
from pathlib import Path

KBB_CAR_PRICES_URL = "https://www.kbb.com/car-prices/"
KBB_WHATS_MY_CAR_WORTH_URL = "https://www.kbb.com/whats-my-car-worth/"
KBB_LOOKUP_BASE_URL = "https://kbb.com/{make}/{model}/{year}/"
KBB_LOOKUP_TRIM_URL = KBB_LOOKUP_BASE_URL + "{trim}/"


KBB_VARIANT_CACHE = Path("cache") / "kbb.cache"
PRICING_CACHE = Path("cache") / "pricing.cache"
ANALYSIS_CACHE = Path("cache") / "analysis.cache"
KBB_CACHE_TTL = timedelta(days=7)

BAD_STRINGS = {"", "unavailable", "n/a", "none", "null", "-", "not specified"}
DRIVETRAINS = {"4x4", "4wd", "2wd", "4xe", "awd", "rwd"}
ENGINE_DISPLACEMENT_RE = re.compile(
    r"\b"  # start at a word boundary
    r"(\d+(?:\.\d+)?)"  # displacement number: integer or decimal (e.g. 2 or 2.5)
    r"(?:L|cc)"  # unit: liters (L) or cubic centimeters (cc)
    r"\b",  # end at a word boundary
    re.IGNORECASE,  # allow l/L/cc/CC
)
BED_LENGTH_RE = re.compile(
    r"\b([3-9])"  # feet, restricted to 3–9
    r"(?:[\.\-]?\d\/\d)?"  # optional fraction (e.g. -1/2, .1/4)
    r"(?:\.\d+)?"  # or optional decimal (e.g. 6.5)
    r"\s?(?:ft|['’])"  # ft, ' or ’
    r"(?:\s?(\d{1,2})\")?",  # optional inches (e.g. 7")
    re.IGNORECASE,
)
BODY_STYLE_RE = re.compile(
    r"\b("  # leading space
    r"Sedan|Coupe|Hatchback|"
    r"Sport Utility|Sport Utility Vehicle|SUV|"
    r"Crew Cab|Extended Cab|SuperCrew Cab|"
    r"Van|Wagon|Pickup"  # body style group
    r")\s+(\dD)\b",  # door count, e.g. 2D, 4D
    re.IGNORECASE,
)
BODY_STYLE_ALIASES = {"SUV 4D": "Sport Utility Vehicle 4D"}


DEAL_ORDER = ["Great", "Good", "Fair", "Poor", "Bad"]
COND_ORDER = ["New", "Certified", "Used"]

# Level 3 regex for dealer fees
FEE_KEYWORDS = [
    r"dealer fee",
    r"dealer fees",
    r"doc fee",
    r"doc fees",
    r"document fee",
    r"document fees",
    r"documentation fee",
    r"documentation fees",
    r"dealer doc fee",
    r"dealer document fee",
    r"dealer documentation fee",
    r"dealer transfer services fees",
    r"dealer and handling fees",
    r"dealer and handling fee",
]
FEE_PATTERN = re.compile("|".join(rf"{kw}" for kw in FEE_KEYWORDS), re.I)


NO_FEE_RE = re.compile(
    r"\bno\s+(?:dealer|doc(?:ument(?:ation)?)?)\s*fees?\b"
    r"|\bzero\s+dealer\s*fees?\b",
    re.I,
)

FEE_PHRASE_RE = re.compile(
    r"|".join(rf"\b{kw}\b" for kw in FEE_KEYWORDS),
    re.I,
)

AMOUNT_RE = re.compile(
    r"|".join(
        [
            # phrase first, amount after
            rf"{kw}[^$]{{0,50}}\$\s*([0-9][0-9,]*(?:\.\d{{2}})?)"
            for kw in FEE_KEYWORDS
        ]
        + [
            # amount first, phrase after
            rf"\$\s*([0-9][0-9,]*(?:\.\d{{2}})?)\s*.{{0,50}}{kw}"
            for kw in FEE_KEYWORDS
        ]
    ),
    re.I,
)

EXCLUDE_KEYWORDS = [
    " exclude ",
    " excludes ",
    " excluded ",
    " do not include ",
    " does not include ",
    " not included ",
]  # use spaces so we don't match partial words

INCLUDE_KEYWORDS = [" is included "]

# Sections breaks:
# - Sentences on periods
# - Semicolons for lists/raw html
# - Conjunctions
# - Clauses (commas, etc.)
SENTENCE_RE = re.compile(r"(?<!\d)\.(?!\d)|(?<=\d)\.(?=\s+[A-Z])")
SEMICOLON_RE = re.compile(r";+")
CONJUNCTION_RE = re.compile(r"\s+(?:and|&|\||/|\+)\s+")
CLAUSE_SPLIT_RE = re.compile(r"(?:,(?!\d)|\|+|!+)")

RATE_SLASH_RE = re.compile(
    r"\$\d+(?:\.\d+)?\s*\\?/\s*(?:<[^>]+>|[A-Za-z_]+)",
    re.I,
)  # Numbers that may follow a $X/[word] format
FEE_WORD_RE = re.compile(r"\bfee\b", re.I)  # Literal search for 'fee'
QUOTED_KV_RE = re.compile(
    r'"\s*[^"]+\s*"\s*:\s*"\s*[^"]*(?:"|$)'
)  # key: value pairs in quotes

DOLLAR_RE = re.compile(
    r"""
    \$
    (?:\d{1,3}(?:,\d{3})+|\d+)
    (?:\.\d+)?                     # decimals only if dot+digits
    (?=
        \s|$                        # space/end
        |,(?=\s|$)                  # comma punctuation only if then space/end
        |\.(?=\s|$|[)\]])           # period punctuation only if then space/end/close
    )
    """,
    re.X,
)

RATE_RE = re.compile(r"\b(?:per|each|every)\s+\$?\d", re.I)  # Rate keywords
PAYMENT_RE = re.compile(
    r"""
    \bper\s+(?:month|mo|week|wk|year|yr)\b |
    /\s*(?:month|mo|week|wk|year|yr)\b |
    \bAPR\b |
    \bmonths?\b |
    \bterm\b |
    \bloan\b |
    \bloan\s+amount\b |
    \bpayment(?:s)?\b |
    \bturn[-\s]?in\s+fee\b
    """,
    re.I | re.X,
)  # Keywords related to leases or other payment structures
DOWN_RE = re.compile(r"\bdown\s+payment\b|\bdown\b", re.I)  # Down payment keywords
CREDIT_RE = re.compile(
    r"\b(?:"
    r"down\s+payment|partial\s+payment|deposit|discount|rebate|credit|equity|"
    r"bonus\s+cash|bonus|trade[-\s]?in"
    r")\b",
    re.I,
)  # Credits for price discount
WARRANTY_RE = re.compile(
    r"\b(?:warranty|extended\s+warranty|service\s+contract|protection\s+plan|benefits|coverage)\b",
    re.I,
)  # Warranty keywords
UPSELL_RE = re.compile(
    r"\b(?:"
    r"equipped with|pre-equipped|included for|adds?|feature(?:s|d)?|"
    r"comes with|helps maintain|protection|coating|app|bedliner|"
    r"window tint|ceramic|gps|theft|lojack|spray[-\s]?in|lifetime|"
    r"professionally|treated|paint|complimentary|carfax|vin\s*etch(?:ing)?|"
    r"package|shield|digital\s+plate|brake\s+plus"
    r")\b",
    re.I,
)  # Keywords related to upsells
FIRST_PERSON_RE = re.compile(
    r"\b(?:i|me|my)\b", re.I
)  # First-person reviews or testimonials


FUNCTION_WORDS_RE = re.compile(
    r"\b(?:"
    r"a|an|the|"
    r"and|or|"
    r"of|to|for|with|by|on|"
    r"is|are|was|were|be|been|being|"
    r"all|any"
    r")\b",
    re.I,
)
PRICE_SCOPE_RE = re.compile(
    r"\b(?:price|prices|priced|advertised|listed|shown|final|total|standard|only)\b",
    re.I,
)
APPLICABILITY_RE = re.compile(
    r"\b(?:include(?:s|d)?|exclude(?:s|d)?|appl(?:y|ies|ied)|subject)\b|\bnot\s+included\b",
    re.I,
)
COLLECTION_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"must\s+be\s+paid|"
    r"due\s+at\s+signing|"
    r"at\s+time\s+of\s+sale|"
    r"time\s+of\s+sale|"
    r"purchaser|consumer|buyer"
    r")\b",
    re.I,
)
VEHICLE_CONTEXT_RE = re.compile(
    r"\b(?:vehicle|vehicles|used|pre[-\s]?owned|purchase|purchases|sale|sales|lease)\b",
    re.I,
)
MARKUP_RE = re.compile(
    r"(?:"
    r"\{\s*\\?\"?\w+\\?\"?\s*:|"  # JSON key prefixes
    r"\\?\"[a-zA-Z0-9_-]+\\?\"|"  # quoted single-word junk
    r"<[^>]+>|"  # HTML tags
    r"\$\(|\)|"  # JS wrappers
    r"\*\*|"  # markdown
    r"&amp;|&nbsp;|"  # HTML entities
    r"\\\"|\"|\'|"  # remaining quotes
    r"[\{\}\[\]]|"  # JSON braces
    r"\\/"  # escaped slash
    r")",
    re.I,
)

WHITESPACE_RE = re.compile(r"\s+")
NEGATION_REMNANTS_RE = re.compile(
    r"\b(?:"
    r"do\s+not|does\s+not|not|may\s+not|"
    r"must\s+paid|which\s+paid|paid|"
    r"in\s+must\s+paid"
    r")\b",
    re.I,
)
DANGLING_FILLERS_RE = re.compile(
    r"\b(?:"
    r"in|extra|additional|"
    r"at\s+time|collected|collected\s+at\s+time"
    r")\b",
    re.I,
)
DISCLAIMERS_MARKETING_RE = re.compile(
    r"\b(?:"
    r"which\s+charges?\s+represent\s+costs?|"
    r"represent\s+costs?|"
    r"fees?\s+you\s+pay|"
    r"our|we\s+have|you\s+pay|"
    r"very\s+simple\s+to\s+deal\s+with|"
    r"no\s+big\s+dealer\s+bs|"
    r"read\s+our\s+reviews"
    r")\b",
    re.I,
)
NON_LETTER_RE = re.compile(r"[^a-z\-]+")
LEADING_NON_LETTER_RE = re.compile(r"^[^a-zA-Z]+")
TRAILING_NON_LETTER_RE = re.compile(r"[^a-zA-Z]+$")

FEE_WORDS = {"fee", "fees", "charge", "charges", "expense"}
FEE_SIGNAL_RE = re.compile(
    r"\b(?:fee|fees|charge|charges|expense)\b",
    re.I,
)

DOLLAR_AMOUNT_RE = re.compile(
    r"""
    ^\$
    (?:
        (?:\d{1,3}(?:,\d{3})+)   # 1,234 or 12,345,678
        |
        \d+                     # 30012
    )
    $
    """,
    re.VERBOSE,
)
