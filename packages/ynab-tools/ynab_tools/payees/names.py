"""Payee name normalization and variation matching (pure functions)."""

import re

SYSTEM_PAYEES = [
    "Starting Balance",
    "Manual Balance Adjustment",
    "Reconciliation Balance Adjustment",
]

PRESERVE_UPPER = {
    "AT&T",
    "ATT",
    "QT",
    "CVS",
    "ALDI",
    "BP",
    "UPS",
    "REI",
    "DSW",
    "KFC",
    "EMC",
}

# Known company name variations: canonical_lower → [import_patterns]
KNOWN_VARIATIONS = {
    "amazon": ["amzn", "amazon.com", "amz*", "amz "],
    "sam's club": ["samsclub", "sams club", "samsclub.com", "sam's club"],
    "sam's": ["samsclub", "sams", "samsclub.com"],
    "walmart": ["wal-mart", "wm ", "walmart.com", "wal mart"],
    "apple": ["apple.com", "apple.com/bill", "apple "],
    "apple.com": ["apple.com/bill"],
    "at&t": ["att ", "att*", "at&t"],
    "cvs": ["cvs/pharmacy", "cvs pharmacy", "cvs "],
    "target": ["target.com", "target "],
    "costco": ["costco.com", "costco whse", "costco "],
    "kroger": ["kroger fuel", "kroger "],
    "lowes": ["lowe's", "lowes.com", "lowes "],
    "lowe's": ["lowes", "lowe's", "lowes.com"],
    "home depot": ["homedepot.com", "home depot", "the home depot"],
    "the home depot": ["homedepot.com", "home depot", "nst the home d"],
    "georgia natural gas": ["spi*ga", "georgia natural"],
    "visible": ["paypal *visible", "visible "],
    "blossman": ["the blossman", "blossman "],
    "snapping shoals": ["ssemc", "snapping shoals"],
    "amicalola": ["mdc*amicalola", "amicalola"],
    "buffalo wild wings": ["tst*buffalo", "buffalo wild"],
    "zelle payment": ["zelle", "zel*"],
    "venmo": ["venmo"],
    "square": ["square", "sq *", "gosq.com"],
    "zelle": ["zelle®", "zelle ", "zel*"],
    "zel": ["zel*"],
    "chick-fil-a": ["chick-fil-a", "cfa ", "chick fil"],
    "starbucks": ["starbucks", "starbuck"],
    "mcdonald's": ["mcdonald's", "mcdonald ", "mcdonalds"],
    "publix": ["publix"],
    "aldi": ["aldi "],
    "walgreens": ["walgreens", "wag "],
    "shell": ["shell oil", "shell service", "shell "],
    "bp": ["bp#", "bp "],
    "exxon": ["exxonmobil", "exxon "],
    "chevron": ["chevron"],
    "racetrac": ["racetrac"],
    "qt": ["quiktrip", "qt "],
    "quiktrip": ["qt ", "quiktrip"],
    "spotify": ["spotify"],
    "netflix": ["netflix"],
    "hulu": ["hulu"],
    "disney+": ["disney plus", "disneyplus", "disney+"],
    "google": ["google ", "google*", "google.com"],
    "paypal": ["paypal "],
    "doordash": ["doordash", "dd "],
    "uber": ["uber ", "uber*"],
    "uber eats": ["ubereats", "uber eats", "uber* eats"],
    "lyft": ["lyft "],
    "grubhub": ["grubhub", "gh "],
    "instacart": ["instacart"],
    "bank of america": ["bkofamerica", "bank of america"],
    "atm withdrawal": ["bkofamerica atm", "withdrwl"],
    "atm deposit": ["bkofamerica atm", "bkofamerica mobile", "deposit"],
    "bank of america mobile deposit": ["bkofamerica mobile"],
    "zaxby's": ["zaxbys", "levelupzaxbys"],
    "arby's": ["arbys"],
    "wendy's": ["wendys"],
    "o'charley's": ["ocharleys", "o charley"],
    "chili's": ["chilis"],
    "applebee's": ["applebees"],
    "denny's": ["dennys"],
    "hardee's": ["hardees"],
    "carl's jr": ["carls jr"],
    "freddy's": ["freddys"],
    "culver's": ["culvers"],
    "five guys": ["5guys"],
    "miller's ale house": ["millers ale house"],
    "bad daddy's burger bar": ["baddaddysburgerbar", "bad daddy's burge"],
    "t.j.maxx": ["tjmaxx"],
    "t.j. maxx": ["tjmaxx"],
    "old navy": ["oldnavy.com", "oldnavy"],
    "dick's sporting goods": ["dickssportinggoods", "dick'ssportinggoo"],
    "dicks sporting goods": ["dick'ssportinggoo"],
    "j.crew factory": ["j crew factory"],
    "j.crew": ["j crew"],
    "kohl's": ["kohls", "www.kohls.com"],
    "h&m": ["hm -", "h&m"],
    "michael's": ["michaels stores"],
    "the north face": ["tnf "],
    "american eagle outfitters": ["ae outf", "a eagle outftr"],
    "att payment": ["att*bill payment"],
    "the local woodfired grill": ["tst* the local wo", "tst*the local wo"],
    "the district salon": ["the district salo"],
    "old milton mammoth": ["old milton mammot"],
    "johnny's new york style": ["johnnys new york style"],
    "shane's rib shack": ["shanes rib shack"],
    "u.s. post office": ["usps po"],
    "micro center": ["mctr-"],
}


def normalize_payee_name(name: str) -> str:
    """Normalize a payee name to canonical form."""
    if not name or name in SYSTEM_PAYEES or name.startswith("Transfer"):
        return name

    result = name
    result = re.sub(r"\s+[A-Z][a-z]+(\s+[A-Z][a-z]+)?\s+[A-Z]{2}\s*$", "", result)
    result = re.sub(r"\s*#\d+\s*", " ", result)
    result = re.sub(r"\s+\d{3,}\s*$", "", result)
    result = re.sub(r"\s+\d{3}-\d{3}-\d{4}\s*$", "", result)
    result = re.sub(r"\s+\d{3}-\d{4}\s*$", "", result)
    result = re.sub(r"\s+", " ", result).strip()

    words = result.split()
    title_words = []
    for word in words:
        if word.upper() in PRESERVE_UPPER:
            title_words.append(word.upper())
        elif word.upper() == word and len(word) > 1:
            title_words.append(word.title())
        else:
            title_words.append(word)

    return " ".join(title_words)


def find_duplicate_payees(payees: list[dict]) -> dict[str, list[dict]]:
    """Find payees that normalize to the same name. Returns groups with 2+ entries."""
    groups: dict[str, list[dict]] = {}
    for payee in payees:
        if payee["name"] in SYSTEM_PAYEES or payee["name"].startswith("Transfer"):
            continue
        canonical = normalize_payee_name(payee["name"])
        groups.setdefault(canonical, []).append(payee)
    return {k: v for k, v in groups.items() if len(v) > 1}


def is_legitimate_rename(payee_name: str, original_import: str) -> bool:
    """Check if a payee rename is legitimate (not a mismatch)."""
    if not payee_name or not original_import:
        return True

    payee_lower = payee_name.lower().strip()
    import_lower = original_import.lower().strip()

    if payee_lower.startswith("transfer"):
        return True

    transfer_patterns = [
        "to checking",
        "from checking",
        "to savings",
        "from savings",
        "to chk",
        "from chk",
        "online banking transfer",
        "online scheduled transfer",
        "payment - thank you",
        "payment-thank you",
        "payment thank you",
        "electronic payment",
        "autopay payment",
        "automatic payment",
        "ba electronic payment",
        "ext credit card",
        "external loan payment",
        "descriptive deposit",
        "descriptive withdrawal",
        "eft payment",
        "standard transfer",
        "ach hold",
        "sofi bank",
    ]
    if any(p in import_lower for p in transfer_patterns):
        return True

    payee_first = payee_lower.split()[0] if payee_lower else ""
    import_first = import_lower.split()[0] if import_lower else ""

    if payee_first == import_first:
        return True
    if payee_first.rstrip("'s#*") == import_first.rstrip("'s#*"):
        return True

    for canonical, import_variants in KNOWN_VARIATIONS.items():
        if payee_lower.startswith(canonical):
            if any(v in import_lower for v in import_variants):
                return True

    if len(payee_lower) >= 4 and payee_lower in import_lower:
        return True
    if len(payee_first) >= 4 and payee_first in import_lower:
        return True
    if len(import_first) >= 4 and import_first in payee_lower:
        return True

    return False


def filter_mismatches(transactions: list[dict]) -> list[dict]:
    """Filter out legitimate renames, keep only actual mismatches."""
    return [t for t in transactions if not is_legitimate_rename(t["payee_name"], t["original_import"])]
