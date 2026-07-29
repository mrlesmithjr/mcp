"""Fuzzy matching between free-text treatment product names and the catalog.

`treatments.product` is free TEXT with no foreign key to `products`, so the same
item is written differently in each place:

    catalog:   "Lesco 0-0-7 Pre-Emergent (50 lb)"
    treatment: "Lesco 0-0-7 Prodiamine"

Substring matching cannot bridge that. Neither string contains the other, and
reversing the comparison only helps the cases where the catalog name is the
longer one ("Ortho Bug B-gon" inside "Ortho Bug B-gon Insect Killer (10 lb)").

So match on shared significant tokens instead. Two products that share a brand
alone ("Lesco") are not the same product; two that also share an analysis or a
distinguishing word ("lesco" + "0-0-7") are. Requiring at least two shared
significant tokens separates `Lesco 0-0-7 Pre-Emergent` from
`Lesco 18-0-9 Weed & Feed`, which is the distinction that matters here.

This is deliberately a heuristic. The durable fix is a `product_id` column on
`treatments`; see the issue trail. Until then this restores the signal without a
schema migration.
"""

from __future__ import annotations

import re

# Packaging, units, and filler. These carry no identity: nearly every catalog
# entry ends in a size, so counting them would match everything to everything.
STOPWORDS = frozenset(
    {
        "lb",
        "lbs",
        "oz",
        "gal",
        "gallon",
        "qt",
        "bag",
        "bags",
        "bottle",
        "jug",
        "box",
        "concentrate",
        "rtu",
        "and",
        "the",
        "for",
        "with",
        "plus",
        "killer",
        "control",
        "size",
        "count",
        "pack",
    }
)

MIN_SHARED_TOKENS = 2

_TOKEN = re.compile(r"[a-z0-9][a-z0-9.\-]*")


def tokenize(text: str) -> set[str]:
    """Significant lowercase tokens, with packaging noise dropped.

    Hyphens and dots are kept inside tokens so a fertilizer analysis stays one
    token: "0-0-7" and "24-0-11" are the most distinguishing part of these
    names, and splitting them into digits would destroy that.
    """
    if not text:
        return set()
    tokens = {t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS}
    # A bare number ("50", "32") is a size that survived the stopword pass
    # because its unit was a separate token. An analysis like "0-0-7" is kept.
    return {t for t in tokens if not t.isdigit()}


def shared_token_count(a: str, b: str) -> int:
    return len(tokenize(a) & tokenize(b))


def matches(catalog_name: str, treatment_product: str) -> bool:
    """True when a treatment's free-text product refers to a catalog product.

    Substring containment in either direction is accepted outright: an exact
    name, or a treatment that named the product without its size, is not a
    guess. Otherwise fall back to the shared-token threshold.
    """
    if not catalog_name or not treatment_product:
        return False

    a, b = catalog_name.lower().strip(), treatment_product.lower().strip()
    if a in b or b in a:
        return True

    return shared_token_count(catalog_name, treatment_product) >= MIN_SHARED_TOKENS
