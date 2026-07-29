"""Product-to-treatment matching.

Cases are taken from the live data that exposed the bug: every product reported
treatment_count 0 because the SQL LIKE join required the treatment's free text to
contain the whole catalog name, size suffix included.
"""

import pytest
from lawnops.matching import matches, shared_token_count, tokenize


class TestTokenize:
    def test_drops_packaging_noise(self):
        assert tokenize("Lesco 0-0-7 Pre-Emergent (50 lb)") == {"lesco", "0-0-7", "pre-emergent"}

    def test_keeps_fertilizer_analysis_intact(self):
        # Splitting "24-0-11" into digits would erase the most distinguishing
        # part of the name.
        assert "24-0-11" in tokenize("Lesco 24-0-11 No Phos (50 lb)")

    def test_empty(self):
        assert tokenize("") == set()


class TestMatches:
    @pytest.mark.parametrize(
        "catalog,treatment",
        [
            # Catalog name is longer: the treatment omitted the size.
            ("Ortho Bug B-gon Insect Killer (10 lb)", "Ortho Bug B-gon"),
            ("BioAdvanced Brush Killer Plus Concentrate", "BioAdvanced Brush Killer Plus"),
            ("Weed B-Gon + Crabgrass Control (32 oz)", "Weed B-Gon + Crabgrass Control"),
            # Exact.
            ("Cyzmic CS (8 oz)", "Cyzmic CS (8 oz)"),
            # Neither contains the other. These are the zero-stock products the
            # tool exists to flag, and the reason a reversed LIKE is not enough.
            ("Lesco 0-0-7 Pre-Emergent (50 lb)", "Lesco 0-0-7 Prodiamine"),
            ("Lebanon Prodiamine 0.58G (40 lb)", "Lebanon Prodiamine 0-0-7"),
        ],
    )
    def test_real_pairs_match(self, catalog, treatment):
        assert matches(catalog, treatment)

    @pytest.mark.parametrize(
        "catalog,treatment",
        [
            # Shares only the brand. Collapsing these would make every Lesco
            # product look used whenever any Lesco product was applied.
            ("Lesco 18-0-9 Weed & Feed (50 lb)", "Lesco 0-0-7 Prodiamine"),
            # Unrelated entries in the treatment log.
            ("Roundup Dual Action RTU", "Sand"),
            ("Roundup Dual Action RTU", "Pine Straw"),
            ("Lesco 0-0-7 Pre-Emergent (50 lb)", "Weed Scouting"),
        ],
    )
    def test_distinct_products_do_not_match(self, catalog, treatment):
        assert not matches(catalog, treatment)

    def test_brand_alone_is_one_token(self):
        assert shared_token_count("Lesco 18-0-9 Weed & Feed (50 lb)", "Lesco 0-0-7 Prodiamine") == 1

    @pytest.mark.parametrize("catalog,treatment", [("", "x"), ("x", ""), ("", "")])
    def test_empty_inputs(self, catalog, treatment):
        assert not matches(catalog, treatment)
