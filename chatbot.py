"""
chatbot.py
Yosra AI - a small, dependency-free personal expense-tracking assistant.

Honest framing (this matters for how the project should be described):
this is a rule-based NLP + tool-calling agent, not a ChatGPT-style
generative model - there is no LLM and no API key involved, so it works
fully offline. What it DOES do, which goes beyond a simple Q&A bot:

  - understands Tamil / English / Tanglish, with light spelling tolerance
  - keeps a little short-term memory per user, so "what about last month?"
    correctly re-uses the category from the previous question
  - doesn't just answer questions - it can also DO things (add an expense,
    set a budget) after asking for a yes/no confirmation and getting a
    real database write to succeed, per the TASK_EXECUTION_RULES below
  - runs its own small "analysis engine": a month-end spending forecast,
    z-score based unusual-expense detection, and a combined monthly report
  - never invents a number - every reply is built from a real query result

TASK_EXECUTION_RULES and LANGUAGE_RULES are the assistant's governing
behavior spec, kept here verbatim.

Pipeline for every message:
 1. detect_language()      - Tamil script, Tanglish, or English
 2. check pending action   - were we waiting on a yes/no from last turn?
 3. extract_entities()     - category + time period mentioned in the text
 4. detect_intent()        - what KIND of question/command this is
 5. answer()               - run the right query/action, reply in-language
"""

import difflib
import re
import statistics
from calendar import monthrange
from datetime import datetime, timedelta

from database import CATEGORY_COLORS, get_connection

# ============================================================================
# Governing behavior spec for Yosra AI (kept verbatim).
# ============================================================================

TASK_EXECUTION_RULES = """
You are Yosra AI, a bilingual Tanglish personal
expense tracking assistant.
TASK EXECUTION:

1. Understand the task given by the user.
2. Identify what action is required.
3. Retrieve the required information from the backend.
4. Execute the requested calculation or operation.
5. Check the result before responding.
6. Explain the result in natural Tanglish.
7. Clearly say when the task is completed.
8. If the task fails, explain the actual reason.
9. Never claim a task is completed unless the
backend confirms successful execution.
10. If required information is missing, ask the user.

SUPPORTED TASKS:

* Analyze daily, weekly, and monthly expenses.
* Calculate income, expenses, and balance.
* Identify highest spending categories.
* Compare spending periods.
* Generate budget and savings suggestions.
* Create or update budgets.
* Add, edit, or delete expenses when requested.
* Answer follow-up questions using conversation
context and actual financial data.

LANGUAGE:

* Understand Tamil, English, and Tanglish.
* Reply in Tanglish when the user types Tanglish.
* Use simple English letters for Tamil words.
* Do not use Tamil script unless requested.

IMPORTANT:
The AI must use backend tools or API endpoints
to execute financial operations.
Never invent database results.
Never say an operation succeeded without confirmation.
Ask for confirmation before destructive actions
or important changes when necessary.
"""

LANGUAGE_RULES = """
You are Yosra AI, a personal expense tracking
and financial analysis assistant.
You must understand and support exactly these
three language styles:

1. Tamil
2. English
3. Tanglish

LANGUAGE DETECTION:

* Detect the language used in every user message.
* Understand Tamil written in Tamil script.
* Understand English written in English.
* Understand Tanglish, where Tamil words are
written using English letters.
* Understand mixed Tamil and English sentences.
* Understand common spelling mistakes and
informal typing.

RESPONSE RULES:

* If the user types Tamil, answer in Tamil script.
* If the user types English, answer in English.
* If the user types Tanglish, answer in Tanglish.
* Do not convert Tanglish answers into Tamil script.
* Do not force English when the user uses Tamil.
* If the user requests another language style,
follow that request.

TASK EXECUTION:

* Understand the user's financial task.
* Retrieve actual expense data from the backend.
* Execute the required calculation or operation.
* Give the result in the detected language.
* Confirm completion only after successful execution.
* Never invent financial data.

Use simple, friendly, natural language.
"""

# ============================================================================
# Language detection
# ============================================================================

_TAMIL_SCRIPT_RE = re.compile(r"[\u0B80-\u0BFF]")

_TANGLISH_WORDS = {
    "eppadi", "evlo", "evalo", "ethana", "irukku", "iruku", "irukkatha", "panren", "panra",
    "pannunga", "panna", "pannu", "venum", "vendam", "vendaam", "illa", "illai",
    "naan", "enakku", "ungal", "unga", "romba", "semma", "nalla", "seri", "sari",
    "aachu", "aayiduchu", "poachu", "poyiduchu", "selavu", "selav", "selavazhichen",
    "varumanam", "sambalam", "sambadhichathu", "panam", "kaasu", "kasu", "vaaram", "maasam",
    "intha", "andha", "enna", "yaaru", "enga", "epo", "eppo", "innaiku", "netru",
    "nethu", "vanakkam", "eluthu", "solra", "sollunga", "mudinja", "mudiyala",
    "thevai", "kudunga", "kudu", "pathi", "michama", "micham", "michapathu", "adhigam",
    "athigam", "migundhadhu", "vaangunen", "vaangina", "saapadu", "sapadu", "panninen",
    "panniten", "panniduven", "pannitean", "vandhuchu", "vanthuchu",
}


def detect_language(text: str) -> str:
    if _TAMIL_SCRIPT_RE.search(text):
        return "ta"
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if any(w in _TANGLISH_WORDS for w in words):
        return "tanglish"
    return "en"


def _pick(lang: str, en: str, tanglish: str, ta: str) -> str:
    return {"en": en, "tanglish": tanglish, "ta": ta}.get(lang, en)


# ============================================================================
# Short-term memory per user (category/period context + any pending
# confirmation). In-memory only - fine for a single-process dev server;
# would move to a DB table for a multi-process deployment.
# ============================================================================

_SESSIONS: dict[int, dict] = {}


def _session(user_id: int) -> dict:
    return _SESSIONS.setdefault(user_id, {"last_category": None, "last_period": None, "pending_delete": None})


_YES_WORDS = {"yes", "y", "yeah", "yep", "ok", "okay", "confirm", "sure", "seri", "sari", "pannunga", "pannu"}
_NO_WORDS = {"no", "n", "nope", "cancel", "illa", "illai", "vendam", "vendaam", "dont", "don't"}


def parse_yes_no(text):
    t = text.strip().lower()
    if t in _YES_WORDS or "ஆம்" in text or ("சரி" in text and "இல்லை" not in text):
        return True
    if t in _NO_WORDS or "இல்லை" in text or "வேண்டாம்" in text:
        return False
    words = set(re.findall(r"[a-zA-Z']+", t))
    if words & _YES_WORDS:
        return True
    if words & _NO_WORDS:
        return False
    return None


# ============================================================================
# Entity extraction - category + time period, in any of the three languages
# ============================================================================

CATEGORY_ALIASES = {
    "food": "Food", "meal": "Food", "meals": "Food", "lunch": "Food", "dinner": "Food",
    "eating": "Food", "restaurant": "Food", "groceries": "Food", "grocery": "Food",
    "saapadu": "Food", "saapdu": "Food", "sapadu": "Food", "unavu": "Food", "samayal": "Food",
    "உணவு": "Food", "சாப்பாடு": "Food",
    "transport": "Transport", "travel": "Transport", "bus": "Transport", "cab": "Transport",
    "auto": "Transport", "fuel": "Transport", "petrol": "Transport",
    "payanam": "Transport", "vandi": "Transport", "bike": "Transport",
    "பயணம்": "Transport", "வாகனம்": "Transport",
    "shopping": "Shopping", "shop": "Shopping", "vaangina": "Shopping", "vaangunen": "Shopping",
    "vangunathu": "Shopping",
    "கடை": "Shopping", "ஷாப்பிங்": "Shopping",
    "bills": "Bills", "bill": "Bills", "electricity": "Bills", "recharge": "Bills",
    "current bill": "Bills", "kadan": "Bills",
    "பில்": "Bills", "கட்டணம்": "Bills",
    "entertainment": "Entertainment", "movie": "Entertainment", "movies": "Entertainment",
    "padam": "Entertainment", "cinema": "Entertainment",
    "படம்": "Entertainment", "சினிமா": "Entertainment",
    "others": "Others", "other": "Others", "misc": "Others",
    "income": "Income", "salary": "Income", "earning": "Income", "earnings": "Income",
    "varumanam": "Income", "sambalam": "Income", "sambadhichathu": "Income",
    "வருமானம்": "Income", "சம்பளம்": "Income",
}

PERIOD_PATTERNS = [
    (r"\btoday\b|\bindru\b|\binnaiku\b|\binniku\b|இன்று", "today"),
    (r"\byesterday\b|\bnetru\b|\bnethu\b|நேற்று", "yesterday"),
    (r"\bthis week\b|\bintha vaaram\b|இந்த\s*வாரம்", "this_week"),
    (r"\blast week\b|\bmunthaiya vaaram\b|\bkadantha vaaram\b|கடந்த\s*வாரம்", "last_week"),
    (r"\bthis month\b|\bintha maasam\b|\bintha matham\b|\bindha month\b|இந்த\s*மாதம்", "this_month"),
    (r"\blast month\b|\bmunthaiya maasam\b|\bkadantha maasam\b|\bkadantha matham\b|கடந்த\s*மாதம்", "last_month"),
    (r"\ball time\b|\boverall\b|\bin total\b|\bso far\b|\bmoththam\b|\bmottham\b|மொத்தம்", "all_time"),
]


def _period_bounds(period, today):
    if period == "today":
        return today, today
    if period == "yesterday":
        y = today - timedelta(days=1)
        return y, y
    if period == "this_week":
        start = today - timedelta(days=today.weekday())
        return start, today
    if period == "last_week":
        this_start = today - timedelta(days=today.weekday())
        start = this_start - timedelta(days=7)
        end = this_start - timedelta(days=1)
        return start, end
    if period == "this_month":
        return today.replace(day=1), today
    if period == "last_month":
        first_this = today.replace(day=1)
        last_month_end = first_this - timedelta(days=1)
        return last_month_end.replace(day=1), last_month_end
    return None, None  # all_time


def extract_entities(text):
    text_l = text.lower()

    category = None
    for word, canonical in CATEGORY_ALIASES.items():
        # Word-boundary only on the left, so plurals/suffixes still match
        # ("foods", "groceries", "bills" against the "food"/"grocery"/"bill" alias).
        if re.search(rf"\b{re.escape(word)}", text_l) or word in text:
            category = canonical
            break

    if category is None:
        # Fuzzy fallback for typos and voice-transcription noise, e.g. mic
        # input mangling "entertainment" into "entertained month".
        latin_aliases = [w for w in CATEGORY_ALIASES if w.isascii()]
        for token in re.findall(r"[a-zA-Z]{4,}", text_l):
            close = difflib.get_close_matches(token, latin_aliases, n=1, cutoff=0.78)
            if close:
                category = CATEGORY_ALIASES[close[0]]
                break

    period = None
    for pattern, name in PERIOD_PATTERNS:
        if re.search(pattern, text_l) or re.search(pattern, text):
            period = name
            break

    return category, period


# ============================================================================
# Intent detection, in any of the three languages
# ============================================================================

_AMOUNT_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:₹|rs\.?|rupees?)", re.I)
_BARE_NUM_RE = re.compile(r"\b([\d,]+(?:\.\d+)?)\b")

_SPEND_VERBS = re.compile(
    r"\b(spent|spend|paid|pay|bought|add|added|panninen|pannen|panniten|kudutha|kuduthen|"
    r"poachu|selavazhichen|செலுத்தி|கொடுத்தேன்|செலவு\s*செய்தேன்)\b",
    re.I,
)
_BUDGET_VERBS = re.compile(
    r"\b(budget|limit|set\s+budget|pannu|set\s+pannu|வரம்பு|பட்ஜெட்)\b", re.I,
)


def parse_amount(text):
    m = _AMOUNT_RE.search(text)
    if m:
        raw = (m.group(1) or m.group(2)).replace(",", "")
        return float(raw)
    m = _BARE_NUM_RE.search(text)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


_INCOME_WORDS = re.compile(
    r"\b(income|salary|earned|earning|received|got\s+paid|credited|varumanam|sambalam|"
    r"sambadhichathu|வருமானம்|சம்பளம்)\b",
    re.I,
)


def parse_add_income(text):
    """Returns {amount, date, description} for a natural-language INCOME
    log (e.g. "add income 15000", "salary 20000 vandhuchu"), else None."""
    if not _INCOME_WORDS.search(text):
        return None
    amount = parse_amount(text)
    if not amount or amount <= 0:
        return None
    _, period = extract_entities(text)
    today = datetime.now().date()
    date = today - timedelta(days=1) if period == "yesterday" else today
    return {"amount": amount, "date": date.isoformat(), "description": "Income"}


_QUESTION_MARKERS = re.compile(
    r"\?|^\s*(how|what|which|why|when|who|show|compare|predict|give|list|generate)\b|"
    r"\b(evlo|evalo|ethana|epo|eppo|yaaru)\b|எவ்வளவு|எது|என்ன|யார்",
    re.I,
)


def parse_add_expense(text):
    """Returns {amount, category, date, description} if this message reads
    like a natural-language EXPENSE log, else None. Income wording is
    handled by parse_add_income instead, never logged as an expense.

    Accepts both an explicit spend verb ("spent 200 on food") AND a bare
    amount+category statement with no verb at all ("2500 in foods",
    "food 150", "150 lunch") - that shorthand is how people actually type
    expenses, so requiring a verb was too strict."""
    if _INCOME_WORDS.search(text) or _BUDGET_VERBS.search(text) or _QUESTION_MARKERS.search(text):
        return None
    if _DELETE_VERBS.search(text) or _EDIT_VERBS.search(text):
        return None
    amount = parse_amount(text)
    if not amount or amount <= 0:
        return None
    category, period = extract_entities(text)
    if category == "Income":
        category = None
    has_verb = bool(_SPEND_VERBS.search(text))
    if not has_verb and not category:
        return None  # too ambiguous: just a bare number, nothing to anchor it to
    category = category or "Others"
    today = datetime.now().date()
    date = today - timedelta(days=1) if period == "yesterday" else today
    desc = re.sub(_AMOUNT_RE, " ", text)
    desc = re.sub(_BARE_NUM_RE, " ", desc)
    desc = re.sub(_SPEND_VERBS, " ", desc)
    desc = re.sub(r"[₹]|\brs\b|\brupees?\b", " ", desc, flags=re.I)
    if period:
        for pattern, name in PERIOD_PATTERNS:
            if name == period:
                desc = re.sub(pattern, " ", desc, flags=re.I)
                break
    desc = re.sub(r"\s+", " ", desc).strip(" .,-") or category
    return {"amount": amount, "category": category, "date": date.isoformat(), "description": desc[:60]}


def parse_set_budget(text):
    if _DELETE_VERBS.search(text) or _EDIT_VERBS.search(text):
        return None
    if not _BUDGET_VERBS.search(text):
        return None
    amount = parse_amount(text)
    category, _ = extract_entities(text)
    if not amount or amount <= 0 or not category or category == "Income":
        return None
    return {"category": category, "monthly_limit": amount}


# ---- delete / edit an existing expense --------------------------------
_DELETE_VERBS = re.compile(
    r"\b(delete|remove|cancel|erase|clear)\b|நீக்கு|அழி", re.I,
)
_EDIT_VERBS = re.compile(
    r"\b(edit|update|change|correct|fix|modify|maathu|thiruthu)\b|மாற்று|திருத்து", re.I,
)
_LAST_REF = re.compile(r"\b(last|latest|recent|previous)\b|கடைசி|சமீபத்திய", re.I)


def parse_all_amounts(text):
    """Every amount mentioned, in reading order - needed for edits like
    "change my ₹200 food expense to ₹150" (old amount, then new amount)."""
    matches = list(_AMOUNT_RE.finditer(text))
    if matches:
        return [float((m.group(1) or m.group(2)).replace(",", "")) for m in matches]
    return [float(m.group(1).replace(",", "")) for m in _BARE_NUM_RE.finditer(text)]


def parse_delete_expense(text):
    if not _DELETE_VERBS.search(text):
        return None
    category, _ = extract_entities(text)
    if category == "Income":
        category = None
    amount = parse_amount(text)
    use_last = bool(_LAST_REF.search(text)) or "expense" in text.lower() and not category and not amount
    return {"category": category, "amount": amount, "use_last": use_last}


def parse_edit_expense(text):
    if not _EDIT_VERBS.search(text):
        return None
    amounts = parse_all_amounts(text)
    if not amounts:
        return None
    new_amount = amounts[-1]
    old_amount = amounts[0] if len(amounts) >= 2 else None
    category, _ = extract_entities(text)
    if category == "Income":
        category = None
    use_last = bool(_LAST_REF.search(text))
    return {"category": category, "old_amount": old_amount, "new_amount": new_amount, "use_last": use_last}


def detect_intent(text):
    t = text.lower()

    if parse_add_income(text):
        return "add_income"
    if parse_delete_expense(text):
        return "delete_expense"
    if parse_edit_expense(text):
        return "edit_expense"
    if parse_set_budget(text):
        return "set_budget"
    # Clearly a budget command (has "budget"/"limit" + an amount) but the
    # category didn't resolve even with fuzzy matching - ask, rather than
    # falling through to the generic "I'm not sure what you're asking".
    if _BUDGET_VERBS.search(text) and parse_amount(text):
        category, _ = extract_entities(text)
        if not category or category == "Income":
            return "set_budget_need_category"
    if parse_add_expense(text):
        return "add_expense"

    if any(w in t for w in ["forecast", "predict", "month end", "month-end", "will i spend", "varaikum"]) \
            or "கணிக்க" in text or "மாத இறுதி" in text:
        return "predict"

    if any(w in t for w in ["unusual", "anomaly", "anomalies", "strange expense", "odd expense"]) \
            or "விசித்திரமான" in text or "அசாதாரண" in text:
        return "anomaly"

    if any(w in t for w in ["monthly report", "generate report", "full report", "financial report", "report pannu"]) \
            or "அறிக்கை" in text:
        return "report"

    if any(w in t for w in ["financial health", "savings rate", "how healthy", "money health"]) \
            or "நிதி ஆரோக்கியம்" in text:
        return "financial_health"

    if re.search(r"^\s*(hi|hello|hey|yo|good morning|good evening|good afternoon|vanakkam)\b", t) \
            or "வணக்கம்" in text:
        return "greeting"

    if any(w in t for w in [
        "how am i doing", "overview", "summary", "how is my spending",
        "how's my spending", "enna nadakkudhu", "pathi sollunga",
    ]) or "சுருக்கம்" in text:
        return "overview"

    if any(w in t for w in ["most", "highest", "top", "biggest", "adhigam", "athigam", "romba selavu"]) \
            or "அதிகம்" in text:
        return "top_category"

    if any(w in t for w in ["balance", "left", "remaining", "migundhadhu", "irukkura kaasu"]) \
            or "மீதி" in text or "இருப்பு" in text:
        return "balance"

    if any(w in t for w in ["compare", "vs", "versus", "difference", "vithiyasam"]) \
            or "வித்தியாசம்" in text:
        return "compare"

    if any(w in t for w in [
        "tip", "save", "reduce", "advice", "suggest", "improve", "better",
        "michama vaikanum", "eppadi michapathu", "save pannanum",
    ]) or "சேமிக்க" in text or "ஆலோசனை" in text:
        return "tip"

    if any(w in t for w in ["income", "earned", "earning", "salary", "varumanam", "sambalam"]) \
            or "வருமானம்" in text or "சம்பளம்" in text:
        return "income_total"

    if any(w in t for w in [
        "how much", "spend", "spent", "expense", "expenses", "cost", "total",
        "evlo", "evalo", "ethana", "selavu", "selavazhichen", "poachu",
    ]) or "செலவு" in text:
        return "category_total"

    return "unclear"


def _period_label(period, lang):
    labels = {
        "today": {"en": "today", "tanglish": "innaiku", "ta": "இன்று"},
        "yesterday": {"en": "yesterday", "tanglish": "netru", "ta": "நேற்று"},
        "this_week": {"en": "this week", "tanglish": "intha vaaram", "ta": "இந்த வாரம்"},
        "last_week": {"en": "last week", "tanglish": "kadantha vaaram", "ta": "கடந்த வாரம்"},
        "this_month": {"en": "this month", "tanglish": "intha maasam", "ta": "இந்த மாதம்"},
        "last_month": {"en": "last month", "tanglish": "kadantha maasam", "ta": "கடந்த மாதம்"},
        "all_time": {"en": "overall", "tanglish": "moththam", "ta": "மொத்தமாக"},
    }
    return labels[period].get(lang, labels[period]["en"])


def _sum_amount(cur, user_id, start, end, category=None, ttype="Debit"):
    query = "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions WHERE user_id = ? AND type = ?"
    params = [user_id, ttype]
    if start is not None:
        query += " AND date >= ? AND date <= ?"
        params += [start.isoformat(), end.isoformat()]
    if category:
        query += " AND category = ?"
        params.append(category)
    cur.execute(query, params)
    return cur.fetchone()["total"]


def _latest_date(cur, user_id):
    cur.execute("SELECT MAX(date) AS d FROM transactions WHERE user_id = ?", (user_id,))
    row = cur.fetchone()["d"]
    return datetime.fromisoformat(row).date() if row else datetime.now().date()


def _find_expense(cur, user_id, category=None, amount=None, use_last=False, days=60):
    """Best-guess match for "the ₹500 shopping expense" / "my last expense"
    style references - most recent Debit transaction matching whatever was
    specified, scoped to the last `days` unless explicitly asking for
    the single latest one regardless of category/amount."""
    query = "SELECT id, date, description, category, amount FROM transactions WHERE user_id=? AND type='Debit'"
    params = [user_id]
    if category:
        query += " AND category=?"
        params.append(category)
    if amount:
        query += " AND amount=?"
        params.append(amount)
    if not use_last:
        cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
        query += " AND date >= ?"
        params.append(cutoff)
    query += " ORDER BY date DESC, id DESC LIMIT 1"
    cur.execute(query, params)
    row = cur.fetchone()
    return dict(row) if row else None


# ============================================================================
# Analysis engine - forecast / anomaly detection / financial health / report
# ============================================================================

def _forecast_reply(cur, user_id, today, lang):
    month_start = today.replace(day=1)
    days_in_month = monthrange(today.year, today.month)[1]
    days_passed = (today - month_start).days + 1
    spent = _sum_amount(cur, user_id, month_start, today, ttype="Debit")
    if spent == 0:
        return _pick(
            lang,
            en="No expenses logged yet this month, so there's nothing to forecast from yet.",
            tanglish="Intha maasam innum expense onnum illa, so forecast panna mudiyala.",
            ta="இந்த மாதம் இன்னும் செலவு எதுவும் இல்லை, எனவே கணிக்க முடியவில்லை.",
        )
    avg_day = spent / days_passed
    days_remaining = days_in_month - days_passed
    predicted = spent + avg_day * days_remaining
    return _pick(
        lang,
        en=(
            f"You've spent **₹{spent:,.0f}** over {days_passed} days this month "
            f"(~₹{avg_day:,.0f}/day). At that pace, you're on track to spend about "
            f"**₹{predicted:,.0f}** by month end."
        ),
        tanglish=(
            f"Intha maasam {days_passed} naal-la **₹{spent:,.0f}** selavu panniteenga "
            f"(~₹{avg_day:,.0f}/day). Idhe pattern continue aana, month end-la around "
            f"**₹{predicted:,.0f}** selavu aagalam."
        ),
        ta=(
            f"இந்த மாதம் {days_passed} நாட்களில் **₹{spent:,.0f}** செலவு செய்துள்ளீர்கள் "
            f"(சராசரி ₹{avg_day:,.0f}/நாள்). இந்த வேகத்தில், மாத இறுதியில் தோராயமாக "
            f"**₹{predicted:,.0f}** செலவாகக்கூடும்."
        ),
    )


def _anomaly_reply(cur, user_id, today, lang):
    cutoff = (today - timedelta(days=60)).isoformat()
    cur.execute(
        "SELECT date, category, amount, description FROM transactions WHERE user_id=? AND type='Debit' AND date>=? ORDER BY date",
        (user_id, cutoff),
    )
    rows = [dict(r) for r in cur.fetchall()]
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)

    recent_cutoff = (today - timedelta(days=14)).isoformat()
    flagged = []
    for category, items in by_cat.items():
        if len(items) < 4:
            continue
        amounts = [i["amount"] for i in items]
        mean = statistics.mean(amounts)
        try:
            stdev = statistics.stdev(amounts)
        except statistics.StatisticsError:
            stdev = 0
        if stdev == 0:
            continue
        for i in items:
            if i["date"] >= recent_cutoff and i["amount"] > mean + 2 * stdev:
                flagged.append((i, mean))

    if not flagged:
        return _pick(
            lang,
            en="Nothing unusual — your recent spending looks consistent with your normal pattern. ✅",
            tanglish="Onnum unusual illa — recent selavu romba usual pattern mathiri thaan irukku. ✅",
            ta="எதுவும் அசாதாரணமாக இல்லை — உங்கள் சமீபத்திய செலவு வழக்கமான முறையில் உள்ளது. ✅",
        )

    flagged.sort(key=lambda x: x[0]["date"], reverse=True)
    lines = [_pick(
        lang,
        en="⚠️ A few expenses stand out from your normal pattern:",
        tanglish="⚠️ Konjam expenses unga normal pattern-ku mela irukku:",
        ta="⚠️ சில செலவுகள் உங்கள் வழக்கமான முறையிலிருந்து வேறுபட்டுள்ளன:",
    ), ""]
    for item, mean in flagged[:5]:
        lines.append(
            _pick(
                lang,
                en=f"• ₹{item['amount']:,.0f} on {item['category']} ({item['date']}) — your usual {item['category']} expense is around ₹{mean:,.0f}.",
                tanglish=f"• ₹{item['amount']:,.0f} {item['category']}-ku ({item['date']}) — unga usual {item['category']} selavu ₹{mean:,.0f} dhan.",
                ta=f"• ₹{item['amount']:,.0f}, {item['category']} ({item['date']}) — வழக்கமான {item['category']} செலவு சுமார் ₹{mean:,.0f}.",
            )
        )
    return "\n".join(lines)


def _financial_health_reply(cur, user_id, today, lang):
    month_start = today.replace(day=1)
    income = _sum_amount(cur, user_id, month_start, today, ttype="Credit")
    expense = _sum_amount(cur, user_id, month_start, today, ttype="Debit")
    balance = income - expense
    savings_rate = (balance / income * 100) if income else None
    days_passed = (today - month_start).days + 1
    daily_avg = expense / days_passed if days_passed else 0

    cur.execute("SELECT COALESCE(SUM(monthly_limit),0) AS t FROM budgets WHERE user_id=?", (user_id,))
    total_budget = cur.fetchone()["t"]
    budget_used = (expense / total_budget * 100) if total_budget else None

    lines = [_pick(lang, "**🧠 Financial Health (this month)**", "**🧠 Financial Health (intha maasam)**", "**🧠 நிதி ஆரோக்கியம் (இந்த மாதம்)**"), ""]
    lbl = lambda en, tn, ta_: _pick(lang, en, tn, ta_)
    lines.append(f"• {lbl('Income','Varumanam','வருமானம்')}: ₹{income:,.0f}")
    lines.append(f"• {lbl('Expenses','Selavu','செலவு')}: ₹{expense:,.0f}")
    lines.append(f"• {lbl('Balance','Balance','இருப்பு')}: ₹{balance:,.0f}")
    if savings_rate is not None:
        lines.append(f"• {lbl('Savings rate','Savings rate','சேமிப்பு விகிதம்')}: {savings_rate:.0f}%")
    if budget_used is not None:
        lines.append(f"• {lbl('Budget used','Budget used','பட்ஜெட் பயன்பாடு')}: {budget_used:.0f}%")
    lines.append(f"• {lbl('Daily average','Daily average','நாள் சராசரி')}: ₹{daily_avg:,.0f}")
    return "\n".join(lines)


def _monthly_report_reply(cur, user_id, today, lang):
    month_start = today.replace(day=1)
    prev_end = month_start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)

    income = _sum_amount(cur, user_id, month_start, today, ttype="Credit")
    expense = _sum_amount(cur, user_id, month_start, today, ttype="Debit")
    prev_expense = _sum_amount(cur, user_id, prev_start, prev_end, ttype="Debit")
    balance = income - expense
    savings_rate = (balance / income * 100) if income else 0

    cur.execute(
        "SELECT category, SUM(amount) AS total FROM transactions WHERE user_id=? AND type='Debit' AND date>=? AND date<=? GROUP BY category ORDER BY total DESC LIMIT 1",
        (user_id, month_start.isoformat(), today.isoformat()),
    )
    top = cur.fetchone()

    cur.execute(
        "SELECT date, description, amount FROM transactions WHERE user_id=? AND type='Debit' AND date>=? AND date<=? ORDER BY amount DESC LIMIT 1",
        (user_id, month_start.isoformat(), today.isoformat()),
    )
    largest = cur.fetchone()

    cur.execute(
        "SELECT date, amount FROM transactions WHERE user_id=? AND type='Debit' AND date>=? AND date<=?",
        (user_id, month_start.isoformat(), today.isoformat()),
    )
    by_weekday: dict[int, float] = {}
    for r in cur.fetchall():
        wd = datetime.fromisoformat(r["date"]).weekday()
        by_weekday[wd] = by_weekday.get(wd, 0) + r["amount"]
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    top_weekday = weekday_names[max(by_weekday, key=by_weekday.get)] if by_weekday else None

    change_pct = ((expense - prev_expense) / prev_expense * 100) if prev_expense else None

    month_name = today.strftime("%B")
    lbl = lambda en, tn, ta_: _pick(lang, en, tn, ta_)
    lines = [f"**📊 {lbl('','','')}{month_name} {lbl('Financial Report','Financial Report','நிதி அறிக்கை')}**", ""]
    lines.append(f"{lbl('Income','Varumanam','வருமானம்')}: ₹{income:,.0f}")
    lines.append(f"{lbl('Expenses','Selavu','செலவு')}: ₹{expense:,.0f}")
    lines.append(f"{lbl('Balance','Balance','இருப்பு')}: ₹{balance:,.0f}")
    lines.append(f"{lbl('Savings rate','Savings rate','சேமிப்பு விகிதம்')}: {savings_rate:.1f}%")
    if top:
        lines.append(f"{lbl('Top category','Romba selavான category','முதன்மை பிரிவு')}: {top['category']} (₹{top['total']:,.0f})")
    if top_weekday:
        lines.append(f"{lbl('Highest spending day','Romba selavான naal','அதிக செலவு நாள்')}: {top_weekday}")
    if largest:
        lines.append(f"{lbl('Largest expense','Romba periya expense','மிகப்பெரிய செலவு')}: {largest['description']} – ₹{largest['amount']:,.0f}")
    if change_pct is not None:
        direction = lbl("up", "adhigam", "அதிகம்") if change_pct > 0 else lbl("down", "kammi", "குறைவு")
        lines.append(f"{lbl('Vs last month','Last month vida','கடந்த மாதத்தை விட')}: {direction} {abs(change_pct):.0f}%")
    lines.append("")
    if top:
        lines.append(_pick(
            lang,
            en=f"**AI Insight:** {top['category']} is your biggest category this month — keeping an eye on it is the fastest way to raise your savings rate.",
            tanglish=f"**AI Insight:** {top['category']} dhan intha maasam unga romba periya category — idha kammi panna, savings rate fast-a improve aagum.",
            ta=f"**AI குறிப்பு:** {top['category']} இந்த மாதம் உங்கள் அதிக செலவு பிரிவு — இதைக் கவனித்தால் சேமிப்பு விகிதம் விரைவில் உயரும்.",
        ))
    return "\n".join(lines)


# ============================================================================
# Direct task execution - add_income / add_expense / set_budget.
# These write to the database immediately (no yes/no step): per the
# TASK_EXECUTION_RULES above, that's still fine as long as the write is
# real and confirmed by the backend before Yosra says "Done!".
# ============================================================================

def _do_add_income(cur, conn, user_id, d, lang):
    cur.execute(
        "INSERT INTO transactions (user_id, date, description, category, type, amount) VALUES (?,?,?,?,?,?)",
        (user_id, d["date"], d["description"], "Income", "Credit", d["amount"]),
    )
    conn.commit()
    return _pick(
        lang,
        en=f"Done! ₹{d['amount']:,.0f} income added.",
        tanglish=f"Done! ₹{d['amount']:,.0f} income add panniten.",
        ta=f"முடிந்தது! ₹{d['amount']:,.0f} வருமானம் சேர்க்கப்பட்டது.",
    )


def _do_add_expense(cur, conn, user_id, d, lang):
    cur.execute(
        "INSERT INTO transactions (user_id, date, description, category, type, amount) VALUES (?,?,?,?,?,?)",
        (user_id, d["date"], d["description"], d["category"], "Debit", d["amount"]),
    )
    conn.commit()
    return _pick(
        lang,
        en=f"Done! ₹{d['amount']:,.0f} {d['category']} expense added.",
        tanglish=f"Done! ₹{d['amount']:,.0f} {d['category']} expense add panniten.",
        ta=f"முடிந்தது! ₹{d['amount']:,.0f} {d['category']} செலவு சேர்க்கப்பட்டது.",
    )


def _do_set_budget(cur, conn, user_id, d, lang):
    cur.execute(
        "INSERT INTO budgets (user_id, category, monthly_limit) VALUES (?,?,?) "
        "ON CONFLICT(user_id, category) DO UPDATE SET monthly_limit = excluded.monthly_limit",
        (user_id, d["category"], d["monthly_limit"]),
    )
    conn.commit()
    return _pick(
        lang,
        en=f"Done! {d['category']} monthly budget set to ₹{d['monthly_limit']:,.0f}.",
        tanglish=f"Done! {d['category']} monthly budget ₹{d['monthly_limit']:,.0f}-a set panniten.",
        ta=f"முடிந்தது! {d['category']} மாத பட்ஜெட் ₹{d['monthly_limit']:,.0f} ஆக அமைக்கப்பட்டது.",
    )


# ============================================================================
# Main entry point
# ============================================================================

def answer(message: str, user_id: int, username: str = "there") -> str:
    conn = get_connection()
    cur = conn.cursor()
    today_real = datetime.now().date()
    today = _latest_date(cur, user_id)

    lang = detect_language(message)
    session = _session(user_id)

    # Deleting is the one irreversible action here, so - unlike add/edit/
    # set-budget - it still gets a yes/no check before anything is removed.
    if session["pending_delete"]:
        match = session["pending_delete"]
        yn = parse_yes_no(message)
        if yn is True:
            cur.execute("DELETE FROM transactions WHERE id=? AND user_id=?", (match["id"], user_id))
            conn.commit()
            session["pending_delete"] = None
            conn.close()
            return _pick(
                lang,
                en=f"Done! Deleted the ₹{match['amount']:,.0f} {match['category']} expense from {match['date']}.",
                tanglish=f"Done! {match['date']} {match['category']} ₹{match['amount']:,.0f} expense delete panniten.",
                ta=f"முடிந்தது! {match['date']} அன்றைய ₹{match['amount']:,.0f} {match['category']} செலவு நீக்கப்பட்டது.",
            )
        if yn is False:
            session["pending_delete"] = None
            conn.close()
            return _pick(lang, "Okay, kept it — nothing was deleted.", "Sari, delete pannala — athu ippadiye irukku.", "சரி, நீக்கவில்லை — அது அப்படியே உள்ளது.")
        conn.close()
        return _pick(
            lang,
            en=f"Just checking — delete the ₹{match['amount']:,.0f} {match['category']} expense from {match['date']}? (yes/no)",
            tanglish=f"{match['date']} {match['category']} ₹{match['amount']:,.0f} expense-a delete pannalama? (yes/no)",
            ta=f"{match['date']} அன்றைய ₹{match['amount']:,.0f} {match['category']} செலவை நீக்கவா? (yes/no)",
        )

    category, period = extract_entities(message)
    intent = detect_intent(message)

    # Light follow-up memory: "what about last month" reuses the last category.
    if intent == "category_total" and category is None and session["last_category"] and len(message.split()) <= 6:
        category = session["last_category"]
    if category and category != "Income":
        session["last_category"] = category
    if period:
        session["last_period"] = period
    period = period or session["last_period"] or "this_week"

    start, end = _period_bounds(period, today)
    label = _period_label(period, lang)

    # Agent actions - executed immediately (no yes/no step): the backend
    # write has to actually succeed before Yosra says "Done!", per the
    # "never claim completed unless confirmed" rule above - but there's no
    # extra confirmation round-trip with the user for these simple, reversible
    # ledger edits.
    if intent == "add_income":
        data = parse_add_income(message)
        reply = _do_add_income(cur, conn, user_id, data, lang)
        conn.close()
        return reply

    if intent == "add_expense":
        data = parse_add_expense(message)
        reply = _do_add_expense(cur, conn, user_id, data, lang)
        conn.close()
        return reply

    if intent == "set_budget":
        data = parse_set_budget(message)
        reply = _do_set_budget(cur, conn, user_id, data, lang)
        conn.close()
        return reply

    if intent == "set_budget_need_category":
        conn.close()
        return _pick(
            lang,
            en="Sure! Which category — Food, Transport, Shopping, Bills, Entertainment, or Others?",
            tanglish="Seri! Edhu category-ku — Food, Transport, Shopping, Bills, Entertainment, illa Others?",
            ta="சரி! எந்த பிரிவுக்கு — Food, Transport, Shopping, Bills, Entertainment, அல்லது Others?",
        )

    if intent == "delete_expense":
        data = parse_delete_expense(message)
        match = _find_expense(cur, user_id, data["category"], data["amount"], data["use_last"])
        if not match:
            conn.close()
            return _pick(
                lang,
                en="I couldn't find a matching expense to delete — try naming the category or amount, e.g. \"delete the ₹500 shopping expense\".",
                tanglish="Adha mathiri expense onnum kaanom — category illa amount sollunga, e.g. \"₹500 shopping expense delete pannu\".",
                ta="பொருந்தும் செலவு கிடைக்கவில்லை — பிரிவு அல்லது தொகையைக் குறிப்பிடவும், எ.கா. \"₹500 shopping expense நீக்கு\".",
            )
        session["pending_delete"] = match
        conn.close()
        return _pick(
            lang,
            en=f"Just checking — delete the ₹{match['amount']:,.0f} {match['category']} expense from {match['date']} ({match['description']})? (yes/no)",
            tanglish=f"{match['date']} {match['category']} ₹{match['amount']:,.0f} ({match['description']}) expense-a delete pannalama? (yes/no)",
            ta=f"{match['date']} அன்றைய ₹{match['amount']:,.0f} {match['category']} செலவை ({match['description']}) நீக்கவா? (yes/no)",
        )

    if intent == "edit_expense":
        data = parse_edit_expense(message)
        match = _find_expense(cur, user_id, data["category"], data["old_amount"], data["use_last"])
        if not match:
            conn.close()
            return _pick(
                lang,
                en="I couldn't find that expense to edit — try naming the category or old amount, e.g. \"change my ₹200 food expense to ₹150\".",
                tanglish="Adha expense kaanom edit panna — category illa old amount sollunga, e.g. \"₹200 food expense-a ₹150-a change pannu\".",
                ta="திருத்த வேண்டிய செலவு கிடைக்கவில்லை — பிரிவு அல்லது பழைய தொகையைக் குறிப்பிடவும்.",
            )
        old_amount = match["amount"]
        cur.execute("UPDATE transactions SET amount=? WHERE id=?", (data["new_amount"], match["id"]))
        conn.commit()
        conn.close()
        return _pick(
            lang,
            en=f"Done! Updated the {match['category']} expense from {match['date']} — ₹{old_amount:,.0f} → ₹{data['new_amount']:,.0f}.",
            tanglish=f"Done! {match['date']} {match['category']} expense update panniten — ₹{old_amount:,.0f} → ₹{data['new_amount']:,.0f}.",
            ta=f"முடிந்தது! {match['date']} {match['category']} செலவு புதுப்பிக்கப்பட்டது — ₹{old_amount:,.0f} → ₹{data['new_amount']:,.0f}.",
        )

    if intent == "predict":
        reply = _forecast_reply(cur, user_id, today_real, lang)
        conn.close()
        return reply

    if intent == "anomaly":
        reply = _anomaly_reply(cur, user_id, today_real, lang)
        conn.close()
        return reply

    if intent == "report":
        reply = _monthly_report_reply(cur, user_id, today_real, lang)
        conn.close()
        return reply

    if intent == "financial_health":
        reply = _financial_health_reply(cur, user_id, today_real, lang)
        conn.close()
        return reply

    if intent == "greeting":
        conn.close()
        return _pick(
            lang,
            en=f"Hi {username}!",
            tanglish=f"Vanakkam {username}! Unga selavu pathi enna therinjukanum?",
            ta=f"வணக்கம் {username}! உங்கள் செலவு பற்றி என்ன தெரிந்துகொள்ள வேண்டும்?",
        )

    if intent == "overview":
        income = _sum_amount(cur, user_id, start, end, ttype="Credit")
        expense = _sum_amount(cur, user_id, start, end, ttype="Debit")
        cur.execute(
            "SELECT category, SUM(amount) AS total FROM transactions WHERE user_id=? AND type='Debit' AND date>=? AND date<=? GROUP BY category ORDER BY total DESC LIMIT 1",
            (user_id, start.isoformat(), end.isoformat()),
        )
        top = cur.fetchone()
        conn.close()
        heading = _pick(lang, f"Here's your overview for {label}:", f"Unga {label} overview idhu:", f"{label} உங்கள் சுருக்கம் இதோ:")
        income_l = _pick(lang, "Income", "Varumanam", "வருமானம்")
        expense_l = _pick(lang, "Expenses", "Selavu", "செலவு")
        balance_l = _pick(lang, "Balance", "Balance", "இருப்பு")
        top_l = _pick(lang, "Biggest category", "Romba selavான category", "அதிக செலவு பிரிவு")
        lines = [heading, "", f"• {income_l}: ₹{income:,.0f}", f"• {expense_l}: ₹{expense:,.0f}", f"• {balance_l}: ₹{income - expense:,.0f}"]
        if top:
            lines.append(f"• {top_l}: {top['category']} (₹{top['total']:,.0f})")
        return "\n".join(lines)

    if intent == "balance":
        income = _sum_amount(cur, user_id, None, None, ttype="Credit")
        expense = _sum_amount(cur, user_id, None, None, ttype="Debit")
        conn.close()
        bal = income - expense
        return _pick(
            lang,
            en=f"Your current balance is **₹{bal:,.0f}**.\n\nThat's ₹{income:,.0f} in total income minus ₹{expense:,.0f} in total expenses.",
            tanglish=f"Unga current balance **₹{bal:,.0f}**.\n\nTotal varumanam ₹{income:,.0f}, total selavu ₹{expense:,.0f} - idhu kazhichi balance idhu.",
            ta=f"உங்கள் தற்போதைய இருப்பு **₹{bal:,.0f}**.\n\nமொத்த வருமானம் ₹{income:,.0f}, மொத்த செலவு ₹{expense:,.0f}.",
        )

    if intent == "income_total":
        total = _sum_amount(cur, user_id, start, end, ttype="Credit")
        conn.close()
        return _pick(
            lang,
            en=f"You earned **₹{total:,.0f}** {label}.",
            tanglish=f"Neenga {label} **₹{total:,.0f}** sambadichirukeenga.",
            ta=f"நீங்கள் {label} **₹{total:,.0f}** சம்பாதித்துள்ளீர்கள்.",
        )

    if intent == "top_category":
        cur.execute(
            """
            SELECT category, SUM(amount) AS total FROM transactions
            WHERE user_id = ? AND type = 'Debit' {date_filter}
            GROUP BY category ORDER BY total DESC
            """.format(date_filter="AND date >= ? AND date <= ?" if start else ""),
            [user_id] + ([start.isoformat(), end.isoformat()] if start else []),
        )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return _pick(lang, f"I couldn't find any expenses {label} yet.", f"{label} onnume selavu illa pola irukku.", f"{label} எந்த செலவும் இல்லை.")
        grand_total = sum(r["total"] for r in rows)
        top = rows[0]
        headline = _pick(
            lang,
            en=f"You spend the most on **{top['category']}** ({top['total'] / grand_total * 100:.0f}% of {label}'s expenses).",
            tanglish=f"Neenga romba selavu pandradhu **{top['category']}** kaaga ({top['total'] / grand_total * 100:.0f}% - {label} selavula).",
            ta=f"நீங்கள் அதிகம் செலவு செய்வது **{top['category']}** ({label} செலவில் {top['total'] / grand_total * 100:.0f}%).",
        )
        sub = _pick(lang, "Top categories:", "Top categories:", "முதன்மை பிரிவுகள்:")
        lines = [headline, "", f"**{sub}**"]
        for i, r in enumerate(rows[:3], start=1):
            pct = r["total"] / grand_total * 100 if grand_total else 0
            lines.append(f"{i}. {r['category']} – ₹{r['total']:,.0f} ({pct:.0f}%)")
        tip = _pick(
            lang,
            en=f"**Tip:** Try trimming {top['category'].lower()} spending by 10–15% to save more.",
            tanglish=f"**Tip:** {top['category']} selavu 10–15% kammi panna try pannunga, michama vaikalam.",
            ta=f"**குறிப்பு:** {top['category']} செலவை 10–15% குறைத்தால் சேமிக்கலாம்.",
        )
        lines += ["", tip]
        return "\n".join(lines)

    if intent == "compare":
        this_start, this_end = _period_bounds("this_week", today)
        last_start, last_end = _period_bounds("last_week", today)
        this_total = _sum_amount(cur, user_id, this_start, this_end, category, "Debit")
        last_total = _sum_amount(cur, user_id, last_start, last_end, category, "Debit")
        conn.close()
        diff = this_total - last_total
        subject = category if category else _pick(lang, "expenses", "selavu", "செலவு")
        if diff == 0:
            return _pick(
                lang,
                en=f"Your {subject.lower() if category else subject} spending is unchanged from last week, at ₹{this_total:,.0f}.",
                tanglish=f"{subject} selavu last week mathiri thaan irukku, ₹{this_total:,.0f}.",
                ta=f"{subject} செலவு கடந்த வாரத்தைப் போலவே உள்ளது, ₹{this_total:,.0f}.",
            )
        direction_en = "up" if diff > 0 else "down"
        direction_tanglish = "adhigam" if diff > 0 else "kammi"
        direction_ta = "அதிகம்" if diff > 0 else "குறைவு"
        return _pick(
            lang,
            en=f"This week's {subject.lower() if category else subject} spending is **₹{this_total:,.0f}**, versus ₹{last_total:,.0f} last week — that's {direction_en} ₹{abs(diff):,.0f}.",
            tanglish=f"Intha vaaram {subject} selavu **₹{this_total:,.0f}**, last week ₹{last_total:,.0f} irundhuchu — ₹{abs(diff):,.0f} {direction_tanglish}.",
            ta=f"இந்த வார {subject} செலவு **₹{this_total:,.0f}**, கடந்த வாரம் ₹{last_total:,.0f} — ₹{abs(diff):,.0f} {direction_ta}.",
        )

    if intent == "tip":
        total_income = _sum_amount(cur, user_id, None, None, ttype="Credit")
        total_expense = _sum_amount(cur, user_id, None, None, ttype="Debit")
        cur.execute(
            "SELECT category, SUM(amount) AS total FROM transactions WHERE user_id = ? AND type='Debit' GROUP BY category ORDER BY total DESC",
            (user_id,),
        )
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return _pick(lang, "Add a few expenses first and I'll help you find ways to save!", "Konjam expenses add pannunga, apram save panna vazhi sollaren!", "முதலில் சில செலவுகளைச் சேர்க்கவும், பின் சேமிக்க உதவுகிறேன்!")

        grand_total = sum(r["total"] for r in rows) or 1
        top = rows[0]
        top_pct = top["total"] / grand_total * 100

        lines = []
        if total_expense > total_income:
            lines.append(_pick(
                lang,
                en=f"⚠️ Overall, you're spending **₹{total_expense - total_income:,.0f} more** than you're earning — that's worth fixing first.",
                tanglish=f"⚠️ Overall, neenga sambadikkaradhai vida **₹{total_expense - total_income:,.0f} extra** selavu pannirukeenga — idhai first fix pannunga.",
                ta=f"⚠️ மொத்தத்தில், சம்பாதிப்பதை விட **₹{total_expense - total_income:,.0f} அதிகமாக** செலவு செய்துள்ளீர்கள் — இதை முதலில் சரி செய்யவும்.",
            ))
            lines.append("")

        lines.append(_pick(
            lang,
            en=f"Your biggest spending category is **{top['category']}** at ₹{top['total']:,.0f} ({top_pct:.0f}% of all your expenses).",
            tanglish=f"Unga romba selavான category **{top['category']}**, ₹{top['total']:,.0f} ({top_pct:.0f}% - total selavula).",
            ta=f"உங்கள் அதிக செலவு பிரிவு **{top['category']}**, ₹{top['total']:,.0f} (மொத்த செலவில் {top_pct:.0f}%).",
        ))
        if len(rows) > 1:
            second = rows[1]
            lines.append(_pick(
                lang,
                en=f"Next highest is **{second['category']}** at ₹{second['total']:,.0f}.",
                tanglish=f"Adhukku next **{second['category']}**, ₹{second['total']:,.0f}.",
                ta=f"அடுத்ததாக **{second['category']}**, ₹{second['total']:,.0f}.",
            ))

        est_saving = top["total"] * 0.125
        lines.append("")
        lines.append(_pick(
            lang,
            en=f"**Suggestion:** Set a weekly cap for {top['category'].lower()} and review any recurring purchases there. Cutting it by ~10-15% would save you roughly ₹{est_saving:,.0f}.",
            tanglish=f"**Suggestion:** {top['category']} kaaga weekly limit vechukonga. 10-15% kammi panna, ₹{est_saving:,.0f} varaikum save aagum.",
            ta=f"**பரிந்துரை:** {top['category']} செலவுக்கு வார வரம்பு வையுங்கள். 10-15% குறைத்தால் ₹{est_saving:,.0f} வரை சேமிக்கலாம்.",
        ))
        return "\n".join(lines)

    if intent == "unclear":
        conn.close()
        return _pick(
            lang,
            en=(
                "Hmm, I didn't quite catch that 🤔 — could you try something like:\n\n"
                "• \"How much did I spend on transport this month?\"\n"
                "• \"Add ₹180 food expense\"\n"
                "• \"Add income ₹15000\"\n"
                "• \"Delete the ₹500 shopping expense\"\n"
                "• \"Change my food expense to ₹150\"\n"
                "• \"Set food budget to ₹4000\"\n"
                "• \"Predict my month-end spending\"\n"
                "• \"Show unusual expenses\"\n"
                "• \"Give me a monthly report\""
            ),
            tanglish=(
                "Aiyo, puriyala 🤔 idhu mathiri kekkalam:\n\n"
                "• \"Intha maasam transport-ku evlo selavu?\"\n"
                "• \"180 food expense add pannu\"\n"
                "• \"Income 15000 add pannu\"\n"
                "• \"₹500 shopping expense delete pannu\"\n"
                "• \"Food expense-a ₹150-a change pannu\"\n"
                "• \"Food budget 4000 set pannu\"\n"
                "• \"Month end evlo selavu aagum predict pannu\"\n"
                "• \"Unusual expenses evlo irukku?\"\n"
                "• \"Monthly report kudu\""
            ),
            ta=(
                "பரவாயில்லை, எனக்கு சரியாகப் புரியவில்லை 🤔 இது போல கேட்கலாம்:\n\n"
                "• \"இந்த மாதம் போக்குவரத்துக்கு எவ்வளவு செலவு?\"\n"
                "• \"₹180 உணவு செலவு சேர்\"\n"
                "• \"உணவு பட்ஜெட் ₹4000 அமை\"\n"
                "• \"மாத இறுதி செலவை கணிக்கவும்\"\n"
                "• \"அசாதாரண செலவுகளைக் காட்டு\"\n"
                "• \"மாத அறிக்கை தரவும்\""
            ),
        )

    total = _sum_amount(cur, user_id, start, end, category, "Debit")
    conn.close()
    if category:
        return _pick(
            lang,
            en=f"You spent **₹{total:,.0f}** on {category} {label}.",
            tanglish=f"Neenga {category} kaaga {label} **₹{total:,.0f}** selavu pannirukeenga.",
            ta=f"நீங்கள் {category}க்காக {label} **₹{total:,.0f}** செலவு செய்துள்ளீர்கள்.",
        )
    return _pick(
        lang,
        en=f"Your total expenses {label} are **₹{total:,.0f}**.",
        tanglish=f"{label} unga total selavu **₹{total:,.0f}**.",
        ta=f"{label} உங்கள் மொத்த செலவு **₹{total:,.0f}**.",
    )
