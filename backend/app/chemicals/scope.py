"""Which chemicals count toward the public herbicide totals.

Forestry use reports also carry rodent baits (pocket gopher and mountain
beaver control on new plantings). They are kept on file but are not
herbicides, so they never enter the herbicide tallies.
"""

from __future__ import annotations

import re

#: Active ingredients of the rodent baits used in California forestry.
RODENTICIDE_INGREDIENTS: frozenset[str] = frozenset({
    "brodifacoum", "bromadiolone", "bromethalin", "chlorophacinone", "cholecalciferol",
    "difenacoum", "difethialone", "diphacinone", "strychnine", "warfarin",
    "zinc phosphide", "aluminum phosphide", "magnesium phosphide",
})

_BAIT_PRODUCT = re.compile(r"\b(RODENT|GOPHER|MOLE|VOLE|RAT|MOUSE|SQUIRREL)\b.*\bBAIT|"
                           r"\bBAIT\b.*\b(RODENT|GOPHER|MOLE|VOLE|RAT|MOUSE|SQUIRREL)\b|"
                           r"\b(STRYCHNINE|ZINC PHOSPHIDE|GOPHER GETTER|PROZAP)\b", re.I)


def is_rodenticide(ingredient_names: list[str], product_name: str | None = None) -> bool:
    if any(name.strip().lower() in RODENTICIDE_INGREDIENTS for name in ingredient_names):
        return True
    return bool(product_name and _BAIT_PRODUCT.search(product_name))
