import os
import logging
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters
)
from handlers import (
    start, new_post, handle_callback_query,
    handle_text_message
)

# Ativa logs úteis
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Tokens e URL do webhook
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# Instância do aplicativo do Telegram
bot_app = ApplicationBuilder().token(TOKEN).build()

# Registro dos handlers
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CallbackQueryHandler(handle_callback_query))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
bot_app.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.ALL, new_post))

# FastAPI para receber webhook
telegram_bot = Bot(TOKEN)
app = FastAPI()

@app.on_event("startup")
async def startup():
    logger.info("Inicializando bot...")
    await bot_app.initialize()
    await telegram_bot.delete_webhook()
    await telegram_bot.set_webhook(WEBHOOK_URL)
    logger.info("Webhook configurado com sucesso!")

@app.post("/webhook")
async def webhook(request: Request):
    logger.info("Webhook update received")
    update = Update.de_json(await request.json(), bot_app.bot)
    await bot_app.process_update(update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "Bot ativo"}
