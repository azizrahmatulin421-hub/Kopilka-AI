import os
import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)

TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "kopilka.db")
GOAL_AMOUNT, GOAL_DATE, DEPOSIT_AMOUNT = range(3)

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            goal_name TEXT NOT NULL DEFAULT 'Моя цель',
            target INTEGER NOT NULL DEFAULT 0,
            saved INTEGER NOT NULL DEFAULT 0,
            deadline TEXT
        )""")

def get_user(uid):
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        if row is None:
            con.execute("INSERT INTO users(user_id) VALUES(?)", (uid,))
            row = con.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        return row

def money(n):
    return f"{int(n):,}".replace(",", " ") + " сум"

def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Создать / изменить цель", callback_data="goal")],
        [InlineKeyboardButton("➕ Добавить накопления", callback_data="deposit")],
        [InlineKeyboardButton("📊 Мой прогресс", callback_data="progress")],
        [InlineKeyboardButton("📅 План накоплений", callback_data="plan")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_user.id)
    await update.message.reply_text(
        "Привет! Я Kopilka AI — помогу копить деньги 💰\n\n"
        "Создай цель, добавляй отложенные деньги и следи за прогрессом.\n"
        "Валюта по умолчанию — узбекский сум.",
        reply_markup=menu()
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — главное меню\n"
        "/goal — создать цель\n"
        "/add — добавить накопления\n"
        "/progress — посмотреть прогресс\n"
        "/plan — план накоплений"
    )

async def show_progress(chat_id, uid, context):
    u = get_user(uid)
    if not u["target"]:
        await context.bot.send_message(chat_id, "Сначала создай цель: нажми «Создать / изменить цель».", reply_markup=menu())
        return
    left = max(0, u["target"] - u["saved"])
    pct = min(100, round(u["saved"] / u["target"] * 100))
    blocks = round(pct / 10)
    bar = "🟩" * blocks + "⬜" * (10 - blocks)
    msg = (f"🎯 Цель: {u['goal_name']}\n"
           f"💰 Накоплено: {money(u['saved'])}\n"
           f"🏁 Нужно: {money(u['target'])}\n"
           f"Осталось: {money(left)}\n\n{bar} {pct}%")
    if left == 0:
        msg += "\n\n🎉 Поздравляю! Цель достигнута!"
    await context.bot.send_message(chat_id, msg, reply_markup=menu())

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid, chat_id = q.from_user.id, q.message.chat_id
    if q.data == "goal":
        await q.message.reply_text("Напиши название своей цели (например, «Новый телефон»):")
        context.user_data["await_goal_name"] = True
    elif q.data == "deposit":
        await q.message.reply_text("Сколько сум добавляем к накоплениям? Напиши число:")
        context.user_data["await_deposit"] = True
    elif q.data == "progress":
        await show_progress(chat_id, uid, context)
    elif q.data == "plan":
        u = get_user(uid)
        if not u["target"]:
            await q.message.reply_text("Сначала создай цель.", reply_markup=menu())
        elif not u["deadline"]:
            await q.message.reply_text("У цели пока нет даты. Измени цель командой /goal, чтобы задать срок.", reply_markup=menu())
        else:
            days = max(1, (date.fromisoformat(u["deadline"]) - date.today()).days)
            left = max(0, u["target"] - u["saved"])
            daily = (left + days - 1) // days
            weekly = (left * 7 + days - 1) // days
            await q.message.reply_text(
                f"📅 План для цели «{u['goal_name']}»\n"
                f"До срока: {days} дн.\n"
                f"Осталось накопить: {money(left)}\n"
                f"В день: примерно {money(daily)}\n"
                f"В неделю: примерно {money(weekly)}",
                reply_markup=menu()
            )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, txt = update.effective_user.id, update.message.text.strip()
    if context.user_data.get("await_goal_name"):
        context.user_data.pop("await_goal_name", None)
        context.user_data["goal_name"] = txt[:60]
        await update.message.reply_text("Какую сумму хочешь накопить? Напиши число в сумах:")
        context.user_data["await_target"] = True
        return
    if context.user_data.get("await_target"):
        try:
            amount = int(Decimal(txt.replace(" ", "").replace(",", ".")))
            if amount <= 0: raise ValueError
        except Exception:
            await update.message.reply_text("Введи положительное число, например 500000.")
            return
        context.user_data.pop("await_target", None)
        context.user_data["target"] = amount
        await update.message.reply_text("К какой дате хочешь накопить? Формат: ГГГГ-ММ-ДД (например, 2027-03-01).")
        context.user_data["await_deadline"] = True
        return
    if context.user_data.get("await_deadline"):
        try:
            d = date.fromisoformat(txt)
            if d <= date.today(): raise ValueError
        except Exception:
            await update.message.reply_text("Дата должна быть в будущем и в формате ГГГГ-ММ-ДД.")
            return
        name = context.user_data.pop("goal_name")
        target = context.user_data.pop("target")
        context.user_data.pop("await_deadline", None)
        with db() as con:
            con.execute("""UPDATE users SET goal_name=?, target=?, deadline=? WHERE user_id=?""",
                        (name, target, d.isoformat(), uid))
        await update.message.reply_text(f"✅ Цель сохранена: {name}\nСумма: {money(target)}\nСрок: {d.strftime('%d.%m.%Y')}", reply_markup=menu())
        return
    if context.user_data.get("await_deposit"):
        try:
            amount = int(Decimal(txt.replace(" ", "").replace(",", ".")))
            if amount <= 0: raise ValueError
        except Exception:
            await update.message.reply_text("Введи положительное число, например 20000.")
            return
        context.user_data.pop("await_deposit", None)
        with db() as con:
            con.execute("UPDATE users SET saved=saved+? WHERE user_id=?", (amount, uid))
        await update.message.reply_text(f"✅ Добавлено: {money(amount)}")
        await show_progress(update.effective_chat.id, uid, context)
        return
    await update.message.reply_text("Выбери действие в меню или напиши /help.", reply_markup=menu())

async def goal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["await_goal_name"] = True
    await update.message.reply_text("Напиши название своей цели:")

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["await_deposit"] = True
    await update.message.reply_text("Сколько сум добавляем? Напиши число:")

async def progress_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_progress(update.effective_chat.id, update.effective_user.id, context)

async def plan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    if not u["target"] or not u["deadline"]:
        await update.message.reply_text("Сначала создай цель и укажи дату через /goal.", reply_markup=menu())
        return
    days = max(1, (date.fromisoformat(u["deadline"]) - date.today()).days)
    left = max(0, u["target"] - u["saved"])
    daily = (left + days - 1) // days
    await update.message.reply_text(
        f"До цели осталось {days} дней.\nОсталось: {money(left)}\nОткладывай примерно {money(daily)} в день.",
        reply_markup=menu()
    )

def main():
    if not TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN. Создай .env по примеру .env.example.")
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("goal", goal_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("progress", progress_cmd))
    app.add_handler(CommandHandler("plan", plan_cmd))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    print("Kopilka AI запущен. Остановить: Ctrl+C")
    app.run_polling()

if __name__ == "__main__":
    main()
