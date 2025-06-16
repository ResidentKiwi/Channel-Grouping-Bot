import os, logging
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters
)
from handlers import (
    start, receive_group_name, new_post,
    handle_callback_query, receive_channel_invite
)

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

app = FastAPI()
bot_app = ApplicationBuilder().token(TOKEN).build()

# 1) Comandos e callbacks
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CallbackQueryHandler(handle_callback_query))

# 2) Mensagens em texto
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_invite))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_group_name))

# 3) Post de canal
bot_app.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.ALL, new_post))

@app.on_event("startup")
async def startup():
    logging.info("Initializing bot")
    await bot_app.initialize()
    telegram_bot = Bot(TOKEN)
    await telegram_bot.delete_webhook()
    await telegram_bot.set_webhook(WEBHOOK_URL)
    logging.info("Webhook configured")

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    logging.info("Webhook update received")
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "Bot ativo"}
