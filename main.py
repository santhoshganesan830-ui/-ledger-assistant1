"""
main.py
FastAPI backend for Ledger Assistant - an AI-powered expense tracker with
username/password accounts.

Run with:
    uvicorn main:app --reload

Then open http://127.0.0.1:8000 in your browser. You'll land on the
login page first; use "Create account" to sign up.
"""

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import auth
import chatbot
import receipt
from categorizer import suggest_category
from database import CATEGORY_COLORS, get_connection, init_db

app = FastAPI(title="Ledger Assistant API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@app.on_event("startup")
def _startup():
    init_db()


# ---------------------------------------------------------------- schemas --
class RegisterIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)


class LoginIn(BaseModel):
    username: str
    password: str


class ExpenseIn(BaseModel):
    date: str | None = None  # YYYY-MM-DD, defaults to today
    description: str = Field(..., min_length=1)
    category: str
    type: str = "Debit"  # "Debit" or "Credit"
    amount: float = Field(..., gt=0)


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1)


class CategorizeIn(BaseModel):
    description: str = ""
    amount: float | None = None


class ImportRow(BaseModel):
    date: str | None = None
    description: str = Field(..., min_length=1)
    category: str
    type: str = "Debit"
    amount: float = Field(..., gt=0)


class ImportIn(BaseModel):
    rows: list[ImportRow]


class BudgetIn(BaseModel):
    category: str
    monthly_limit: float = Field(..., gt=0)


class GoalIn(BaseModel):
    name: str = Field(..., min_length=1)
    target_amount: float = Field(..., gt=0)
    target_date: str | None = None


class GoalContributeIn(BaseModel):
    amount: float = Field(..., gt=0)


class BillIn(BaseModel):
    name: str = Field(..., min_length=1)
    amount: float = Field(..., gt=0)
    category: str
    due_day: int = Field(..., ge=1, le=28)


# ------------------------------------------------------------------ utils --
def _latest_date(cur, user_id):
    cur.execute("SELECT MAX(date) AS d FROM transactions WHERE user_id = ?", (user_id,))
    row = cur.fetchone()["d"]
    return datetime.fromisoformat(row).date() if row else datetime.now().date()


def _sum(cur, user_id, start, end, ttype):
    cur.execute(
        "SELECT COALESCE(SUM(amount),0) AS t FROM transactions WHERE user_id=? AND type=? AND date>=? AND date<=?",
        (user_id, ttype, start.isoformat(), end.isoformat()),
    )
    return cur.fetchone()["t"]


def _sum_all_time(cur, user_id, ttype):
    cur.execute(
        "SELECT COALESCE(SUM(amount),0) AS t FROM transactions WHERE user_id=? AND type=?",
        (user_id, ttype),
    )
    return cur.fetchone()["t"]


# -------------------------------------------------------------- auth API --
@app.post("/api/auth/register", status_code=201)
def register(payload: RegisterIn):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = ?", (payload.username,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(400, "That username is already taken.")

    salt, pwd_hash = auth.hash_password(payload.password)
    cur.execute(
        "INSERT INTO users (username, salt, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (payload.username, salt, pwd_hash, datetime.now().isoformat()),
    )
    user_id = cur.lastrowid

    token = auth.create_token()
    cur.execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
        (token, user_id, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return {"token": token, "username": payload.username}


@app.post("/api/auth/login")
def login(payload: LoginIn):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (payload.username,))
    user = cur.fetchone()
    if not user or not auth.verify_password(payload.password, user["salt"], user["password_hash"]):
        conn.close()
        raise HTTPException(401, "Incorrect username or password.")

    token = auth.create_token()
    cur.execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
        (token, user["id"], datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return {"token": token, "username": user["username"]}


@app.post("/api/auth/logout")
def logout(user=Depends(auth.get_current_user)):
    conn = get_connection()
    conn.execute("DELETE FROM sessions WHERE token = ?", (user["token"],))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=Depends(auth.get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT username, created_at FROM users WHERE id = ?", (user["id"],))
    row = cur.fetchone()
    cur.execute("SELECT COUNT(*) AS c FROM transactions WHERE user_id = ?", (user["id"],))
    tx_count = cur.fetchone()["c"]
    conn.close()
    return {"username": row["username"], "created_at": row["created_at"], "transaction_count": tx_count}


# ------------------------------------------------------------ data API ----
@app.get("/api/summary")
def summary(user=Depends(auth.get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    uid = user["id"]
    today = _latest_date(cur, uid)
    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    total_income = _sum(cur, uid, month_start, today, "Credit")
    total_expenses = _sum(cur, uid, month_start, today, "Debit")
    prev_income = _sum(cur, uid, prev_month_start, prev_month_end, "Credit")
    prev_expenses = _sum(cur, uid, prev_month_start, prev_month_end, "Debit")

    cur.execute(
        "SELECT COALESCE(SUM(amount),0) AS t, COUNT(*) AS c FROM transactions WHERE user_id=? AND type='Debit' AND date=?",
        (uid, today.isoformat()),
    )
    today_row = cur.fetchone()
    conn.close()

    def pct_change(now, prev):
        if prev == 0:
            return 0.0
        return round((now - prev) / prev * 100, 1)

    balance = total_income - total_expenses
    prev_balance = prev_income - prev_expenses

    return {
        "total_income": total_income,
        "total_expenses": total_expenses,
        "balance": balance,
        "today_expenses": today_row["t"],
        "today_count": today_row["c"],
        "income_change_pct": pct_change(total_income, prev_income),
        "expense_change_pct": pct_change(total_expenses, prev_expenses),
        "balance_change_pct": pct_change(balance, prev_balance),
        "today": today.isoformat(),
    }


@app.get("/api/daily-expenses")
def daily_expenses(user=Depends(auth.get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    uid = user["id"]
    today = _latest_date(cur, uid)
    month_start = today.replace(day=1)
    result = []
    d = month_start
    while d <= today:
        income = _sum(cur, uid, d, d, "Credit")
        expenses = _sum(cur, uid, d, d, "Debit")
        result.append({
            "date": d.isoformat(),
            "label": f"{d.strftime('%b')} {d.day}",
            "income": income,
            "expenses": expenses,
        })
        d += timedelta(days=1)
    conn.close()
    return result


@app.get("/api/category-expenses")
def category_expenses(user=Depends(auth.get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT category, SUM(amount) AS total FROM transactions WHERE user_id=? AND type='Debit' GROUP BY category ORDER BY total DESC",
        (user["id"],),
    )
    rows = cur.fetchall()
    conn.close()
    grand_total = sum(r["total"] for r in rows) or 1
    return {
        "total": grand_total,
        "categories": [
            {
                "category": r["category"],
                "amount": r["total"],
                "percent": round(r["total"] / grand_total * 100),
                "color": CATEGORY_COLORS.get(r["category"], "#95A5A6"),
            }
            for r in rows
        ],
    }


@app.get("/api/portfolio")
def portfolio(user=Depends(auth.get_current_user)):
    """All-time totals for the Portfolio view - unlike /api/summary, which
    is scoped to the current month, this covers every transaction ever
    recorded on the account."""
    conn = get_connection()
    cur = conn.cursor()
    uid = user["id"]
    total_income = _sum_all_time(cur, uid, "Credit")
    total_expenses = _sum_all_time(cur, uid, "Debit")
    conn.close()
    return {
        "total_income": total_income,
        "total_expenses": total_expenses,
        "balance": total_income - total_expenses,
    }


@app.get("/api/transactions")
def transactions(limit: int = 10, user=Depends(auth.get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    uid = user["id"]
    cur.execute(
        "SELECT id, date, description, category, type, amount FROM transactions WHERE user_id=? ORDER BY date DESC, id DESC LIMIT ?",
        (uid, limit),
    )
    rows = [dict(r) for r in cur.fetchall()]

    # Running balance the same way the ledger table does (oldest -> newest)
    cur.execute(
        "SELECT id, date, type, amount FROM transactions WHERE user_id=? ORDER BY date ASC, id ASC",
        (uid,),
    )
    running = 0.0
    balance_by_id = {}
    for r in cur.fetchall():
        running += r["amount"] if r["type"] == "Credit" else -r["amount"]
        balance_by_id[r["id"]] = running
    conn.close()

    for r in rows:
        r["balance"] = round(balance_by_id.get(r["id"], 0), 2)
    return rows


@app.post("/api/expenses", status_code=201)
def add_expense(expense: ExpenseIn, user=Depends(auth.get_current_user)):
    if expense.type not in ("Debit", "Credit"):
        raise HTTPException(400, "type must be 'Debit' or 'Credit'")
    date = expense.date or datetime.now().date().isoformat()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions (user_id, date, description, category, type, amount) VALUES (?,?,?,?,?,?)",
        (user["id"], date, expense.description, expense.category, expense.type, expense.amount),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return {"id": new_id, **expense.model_dump(), "date": date}


@app.post("/api/categorize")
def categorize(payload: CategorizeIn, user=Depends(auth.get_current_user)):
    """Rule-based category suggestion for a description (+ optional amount)."""
    return suggest_category(payload.description, payload.amount)


MAX_RECEIPT_BYTES = 8 * 1024 * 1024


@app.post("/api/receipt/scan")
async def scan_receipt(file: UploadFile = File(...), user=Depends(auth.get_current_user)):
    """OCR a receipt photo and return *suggested* shop/amount/date/category.
    Nothing is saved here - the frontend shows the values for the user to
    confirm, then saves through the normal /api/expenses endpoint."""
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Please upload an image file.")
    data = await file.read()
    if len(data) > MAX_RECEIPT_BYTES:
        raise HTTPException(413, "Image is too large (max 8 MB).")
    try:
        text = receipt.ocr_image(data)
    except receipt.OCRUnavailable as e:
        raise HTTPException(503, str(e))
    except Exception:
        raise HTTPException(422, "Could not read that image.")
    return receipt.parse_receipt_text(text)


@app.post("/api/expenses/import", status_code=201)
def import_expenses(payload: ImportIn, user=Depends(auth.get_current_user)):
    """Restores/imports transactions in bulk (from a previously exported
    CSV, for example). Rows with an invalid type are skipped rather than
    failing the whole batch."""
    conn = get_connection()
    cur = conn.cursor()
    inserted = 0
    for row in payload.rows:
        if row.type not in ("Debit", "Credit"):
            continue
        date = row.date or datetime.now().date().isoformat()
        cur.execute(
            "INSERT INTO transactions (user_id, date, description, category, type, amount) VALUES (?,?,?,?,?,?)",
            (user["id"], date, row.description, row.category, row.type, row.amount),
        )
        inserted += 1
    conn.commit()
    conn.close()
    return {"inserted": inserted}


@app.delete("/api/expenses/{expense_id}", status_code=204)
def delete_expense(expense_id: int, user=Depends(auth.get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM transactions WHERE id = ? AND user_id = ?",
        (expense_id, user["id"]),
    )
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, "Transaction not found.")
    cur.execute("DELETE FROM transactions WHERE id = ?", (expense_id,))
    conn.commit()
    conn.close()
    return None


@app.get("/api/categories")
def categories():
    return list(CATEGORY_COLORS.keys()) + ["Income"]


@app.post("/api/chat")
def chat(payload: ChatIn, user=Depends(auth.get_current_user)):
    reply = chatbot.answer(payload.message, user["id"], user["username"])
    return {"reply": reply}


# --------------------------------------------------------- budgets API ----
@app.get("/api/budgets")
def list_budgets(user=Depends(auth.get_current_user)):
    """One row per expense category, with this month's spend against
    whatever monthly limit the user has set (null if none set yet)."""
    conn = get_connection()
    cur = conn.cursor()
    uid = user["id"]
    today = _latest_date(cur, uid)
    month_start = today.replace(day=1)

    cur.execute("SELECT category, monthly_limit FROM budgets WHERE user_id = ?", (uid,))
    limits = {r["category"]: r["monthly_limit"] for r in cur.fetchall()}

    out = []
    for category in CATEGORY_COLORS.keys():
        cur.execute(
            "SELECT COALESCE(SUM(amount),0) AS t FROM transactions WHERE user_id=? AND type='Debit' AND category=? AND date>=? AND date<=?",
            (uid, category, month_start.isoformat(), today.isoformat()),
        )
        spent = cur.fetchone()["t"]
        limit = limits.get(category)
        percent = round(spent / limit * 100) if limit else None
        status = "none"
        if limit:
            status = "over" if percent >= 100 else "warning" if percent >= 80 else "ok"
        out.append({
            "category": category,
            "monthly_limit": limit,
            "spent": spent,
            "percent": percent,
            "status": status,
            "color": CATEGORY_COLORS.get(category, "#95A5A6"),
        })
    conn.close()
    return out


@app.post("/api/budgets", status_code=201)
def set_budget(payload: BudgetIn, user=Depends(auth.get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO budgets (user_id, category, monthly_limit) VALUES (?,?,?) "
        "ON CONFLICT(user_id, category) DO UPDATE SET monthly_limit = excluded.monthly_limit",
        (user["id"], payload.category, payload.monthly_limit),
    )
    conn.commit()
    conn.close()
    return {"category": payload.category, "monthly_limit": payload.monthly_limit}


@app.delete("/api/budgets/{category}", status_code=204)
def clear_budget(category: str, user=Depends(auth.get_current_user)):
    conn = get_connection()
    conn.execute("DELETE FROM budgets WHERE user_id = ? AND category = ?", (user["id"], category))
    conn.commit()
    conn.close()
    return None


# ----------------------------------------------------------- goals API ----
@app.get("/api/goals")
def list_goals(user=Depends(auth.get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, target_amount, saved_amount, target_date, created_at FROM goals WHERE user_id=? ORDER BY created_at DESC",
        (user["id"],),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        r["percent"] = round(min(r["saved_amount"] / r["target_amount"], 1) * 100) if r["target_amount"] else 0
    return rows


@app.post("/api/goals", status_code=201)
def create_goal(payload: GoalIn, user=Depends(auth.get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO goals (user_id, name, target_amount, saved_amount, target_date, created_at) VALUES (?,?,?,0,?,?)",
        (user["id"], payload.name, payload.target_amount, payload.target_date, datetime.now().isoformat()),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return {"id": new_id, "name": payload.name, "target_amount": payload.target_amount, "saved_amount": 0, "target_date": payload.target_date}


@app.post("/api/goals/{goal_id}/contribute")
def contribute_goal(goal_id: int, payload: GoalContributeIn, user=Depends(auth.get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, saved_amount FROM goals WHERE id=? AND user_id=?", (goal_id, user["id"]))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Goal not found.")
    new_saved = row["saved_amount"] + payload.amount
    cur.execute("UPDATE goals SET saved_amount=? WHERE id=?", (new_saved, goal_id))
    conn.commit()
    conn.close()
    return {"id": goal_id, "saved_amount": new_saved}


@app.delete("/api/goals/{goal_id}", status_code=204)
def delete_goal(goal_id: int, user=Depends(auth.get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM goals WHERE id=? AND user_id=?", (goal_id, user["id"]))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, "Goal not found.")
    cur.execute("DELETE FROM goals WHERE id=?", (goal_id,))
    conn.commit()
    conn.close()
    return None


# ----------------------------------------------------------- bills API ----
def _next_due(due_day: int, today, last_paid_date: str | None = None):
    """The next occurrence of due_day on/after today, this month or next.
    If the bill was already paid for the current cycle, skip ahead to the
    following month so a freshly-paid bill doesn't still read "due now"."""
    from calendar import monthrange
    this_month_days = monthrange(today.year, today.month)[1]
    day = min(due_day, this_month_days)
    candidate = today.replace(day=day)
    already_paid_this_cycle = bool(last_paid_date) and datetime.fromisoformat(last_paid_date).date() >= candidate.replace(day=1)
    if candidate >= today and not already_paid_this_cycle:
        return candidate
    year, month = (today.year, today.month + 1) if today.month < 12 else (today.year + 1, 1)
    next_month_days = monthrange(year, month)[1]
    return today.replace(year=year, month=month, day=min(due_day, next_month_days))


@app.get("/api/bills")
def list_bills(user=Depends(auth.get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    uid = user["id"]
    today = _latest_date(cur, uid)
    cur.execute(
        "SELECT id, name, amount, category, due_day, last_paid_date, created_at FROM bills WHERE user_id=? ORDER BY due_day ASC",
        (uid,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        next_due = _next_due(r["due_day"], today, r["last_paid_date"])
        r["next_due_date"] = next_due.isoformat()
        r["days_until"] = (next_due - today).days
    return rows


@app.post("/api/bills", status_code=201)
def create_bill(payload: BillIn, user=Depends(auth.get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO bills (user_id, name, amount, category, due_day, last_paid_date, created_at) VALUES (?,?,?,?,?,NULL,?)",
        (user["id"], payload.name, payload.amount, payload.category, payload.due_day, datetime.now().isoformat()),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return {"id": new_id, **payload.model_dump()}


@app.post("/api/bills/{bill_id}/pay", status_code=201)
def pay_bill(bill_id: int, user=Depends(auth.get_current_user)):
    """Marks a bill paid for this cycle and logs it as a real expense
    transaction, so it shows up in the ledger and every chart/total."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM bills WHERE id=? AND user_id=?", (bill_id, user["id"]))
    bill = cur.fetchone()
    if not bill:
        conn.close()
        raise HTTPException(404, "Bill not found.")
    today = _latest_date(cur, user["id"]).isoformat()
    cur.execute(
        "INSERT INTO transactions (user_id, date, description, category, type, amount) VALUES (?,?,?,?,?,?)",
        (user["id"], today, f"{bill['name']} (bill)", bill["category"], "Debit", bill["amount"]),
    )
    cur.execute("UPDATE bills SET last_paid_date=? WHERE id=?", (today, bill_id))
    conn.commit()
    conn.close()
    return {"id": bill_id, "last_paid_date": today}


@app.delete("/api/bills/{bill_id}", status_code=204)
def delete_bill(bill_id: int, user=Depends(auth.get_current_user)):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM bills WHERE id=? AND user_id=?", (bill_id, user["id"]))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, "Bill not found.")
    cur.execute("DELETE FROM bills WHERE id=?", (bill_id,))
    conn.commit()
    conn.close()
    return None


# ---------------------------------------------------------- static files --
# Serve the plain HTML/CSS/JS frontend from the same server so the whole
# app runs with a single `uvicorn main:app` command. Mounted last (and at
# "/") so it never shadows the /api/* routes above, while still serving
# index.html / login.html / styles.css / script.js at their plain paths.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
