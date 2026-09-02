#!/usr/bin/env python3
"""
Mitz Telegram Bot — issues API keys, credits, revoke.
"""

from __future__ import annotations

import logging
import os

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

BOT_TOKEN = os.environ["8972595355:AAHXY0PUSTkWUYr5-em8Ikc_uPuf6FBxa2A"]
API_BASE = os.environ.get("MITZ_API_BASE", "http://127.0.0.1:8080").rstrip("/")
ADMIN_TOKEN = os.environ["MITZ_ADMIN_TOKEN"]
OWNER_IDS = {
    int(x) for x in os.environ.get("MITZ_OWNER_IDS", "8632939616").split(",") if x.strip().isdigit()
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mitz-bot")

SESSION = requests.Session()
SESSION.headers.update({"X-Admin-Token": ADMIN_TOKEN, "Content-Type": "application/json"})

def admin_post(path: str, payload: dict) -> dict:
    r = SESSION.post(f"{API_BASE}{path}", json=payload, timeout=20)
    return r.json()

def admin_get(path: str) -> dict:
    r = SESSION.get(f"{API_BASE}{path}", timeout=20)
    return r.json()

def is_owner(uid: int) -> bool:
    return uid in OWNER_IDS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        f"Welcome <b>{user.first_name}</b>\n\n"
        "<b>Mitz MLBB Checker</b>\n\n"
        "Commands:\n"
        "/key — get or view your API key\n"
        "/balance — credits left\n"
        "/revoke — invalidate current key and issue new one\n"
        "/help — how to use the client\n"
    )
    if is_owner(user.id):
        text += "\nOwner:\n/addcredits &lt;tg_id&gt; &lt;amount&gt;\n/stats"
    await update.message.reply_text(text, parse_mode="HTML")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>Client usage</b>\n\n"
        "1. Run <code>mitz_tool.py</code>\n"
        "2. Paste Api Key from this bot\n"
        "3. Paste Api Base Url (your Railway URL)\n"
        "4. Menu unlocks — Single / Bulk Device ID checks\n\n"
        "Every single check = 1 credit\n"
        "Every bulk line = 1 credit\n\n"
        "Key cannot be changed from the tool. Only /revoke here issues a new one.",
        parse_mode="HTML",
    )

async def key_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    res = admin_post(
        "/api/v1/admin/create_key",
        {
            "telegram_id": user.id,
            "username": user.username or user.first_name or "",
            "credits": 0 if not is_owner(user.id) else 99999,
            "role": "owner" if is_owner(user.id) else "user",
        },
    )
    if not res.get("ok"):
        await update.message.reply_text(f"Error: {res.get('error', 'unknown')}")
        return
    key = res["api_key"]
    credits = res.get("credits", 0)
    msg = res.get("message", "")
    note = " (existing key)" if msg == "existing_key" else " (new key)"
    await update.message.reply_text(
        f"<b>Your API Key{note}</b>\n\n"
        f"<code>{key}</code>\n\n"
        f"Credits: <b>{credits}</b>\n"
        f"Role: {res.get('role', 'user')}\n\n"
        "Paste this into Mitz Tool. Do not share it.",
        parse_mode="HTML",
    )

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    admin_post(
        "/api/v1/admin/create_key",
        {
            "telegram_id": user.id,
            "username": user.username or "",
            "credits": 0,
            "role": "owner" if is_owner(user.id) else "user",
        },
    )
    res = admin_post(
        "/api/v1/admin/create_key",
        {"telegram_id": user.id, "username": user.username or "", "credits": 0},
    )
    await update.message.reply_text(
        f"Credits: <b>{res.get('credits', 0)}</b>\nKey: <code>{res.get('api_key', '—')}</code>",
        parse_mode="HTML",
    )

async def revoke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Yes, revoke and new key", callback_data="revoke_yes"),
                InlineKeyboardButton("Cancel", callback_data="revoke_no"),
            ]
        ]
    )
    await update.message.reply_text(
        "Revoke current API key and issue a brand new one?\n"
        "Old key stops working immediately.",
        reply_markup=kb,
    )

async def revoke_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "revoke_no":
        await q.edit_message_text("Cancelled.")
        return
    user = q.from_user
    admin_post("/api/v1/admin/revoke_key", {"telegram_id": user.id})
    res = admin_post(
        "/api/v1/admin/create_key",
        {
            "telegram_id": user.id,
            "username": user.username or user.first_name or "",
            "credits": 0 if not is_owner(user.id) else 99999,
            "role": "owner" if is_owner(user.id) else "user",
        },
    )
    await q.edit_message_text(
        f"<b>New API Key issued</b>\n\n<code>{res.get('api_key')}</code>\n\n"
        f"Credits: <b>{res.get('credits', 0)}</b>\nOld key is dead.",
        parse_mode="HTML",
    )

async def addcredits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: /addcredits <telegram_id> <amount>")
        return
    try:
        tg_id = int(args[0])
        amount = float(args[1])
    except ValueError:
        await update.message.reply_text("Bad numbers.")
        return
    res = admin_post("/api/v1/admin/add_credits", {"telegram_id": tg_id, "amount": amount})
    if res.get("ok"):
        await update.message.reply_text(f"OK. New balance: {res['credits']}")
    else:
        await update.message.reply_text(f"Fail: {res.get('error')}")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    res = admin_get("/api/v1/admin/stats")
    await update.message.reply_text(
        f"Active users: {res.get('active_users')}\n"
        f"Total credits: {res.get('total_credits')}\n"
        f"Usage events: {res.get('usage_events')}"
    )

def main():
    if not BOT_TOKEN or not ADMIN_TOKEN:
        raise SystemExit("Set MITZ_BOT_TOKEN and MITZ_ADMIN_TOKEN")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("key", key_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("revoke", revoke_cmd))
    app.add_handler(CommandHandler("addcredits", addcredits_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(revoke_cb, pattern="^revoke_"))
    log.info("Mitz bot starting…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
