import logging
import json
import os
import random
from datetime import datetime
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import Application, CommandHandler, filters, ContextTypes, InlineQueryHandler
from groq import Groq
from dotenv import load_dotenv
from pathlib import Path

# Force load .env with override
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path, override=True)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

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

# ── Groq Call for Processing (No Response) ────────────────────────────────────
def process_with_groq(user_message: str, conversation: list, categories: list) -> dict | None:
    """Process message with Groq but don't return a response to user"""
    client = Groq(api_key=GROQ_API_KEY)
    
    # Modified system prompt - SILENT mode
    silent_prompt = """You are Fola's personal documentation assistant. You work SILENTLY in the background.

CRITICAL RULES:
1. NEVER respond to the user with any message
2. Process the input and ONLY output the JSON entry
3. Do NOT add any text before or after the JSON
4. Your ENTIRE response should be valid JSON only

Output format (and nothing else):
{"category":"category name","text":"original message","tags":[],"timestamp":"auto_fill"}

Categories to use:
- 📖 Book Material
- 💭 Dreams  
- 💡 Ideas & Fleeting Thoughts
- 🗣️ Quotes & Statements
- 📋 To-Do / Reminders

If message doesn't fit existing category, create a new one.

Example response:
{"category":"💡 Ideas & Fleeting Thoughts","text":"I should build a mobile app","tags":["idea"],"timestamp":""}"""

    context_note = f"\n\nCurrent categories: {', '.join(categories) if categories else 'none yet'}"

    messages = (
        [{"role": "system", "content": silent_prompt}]
        + conversation[-5:]
        + [{"role": "user", "content": user_message + context_note}]
    )

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=200,
        messages=messages,
    )
    
    # Try to parse the response as JSON
    try:
        content = response.choices[0].message.content.strip()
        content = content.replace('```json', '').replace('```', '').strip()
        entry_data = json.loads(content)
        return entry_data
    except json.JSONDecodeError:
        # Fallback: create basic entry
        return {
            "category": "Uncategorized",
            "text": user_message,
            "tags": [],
            "timestamp": datetime.now().isoformat()
        }

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

# ── Inline Query Handler with Additional Features ────────────────────────────
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline queries with multiple features"""
    query = update.inline_query.query.strip()
    user_id = update.inline_query.from_user.id
    
    # Feature 1: Show recent entries
    if query == "recent":
        data = load_data()
        results = []
        for i, entry in enumerate(data["entries"][-5:]):
            results.append(
                InlineQueryResultArticle(
                    id=f"recent_{i}",
                    title=f"📝 {entry.get('category', 'Uncategorized')}",
                    description=entry.get('text', '')[:60],
                    input_message_content=InputTextMessageContent(
                        f"*{entry.get('category', 'Uncategorized')}*\n\n{entry.get('text', '')}",
                        parse_mode="Markdown"
                    )
                )
            )
        await update.inline_query.answer(results[:20], cache_time=10)
        return
    
    # Feature 2: Show stats
    if query == "stats":
        data = load_data()
        total_entries = len(data["entries"])
        total_categories = len(data["categories"])
        
        # Get category counts
        category_counts = {}
        for entry in data["entries"]:
            cat = entry.get("category", "Uncategorized")
            category_counts[cat] = category_counts.get(cat, 0) + 1
        
        stats_text = f"📊 *Bot Statistics*\n\n"
        stats_text += f"Total entries: {total_entries}\n"
        stats_text += f"Total categories: {total_categories}\n\n"
        stats_text += "*Top categories:*\n"
        for cat, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
            stats_text += f"• {cat}: {count}\n"
        
        results = [
            InlineQueryResultArticle(
                id="stats",
                title="📊 View Statistics",
                description=f"{total_entries} total entries across {total_categories} categories",
                input_message_content=InputTextMessageContent(stats_text, parse_mode="Markdown")
            )
        ]
        await update.inline_query.answer(results, cache_time=30)
        return
    
    # Feature 3: Show random quote
    if query == "random":
        data = load_data()
        quotes = [e for e in data["entries"] if "quote" in str(e.get("tags", [])).lower() or "🗣️" in e.get("category", "")]
        
        if quotes:
            random_quote = random.choice(quotes)
            quote_text = f"🗣️ *Random Quote*\n\n_{random_quote.get('text', '')}_"
        else:
            quote_text = "No quotes logged yet. Start logging quotes with #quote!"
        
        results = [
            InlineQueryResultArticle(
                id="random_quote",
                title="🎲 Random Quote",
                description="Get a random quote from your vault",
                input_message_content=InputTextMessageContent(quote_text, parse_mode="Markdown")
            )
        ]
        await update.inline_query.answer(results, cache_time=5)
        return
    
    # Feature 4: Help - show all inline commands
    if query == "help" or query == "commands":
        help_text = """*🤖 Inline Commands Available*

Type @YourBotName followed by:

• `recent` - Show last 5 entries
• `stats` - View bot statistics  
• `random` - Get random quote
• `search KEYWORD` - Search entries
• `help` - Show this help

*Quick logging:*
Just type your thought directly to log it!

*Examples:*
• `@YourBot recent`
• `@YourBot search project`
• `@YourBot I have a new idea!`"""
        
        results = [
            InlineQueryResultArticle(
                id="help",
                title="❓ Help & Commands",
                description="Show all available inline commands",
                input_message_content=InputTextMessageContent(help_text, parse_mode="Markdown")
            )
        ]
        await update.inline_query.answer(results, cache_time=60)
        return
    
    # Feature 5: Search functionality
    if query.lower().startswith("search "):
        search_term = query[7:].strip()
        data = load_data()
        results = [e for e in data["entries"] if search_term.lower() in e["text"].lower()]
        
        if results:
            inline_results = []
            for i, entry in enumerate(results[:10]):
                inline_results.append(
                    InlineQueryResultArticle(
                        id=f"search_{i}",
                        title=f"🔍 {entry.get('category', 'Entry')}",
                        description=entry.get('text', '')[:60],
                        input_message_content=InputTextMessageContent(
                            f"*Search result for '{search_term}':*\n\n{entry.get('text', '')}",
                            parse_mode="Markdown"
                        )
                    )
                )
            await update.inline_query.answer(inline_results, cache_time=10)
        else:
            no_results = [
                InlineQueryResultArticle(
                    id="no_results",
                    title="❌ No results found",
                    description=f"No entries containing '{search_term}'",
                    input_message_content=InputTextMessageContent(f"No entries found for '{search_term}'")
                )
            ]
            await update.inline_query.answer(no_results, cache_time=5)
        return
    
    # Default: Log any other text as a thought (silent logging)
    if query:
        data = load_data()
        
        # Process the message silently
        entry_data = process_with_groq(query, data["conversation"], data["categories"])
        
        # Add timestamp if not provided
        if "timestamp" not in entry_data or not entry_data["timestamp"]:
            entry_data["timestamp"] = datetime.now().isoformat()
        
        # Save the entry
        data["entries"].append(entry_data)
        
        # Add to categories if new
        cat = entry_data.get("category", "")
        if cat and cat not in data["categories"]:
            data["categories"].append(cat)
        
        # Update conversation memory
        data["conversation"].append({"role": "user", "content": query})
        data["conversation"] = data["conversation"][-20:]
        
        save_data(data)
        
        # Show quick confirmation (remove for complete silence)
        results = [
            InlineQueryResultArticle(
                id="logged",
                title="✅ Thought Logged",
                description=f"Saved to: {entry_data.get('category', 'Uncategorized')}",
                input_message_content=InputTextMessageContent(
                    f"✅ Logged to: {entry_data.get('category', 'Uncategorized')}\n\n{query[:100]}"
                )
            )
        ]
        await update.inline_query.answer(results, cache_time=0)
        return
    
    # If no query, show default menu
    results = [
        InlineQueryResultArticle(
            id="help",
            title="❓ Help",
            description="Show all commands",
            input_message_content=InputTextMessageContent("/help")
        ),
        InlineQueryResultArticle(
            id="recent",
            title="📝 Recent Entries",
            description="Show last 5 entries",
            input_message_content=InputTextMessageContent("/recent")
        ),
        InlineQueryResultArticle(
            id="stats",
            title="📊 Statistics",
            description="View bot stats",
            input_message_content=InputTextMessageContent("/stats")
        ),
        InlineQueryResultArticle(
            id="random",
            title="🎲 Random Quote",
            description="Get a random quote",
            input_message_content=InputTextMessageContent("/random")
        ),
        InlineQueryResultArticle(
            id="summary",
            title="📂 Full Summary",
            description="Show complete documentation summary",
            input_message_content=InputTextMessageContent("/summary")
        ),
    ]
    await update.inline_query.answer(results, cache_time=0)

# ── Command Handlers ──────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    bot_username = context.bot.username

    await update.message.reply_text(
        f"👋 PA Bot active (Silent Mode).\n\n"
        f"**I only respond to commands:**\n"
        f"/summary — overview of all entries\n"
        f"/quotes — your quotes vault\n"
        f"/categories — all categories\n"
        f"/search [term] — find entries\n"
        f"/clear_history — reset memory\n\n"
        f"**New Inline Commands (type @{bot_username} in ANY chat):**\n"
        f"• `recent` - Show last 5 entries\n"
        f"• `stats` - View bot statistics\n"
        f"• `random` - Get random quote\n"
        f"• `search KEYWORD` - Search entries\n"
        f"• `help` - Show all commands\n\n"
        f"**Quick logging:**\n"
        f"Just type @{bot_username} followed by your thought!\n\n"
        f"_I won't respond to regular messages - only commands._",
        parse_mode="Markdown"
    )

    # Schedule randomized check-ins
    interval = random.randint(6 * 3600, 18 * 3600)
    context.job_queue.run_repeating(
        random_checkin,
        interval=interval,
        first=interval,
        data={"chat_id": chat_id},
        name=f"checkin_{chat_id}",
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = context.bot.username
    help_text = f"""*🤖 PA Bot Commands*

*Regular Commands:*
/summary - Show all entries grouped by category
/quotes - Display all saved quotes
/categories - List all categories with counts
/search [term] - Search entries
/clear_history - Reset conversation memory

*Inline Commands (type @{bot_username} in any chat):*
• `recent` - Show last 5 entries
• `stats` - View bot statistics
• `random` - Get random quote
• `search KEYWORD` - Search entries
• `help` - Show this help

*Quick Logging:*
Just type @{bot_username} followed by your thought to silently log it!

*Examples:*
• `@{bot_username} I need to remember this idea`
• `@{bot_username} recent`
• `@{bot_username} search project`"""
    
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data["entries"]:
        await update.message.reply_text("No entries yet.")
        return
    
    lines = ["📝 *Recent Entries*\n"]
    for entry in data["entries"][-5:]:
        lines.append(f"*{entry.get('category', 'Uncategorized')}*")
        lines.append(f"{entry.get('text', '')[:100]}\n")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    total_entries = len(data["entries"])
    total_categories = len(data["categories"])
    
    category_counts = {}
    for entry in data["entries"]:
        cat = entry.get("category", "Uncategorized")
        category_counts[cat] = category_counts.get(cat, 0) + 1
    
    stats_text = f"📊 *Bot Statistics*\n\n"
    stats_text += f"Total entries: {total_entries}\n"
    stats_text += f"Total categories: {total_categories}\n\n"
    stats_text += "*Top categories:*\n"
    for cat, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
        stats_text += f"• {cat}: {count}\n"
    
    await update.message.reply_text(stats_text, parse_mode="Markdown")

async def cmd_random(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    quotes = [e for e in data["entries"] if "quote" in str(e.get("tags", [])).lower() or "🗣️" in e.get("category", "")]
    
    if quotes:
        random_quote = random.choice(quotes)
        await update.message.reply_text(
            f"🗣️ *Random Quote*\n\n_{random_quote.get('text', '')}_",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("No quotes logged yet. Add #quote to your entries!")

async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data["entries"]:
        await update.message.reply_text("No entries yet. Use inline mode (@{}) to log thoughts!".format(
            context.bot.username
        ))
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

async def cmd_quotes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    quotes = [e for e in data["entries"] if "quote" in str(e.get("tags", [])).lower() or "🗣️" in e.get("category", "")]

    if not quotes:
        await update.message.reply_text("No quotes logged yet.")
        return

    lines = ["🗣️ *Quotes Vault*\n"]
    for i, q in enumerate(quotes[-10:], 1):
        lines.append(f"{i}. _{q['text']}_")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

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

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).lower()
    if not query:
        await update.message.reply_text(
            "Usage: /search [term]\n\n"
            "Or use inline: @{} search term".format(context.bot.username)
        )
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

async def cmd_clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    data["conversation"] = []
    save_data(data)
    await update.message.reply_text("🧹 Conversation memory cleared. Entries are safe.")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("recent", cmd_recent))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("random", cmd_random))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("quotes", cmd_quotes))
    app.add_handler(CommandHandler("categories", cmd_categories))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("clear_history", cmd_clear_history))
    
    # Inline query handler
    app.add_handler(InlineQueryHandler(inline_query))

    logger.info("Bot started with INLINE mode - only responds to commands")
    app.run_polling()

if __name__ == "__main__":
    main()