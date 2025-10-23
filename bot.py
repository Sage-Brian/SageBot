"""
SageBot v2 - Advanced Document Analysis Telegram Bot
Features:
- Paid-access system (30/90-day codes), persistent storage (paid_users.json)
- Admin commands to grant/revoke access
- M-Pesa payment integration
- Document handling: .txt, .docx, .pdf
- AI-detection heuristics, plagiarism detection, grammar checking
- Generates detailed PDF & Word reports (via report_generator.py)
"""

# ------------------ IMPORTS ------------------
from report_generator import generate_colored_pdf
import os, time, json
from functools import wraps
from collections import Counter
import pandas as pd
from rapidfuzz import fuzz
from textblob import TextBlob

try:
    from language_tool_python import LanguageTool
    LT_AVAILABLE = True
except Exception:
    LT_AVAILABLE = False

try:
    from docx import Document as DocxDocument
except Exception:
    DocxDocument = None

try:
    from PyPDF2 import PdfReader
except Exception:
    PdfReader = None

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)

# ✅ Import M-Pesa and report generator
from payment_mpesa import lipa_na_mpesa
from report_generator import generate_colored_pdf, generate_word_report

# ------------------ CONFIGURATION ------------------
BOT_TOKEN = "8130637111:AAFHI4pCRdsirPft96mU34DyPjVtxzDhVuI"
ADMIN_USER_ID = 7705556471

PAID_USERS_FILE = "paid_users.json"
PLAG_DB_FILE = "plagiarism_database.csv"
DOWNLOADS_DIR = "downloads"
REPORTS_DIR = "reports"

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

# ------------------ UTILITIES / PERSISTENCE ------------------
def load_paid_users():
    if os.path.exists(PAID_USERS_FILE):
        try:
            with open(PAID_USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_paid_users(data=None):
    data = data if data is not None else PAID_USERS
    with open(PAID_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)

PAID_USERS = load_paid_users()

def now_ts(): 
    return time.time()

def user_has_access(user_id: int) -> bool:
    uid = str(user_id)
    expiry = PAID_USERS.get(uid)
    return (expiry and expiry > now_ts()) or int(user_id) == ADMIN_USER_ID

def grant_user(user_id: int, days: int):
    PAID_USERS[str(user_id)] = now_ts() + days * 24 * 3600
    save_paid_users()

def revoke_user(user_id: int):
    uid = str(user_id)
    if uid in PAID_USERS:
        del PAID_USERS[uid]
        save_paid_users()

# ------------------ DECORATORS ------------------
def restricted(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if user_has_access(update.effective_user.id):
            return await func(update, context, *args, **kwargs)
        await update.message.reply_text("🚫 You do not have active access. Use /pay_mpesastk to purchase.")
    return wrapped

def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_user.id != ADMIN_USER_ID:
            await update.message.reply_text("⛔ Only admin can use this command.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# ------------------ DOCUMENT HANDLING ------------------
def safe_read_text(path: str) -> str:
    path = str(path)
    if path.lower().endswith(".txt"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    if path.lower().endswith(".docx") and DocxDocument:
        doc = DocxDocument(path)
        return "\n".join(p.text for p in doc.paragraphs)
    if path.lower().endswith(".pdf") and PdfReader:
        reader = PdfReader(path)
        return "\n".join([p.extract_text() or "" for p in reader.pages])
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

# ------------------ ANALYSIS ------------------
def lexical_richness(text: str) -> float:
    words = [w for w in text.split() if w.isalpha()]
    return len(set(words)) / len(words) if words else 0.0

def average_sentence_length(text: str) -> float:
    sents = [s.strip() for s in text.replace("\r", "\n").split("\n") if s.strip()]
    lens = [len(s.split()) for s in sents]
    return sum(lens) / max(1, len(lens)) if lens else 0.0

def repetitiveness_score(text: str) -> float:
    words = [w for w in text.split() if w]
    if len(words) < 6:
        return 0.0
    ngrams = [" ".join(words[i:i + 3]) for i in range(len(words) - 2)]
    cnt = Counter(ngrams)
    repeats = sum(v - 1 for v in cnt.values() if v > 1)
    return repeats / max(1, len(ngrams))

def template_similarity_score(text: str) -> float:
    templates = [
        "As an AI language model",
        "This article discusses",
        "In conclusion, this",
        "It is important to note that",
        "The following text provides an overview",
    ]
    scores = [fuzz.partial_ratio(text[:400], t) for t in templates]
    return max(scores) / 100.0

def ai_detection_advanced(text: str) -> dict:
    richness = lexical_richness(text)
    avg_sent = average_sentence_length(text)
    repet = repetitiveness_score(text)
    templ = template_similarity_score(text)
    s_richness = max(0.0, (0.35 - richness) / 0.35)
    s_avg_sent = max(0.0, min(1.0, abs(avg_sent - 20) / 20))
    s_repet = max(0.0, min(1.0, repet))
    s_templ = templ
    combined = (0.35 * s_richness + 0.25 * s_avg_sent + 0.25 * s_repet + 0.15 * s_templ)
    return {
        "percent": int(round(combined * 100)),
        "components": {
            "lexical_richness": richness,
            "avg_sentence_length": avg_sent,
            "repetitiveness": repet,
            "template_similarity": templ,
        },
    }

def plagiarism_check_local(text: str, top_n: int = 3) -> dict:
    if not os.path.exists(PLAG_DB_FILE):
        return {"percent": 0, "matches": []}
    try:
        df = pd.read_csv(PLAG_DB_FILE)
    except:
        return {"percent": 0, "matches": []}
    best = 0
    matches = []
    for _, row in df.iterrows():
        content = str(row.get("content", "") or "")
        score = fuzz.token_set_ratio(text[:2000], content[:2000])
        if score >= 50:
            matches.append({"score": int(score), "snippet": content[:400]})
        if score > best:
            best = score
    matches = sorted(matches, key=lambda x: x["score"], reverse=True)[:top_n]
    return {"percent": int(best), "matches": matches}

def grammar_and_corrections(text: str) -> dict:
    corrected = text
    issues = []
    if LT_AVAILABLE:
        try:
            tool = LanguageTool("en-US")
            matches = tool.check(text)
            issues = [{"message": m.message} for m in matches]
            corrected = tool.correct(text)
        except:
            pass
    else:
        tb = TextBlob(text)
        corrected = str(tb.correct())
    return {"issues": issues, "corrected": corrected, "issue_count": len(issues)}

# ------------------ PAYMENT ------------------
PAY_STEP_PHONE = 1

@restricted
async def pay_mpesastk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_has_access(user_id):
        await update.message.reply_text("✅ You already have active access.")
        return ConversationHandler.END
    await update.message.reply_text("💳 Enter phone number 2547XXXXXXXX to pay for 30-day access:")
    return PAY_STEP_PHONE

async def handle_phone_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("254") or len(phone) != 12:
        await update.message.reply_text("⚠️ Invalid format. Use 2547XXXXXXXX.")
        return PAY_STEP_PHONE
    response = lipa_na_mpesa(phone, 200)
    if "error" in response:
        await update.message.reply_text(f"❌ Payment failed: {response['error']}")
    else:
        await update.message.reply_text(f"✅ Payment request sent to {phone}. Complete payment on your phone.")
    return ConversationHandler.END

# ------------------ COMMANDS ------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to SageBot v2!\n"
        "Send a document (.txt/.docx/.pdf) for AI analysis and plagiarism check.\n"
        "Use /pay_mpesastk to purchase access."
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - Intro\n"
        "/help - This message\n"
        "/pay_mpesastk - Pay for 30-day access\n"
        "/report - Get analysis report"
    )

# ------------------ ADMIN COMMANDS ------------------
@admin_only
async def cmd_grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("Usage: /grant <user_id> <days>")
        return
    try:
        uid = int(args[0])
        days = int(args[1])
    except:
        await update.message.reply_text("Invalid arguments.")
        return
    grant_user(uid, days)
    await update.message.reply_text(f"✅ Granted {days} days to {uid}.")

@admin_only
async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /revoke <user_id>")
        return
    try:
        uid = int(args[0])
    except:
        await update.message.reply_text("Invalid user ID")
        return
    revoke_user(uid)
    await update.message.reply_text(f"✅ Revoked access for {uid}.")

@admin_only
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = now_ts()
    lines = []
    for uid, expiry in PAID_USERS.items():
        remain = max(0, int(expiry - now))
        days = remain // (24 * 3600)
        lines.append(f"{uid} — expires in {days} days")
    await update.message.reply_text("\n".join(lines) if lines else "No paid users.")

# ------------------ DOCUMENT HANDLER ------------------
@restricted
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        await update.message.reply_text("⚠️ No document detected.")
        return
    file_obj = await doc.get_file()
    filename = f"{DOWNLOADS_DIR}/{int(time.time())}_{doc.file_name}"
    await file_obj.download_to_drive(filename)

    try:
        text = safe_read_text(filename)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error reading file: {e}")
        return
    if not text.strip():
        await update.message.reply_text("⚠️ Empty text")
        return

    ai = ai_detection_advanced(text)
    plag = plagiarism_check_local(text)
    grammar = grammar_and_corrections(text)

    context.user_data["analysis"] = {
        "filename": doc.file_name,
        "ai": ai,
        "plagiarism": plag,
        "grammar": grammar,
        "raw_text_path": filename,
        "timestamp": now_ts(),
    }

    await update.message.reply_text(
        f"✅ Received your document: {doc.file_name}\n"
        f"🧠 AI suspicion: {ai['percent']}%\n"
        f"📚 Plagiarism top match: {plag['percent']}%\n"
        f"✍️ Grammar issues: {grammar['issue_count']}\n"
        f"Use /report to get a detailed report."
    )

# ------------------ /report COMMAND ------------------
@restricted
async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data.get("analysis")
    if not data:
        await update.message.reply_text("⚠️ No analysis found. Upload a document first.")
        return

    sections = {
        "Summary": (
            f"File: {data['filename']}\n"
            f"AI Suspicion: {data['ai']['percent']}%\n"
            f"Plagiarism: {data['plagiarism']['percent']}%\n"
            f"Grammar issues: {data['grammar']['issue_count']}"
        ),
        "AI Breakdown": json.dumps(data["ai"]["components"], indent=2),
        "Plagiarism Matches": "\n".join(
            [f"{m['score']}% - {m['snippet']}" for m in data["plagiarism"].get("matches", [])]
        ),
        "Grammar Corrections": data["grammar"]["corrected"],
    }

    pdf_path = f"{REPORTS_DIR}/{int(time.time())}_{data['filename']}.pdf"
    word_path = f"{REPORTS_DIR}/{int(time.time())}_{data['filename']}.docx"

    generate_colored_pdf(pdf_path, f"Analysis Report - {data['filename']}", sections, highlights=["AI", "plagiarized"])
    generate_word_report(word_path, f"Analysis Report - {data['filename']}", sections, highlights=["AI", "plagiarized"])

    await update.message.reply_text("📄 Your detailed reports are ready:")
    await update.message.reply_document(open(pdf_path, "rb"))
    await update.message.reply_document(open(word_path, "rb"))

# ------------------ MAIN APPLICATION ------------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("report", cmd_report))

    # Admin commands
    app.add_handler(CommandHandler("grant", cmd_grant))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("users", cmd_users))

    # Document handler
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Payment ConversationHandler
    pay_conv = ConversationHandler(
        entry_points=[CommandHandler("pay_mpesastk", pay_mpesastk)],
        states={PAY_STEP_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone_input)]},
        fallbacks=[]
    )
    app.add_handler(pay_conv)

    app.run_polling()

if _name_ == "_main_":
    main()