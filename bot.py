import logging
import json
import os
import random
from datetime import datetime
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from groq import Groq

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

DATA_FILE = "data.json"
LOG_FILE = "bot.log"
GROQ_MODEL = "llama-3.3-70b-versatile"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


# ── Persistence ───────────────────────────────────────────────────────────────
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"entries": [], "categories": [], "conversation": []}


def save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ── System Prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are Fola's personal documentation assistant on Telegram. You receive raw, unfiltered thoughts and organize them.

BEHAVIOR:
When Fola sends a message:
1. Silently categorize it into the best-fitting bucket. If it doesn't fit existing categories, create one and ask: "I've created a new group called [X] — keep that name or rename it?"
2. Confirm receipt with ONE line: "Logged → [Category] [emoji]"
3. If it's a standalone quote or statement, tag it #quote and log to Quotes Vault.
4. Save the entry as JSON using this exact format on the LAST line of your response:
   ENTRY:{"category":"..","text":"..","tags":[],"timestamp":".."}

DEFAULT CATEGORIES (expand as needed):
- 📖 Book Material
- 💭 Dreams
- 💡 Ideas & Fleeting Thoughts
- 🗣️ Quotes & Statements
- 📋 To-Do / Reminders

COMMANDS (when user types these):
- /summary — show all categories with entry counts and last 2 entries each
- /quotes — list all #quote entries
- /categories — list all categories
- /search [term] — search entries
- /clear_history — reset conversation memory (entries are kept)

RULES:
- Be extremely brief. One or two lines max unless asked for more.
- Only ask when categorization is genuinely ambiguous.
- On /summary, format cleanly with category headers.
- Never lecture or over-explain.
- Treat every message as valuable.

Current categories will be injected as context into each message."""


# ── Groq Call ─────────────────────────────────────────────────────────────────
def ask_groq(user_message: str, conversation: list, categories: list) -> str:
    client = Groq(api_key=GROQ_API_KEY)

    context_note = f"\n\n[Current categories: {', '.join(categories) if categories else 'none yet'}]"

    messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + conversation[-10:]
        + [{"role": "user", "content": user_message + context_note}]
    )

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=500,
        messages=messages,
    )
    return response.choices[0].message.content


# ── Entry Parsing ─────────────────────────────────────────────────────────────
def parse_entry(response_text: str) -> dict | None:
    for line in response_text.strip().splitlines():
        if line.startswith("ENTRY:"):
            try:
                return json.loads(line[6:])
            except json.JSONDecodeError:
                pass
    return None


def clean_response(response_text: str) -> str:
    lines = [l for l in response_text.splitlines() if not l.startswith("ENTRY:")]
    return "\n".join(lines).strip()


# ── Random Check-ins ──────────────────────────────────────────────────────────
CHECKINS = [
    "💭 You haven't revisited your Book Material recently. Want a summary?",
    "✨ Reminder: you've been building something. Keep logging.",
    "📖 Your Quotes Vault has grown. Want me to surface a random one?",
    "🧠 Any new thoughts? I'm here whenever.",
    "💡 Idea check: anything brewing you haven't written down yet?",
]


async def random_checkin(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data["chat_id"]
    await context.bot.send_message(chat_id=chat_id, text=random.choice(CHECKINS))


# ── Command: /start ───────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    await update.message.reply_text(
        "👋 PA Bot active. Send me your thoughts — I'll sort and log everything.\n\n"
        "Commands:\n"
        "/summary — overview of all entries\n"
        "/quotes — your quotes vault\n"
        "/categories — all categories\n"
        "/search [term] — find entries\n"
        "/clear_history — reset memory"
    )

    # Schedule randomized check-ins (every 6–18 hours)
    interval = random.randint(6 * 3600, 18 * 3600)
    context.job_queue.run_repeating(
        random_checkin,
        interval=interval,
        first=interval,
        data={"chat_id": chat_id},
        name=f"checkin_{chat_id}",
    )


# ── Message Handler ───────────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    data = load_data()

    response = ask_groq(user_text, data["conversation"][-10:], data["categories"])
    reply = clean_response(response)

    entry = parse_entry(response)
    if entry:
        entry["timestamp"] = datetime.now().isoformat()
        data["entries"].append(entry)
        cat = entry.get("category", "")
        if cat and cat not in data["categories"]:
            data["categories"].append(cat)

    data["conversation"].append({"role": "user", "content": user_text})
    data["conversation"].append({"role": "assistant", "content": response})
    data["conversation"] = data["conversation"][-20:]

    save_data(data)
    await update.message.reply_text(reply)


# ── Command: /summary ─────────────────────────────────────────────────────────
async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data["entries"]:
        await update.message.reply_text("No entries yet. Start sending thoughts!")
        return

    grouped: dict[str, list] = {}
    for e in data["entries"]:
        grouped.setdefault(e.get("category", "Uncategorized"), []).append(e)

    lines = ["📂 *Your Documentation Summary*\n"]
    for cat, entries in grouped.items():
        lines.append(f"*{cat}* — {len(entries)} entries")
        for e in entries[-2:]:
            snippet = e["text"][:80] + ("…" if len(e["text"]) > 80 else "")
            lines.append(f"  • {snippet}")
        lines.append("")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Command: /quotes ──────────────────────────────────────────────────────────
async def cmd_quotes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    quotes = [e for e in data["entries"] if "#quote" in e.get("tags", [])]

    if not quotes:
        await update.message.reply_text("No quotes logged yet.")
        return

    lines = ["🗣️ *Quotes Vault*\n"]
    for i, q in enumerate(quotes, 1):
        lines.append(f"{i}. _{q['text']}_")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Command: /categories ──────────────────────────────────────────────────────
async def cmd_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data["categories"]:
        await update.message.reply_text("No categories yet.")
        return

    count_map: dict[str, int] = {}
    for e in data["entries"]:
        cat = e.get("category", "Uncategorized")
        count_map[cat] = count_map.get(cat, 0) + 1

    lines = ["📁 *Categories*\n"]
    for cat in data["categories"]:
        lines.append(f"• {cat} ({count_map.get(cat, 0)})")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Command: /search ──────────────────────────────────────────────────────────
async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).lower()
    if not query:
        await update.message.reply_text("Usage: /search [term]")
        return

    data = load_data()
    results = [e for e in data["entries"] if query in e["text"].lower()]

    if not results:
        await update.message.reply_text(f'No entries found for "{query}".')
        return

    lines = [f"🔍 *Results for '{query}'*\n"]
    for e in results[:10]:
        lines.append(f"• [{e.get('category', '')}] {e['text'][:100]}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Command: /clear_history ───────────────────────────────────────────────────
async def cmd_clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    data["conversation"] = []
    save_data(data)
    await update.message.reply_text("🧹 Conversation memory cleared. Entries are safe.")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("quotes", cmd_quotes))
    app.add_handler(CommandHandler("categories", cmd_categories))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("clear_history", cmd_clear_history))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()