"""What kind of thing is this, when the merchant will not say.

Swiggy returns no category. Neither `search_products` nor `get_cart` carries
one — checked on both reference pages — so a live cart arrives with every item
unclassified, and `_policy_reasons` turns a blank category into `category.unknown`
→ CLARIFY. Left alone, every real order would stop and ask.

So the category is resolved here, from the product name, and **it fails closed**:
a name this table cannot place stays blank and the engine asks. That direction
matters. A resolver that guessed would be a resolver that could quietly widen a
policy — one confident wrong answer and an off-scope item rides through a check
that exists to catch exactly that. Interrupting the user is the cheap failure;
silently authorising the wrong thing is the expensive one.

Kept out of the adapter on purpose: the adapter reports what Swiggy said and
nothing enriched, so there is never a question of whether a category came from
the merchant or from us.
"""

from __future__ import annotations

#: The category the Swiggy adapter stamps on bill lines that are a charge rather
#: than a good. Every policy allows it — see `with_fees` — because a delivery fee
#: is not a discretionary purchase the user authorises separately, it is the cost
#: of the delivery they already authorised.
#:
#: `categorise` never returns this. Only the adapter may mint such a line, or a
#: merchant could get an item classified into a category every policy accepts.
FEES = "fees"


def with_fees(categories) -> frozenset[str]:
    """The categories a policy allows, plus fees.

    In one place on purpose. Three call sites each remembering to union a
    constant is three places for it to be forgotten, and the one that forgets
    turns every real order into an escalation about a ₹35 delivery charge.
    """
    return frozenset(categories) | {FEES}


# Substrings, lower-cased, checked against the product name. Ordered most
# specific first — "coconut oil" is groceries, "engine oil" would not be, and
# the table should never have to guess which one "oil" meant.
_GROCERIES: tuple[str, ...] = (
    # staples
    "atta",
    "maida",
    "besan",
    "rava",
    "sooji",
    "rice",
    "poha",
    "dal",
    "dhal",
    "pulse",
    "rajma",
    "chana",
    "chickpea",
    "lentil",
    "quinoa",
    "oats",
    # dairy and eggs
    "milk",
    "curd",
    "dahi",
    "yoghurt",
    "yogurt",
    "paneer",
    "cheese",
    "butter",
    "ghee",
    "cream",
    "egg",
    # produce
    "banana",
    "apple",
    "onion",
    "potato",
    "tomato",
    "coriander",
    "spinach",
    "carrot",
    "lemon",
    "ginger",
    "garlic",
    "chilli",
    "chili",
    "capsicum",
    "cucumber",
    "beans",
    "peas",
    "cabbage",
    "cauliflower",
    "brinjal",
    "okra",
    "bhindi",
    "methi",
    "palak",
    "fruit",
    "vegetable",
    # bakery
    "bread",
    "bun",
    "pav",
    "rusk",
    "biscuit",
    "cookie",
    # cooking
    "sunflower oil",
    "mustard oil",
    "coconut oil",
    "olive oil",
    "groundnut oil",
    "cooking oil",
    "refined oil",
    "salt",
    "sugar",
    "jaggery",
    "turmeric",
    "haldi",
    "jeera",
    "cumin",
    "masala",
    "spice",
    "pepper",
    "vinegar",
    # drinks and pantry
    "coffee",
    "tea",
    "juice",
    "honey",
    "jam",
    "sauce",
    "ketchup",
    "noodle",
    "pasta",
    "flour",
    "cereal",
    "namkeen",
    "papad",
    "pickle",
    "achar",
)

_HOUSEHOLD: tuple[str, ...] = (
    "detergent",
    "dishwash",
    "toilet",
    "cleaner",
    "phenyl",
    "harpic",
    "lizol",
    "tissue",
    "napkin",
    "garbage bag",
    "trash bag",
    "mop",
    "broom",
    "scrub",
    "floor cleaner",
    "fabric",
)

_PERSONAL_CARE: tuple[str, ...] = (
    "shampoo",
    "conditioner",
    "soap",
    "bodywash",
    "body wash",
    "toothpaste",
    "toothbrush",
    "deodorant",
    "sanitiser",
    "sanitizer",
    "razor",
    "shaving",
    "moisturiser",
    "moisturizer",
    "lotion",
    "sunscreen",
    "handwash",
)

_TABLE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("groceries", _GROCERIES),
    ("household", _HOUSEHOLD),
    ("personal care", _PERSONAL_CARE),
)


def categorise(name: str) -> str:
    """A category for this product, or `""` when the table cannot place it.

    `""` is not a failure mode to smooth over — it is the answer that makes the
    engine ask instead of assume.
    """
    lowered = name.casefold()
    for category, needles in _TABLE:
        if any(needle in lowered for needle in needles):
            return category
    return ""
