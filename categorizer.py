"""
categorizer.py
Rule-based expense categorization. No AI / no internet: a rule is just
"if the text contains one of these keywords -> this category".

Keywords cover English, Tanglish and Tamil. Edit RULES to teach it new
shops - order matters, the first matching rule wins.
"""

import re

REVIEW_THRESHOLD = 1000  # amounts above this get flagged for the user to double-check

RULES: list[tuple[str, list[str]]] = [
    ("Bills", [
        "electricity", "eb bill", "tneb", "tangedco", "current bill", "water bill",
        "gas bill", "lpg", "recharge", "broadband", "wifi", "internet bill",
        "postpaid", "prepaid", "airtel", "jio", "vodafone", "bsnl", "dth",
        "insurance", "emi", "rent", "mobile bill", "phone bill", "property tax",
        "மின்சாரம்", "மின் கட்டணம்", "வாடகை", "ரீசார்ஜ்",
    ]),
    ("Transport", [
        "bus", "train", "metro", "auto", "taxi", "cab", "uber", "ola", "rapido",
        "petrol", "diesel", "fuel", "toll", "parking", "flight", "irctc",
        "redbus", "ticket", "travel",
        "பேருந்து", "ரயில்", "பெட்ரோல்", "டீசல்", "ஆட்டோ", "பயணம்",
    ]),
    ("Food", [
        "restaurant", "hotel", "cafe", "coffee", "tea", "juice", "biryani", "lunch",
        "dinner", "breakfast", "snacks", "swiggy", "zomato", "bakery", "pizza",
        "burger", "grocery", "groceries", "vegetables", "fruits", "milk", "meals",
        "tiffin", "dominos", "kfc", "mcdonald", "saravana", "bhavan",
        "உணவு", "சாப்பாடு", "ஹோட்டல்", "டீ", "காபி", "மளிகை", "காய்கறி", "பால்",
    ]),
    ("Entertainment", [
        "movie", "cinema", "theatre", "theater", "pvr", "inox", "netflix", "prime",
        "hotstar", "spotify", "youtube", "game", "gaming", "concert", "bookmyshow",
        "subscription",
        "சினிமா", "படம்", "பொழுதுபோக்கு",
    ]),
    ("Shopping", [
        "amazon", "flipkart", "myntra", "meesho", "shopping", "mall", "clothes",
        "dress", "shirt", "shoes", "footwear", "mobile", "laptop", "electronics",
        "jewel", "gift", "pothys", "lifestyle", "decathlon",
        "துணி", "ஆடை", "செருப்பு", "நகை", "பரிசு",
    ]),
]


def _contains(text: str, keyword: str) -> bool:
    if keyword.isascii():
        # whole-word match so "bus" doesn't fire on "business"
        return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text) is not None
    return keyword in text  # Tamil script has no reliable word boundaries


def suggest_category(text: str, amount: float | None = None) -> dict:
    """Returns {category, matched, review, reason}. `category` is "Others"
    when no rule matched; `review` is True when the user should double-check."""
    t = (text or "").lower()
    category, matched = "Others", None
    for cat, keywords in RULES:
        hit = next((k for k in keywords if _contains(t, k)), None)
        if hit:
            category, matched = cat, hit
            break

    reasons = []
    if matched is None:
        reasons.append("No rule matched this description.")
    if amount is not None and amount > REVIEW_THRESHOLD:
        reasons.append(f"Amount is above ₹{REVIEW_THRESHOLD:,} - please review.")
    return {
        "category": category,
        "matched": matched,
        "review": bool(reasons),
        "reason": " ".join(reasons),
    }
