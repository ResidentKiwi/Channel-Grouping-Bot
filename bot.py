import os
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from handlers import start, criar_grupo, adicionar_canal, new_post, handle_callback_query

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Ex: https://channel-grouping-bot.onrender.com/webhook

bot_app = ApplicationBuilder().token(TOKEN).build()

bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("criar_grupo", criar_grupo))
bot_app.add_handler(CommandHandler("adicionar_canal", adicionar_canal))
bot_app.add_handler(CallbackQueryHandler(handle_callback_query))
bot_app.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.ALL, new_post))

# FastAPI app
app = FastAPI()
telegram_bot = Bot(TOKEN)

@app.on_event("startup")
async def setup_webhook():
    await telegram_bot.delete_webhook()
    await telegram_bot.set_webhook(url=WEBHOOK_URL)

@app.post("/webhook")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "Bot online!"}
