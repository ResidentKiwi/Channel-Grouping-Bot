import os
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters
)
from handlers import (
    start, receive_group_name, new_post, handle_callback_query,
    receive_channel_invite
)

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

bot_app = ApplicationBuilder().token(TOKEN).build()

# Prioridade correta dos handlers de mensagens
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CallbackQueryHandler(handle_callback_query))

# Primeiro verifica se o usuário está convidando canal (estado específico)
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_invite))
# Depois verifica se está criando grupo
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_group_name))

# Handler para receber posts de canais
bot_app.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.ALL, new_post))

telegram_bot = Bot(TOKEN)
app = FastAPI()

@app.on_event("startup")
async def startup():
    await bot_app.initialize()
    await telegram_bot.delete_webhook()
    await telegram_bot.set_webhook(WEBHOOK_URL)

@app.post("/webhook")
async def webhook(request: Request):
    update = Update.de_json(await request.json(), bot_app.bot)
    await bot_app.process_update(update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "Bot ativo"}
