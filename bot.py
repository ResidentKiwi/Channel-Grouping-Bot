import os
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from handlers import (
    start, criar_grupo, adicionar_canal, new_post, handle_callback_query,
    meuscanais, meusgrupos, sair_grupo
)

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

bot_app = ApplicationBuilder().token(TOKEN).build()

# Comandos principais
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("criar_grupo", criar_grupo))
bot_app.add_handler(CommandHandler("adicionar_canal", adicionar_canal))

# Comandos novos
bot_app.add_handler(CommandHandler("meuscanais", meuscanais))
bot_app.add_handler(CommandHandler("meusgrupos", meusgrupos))
bot_app.add_handler(CommandHandler("sair_grupo", sair_grupo))

# Respostas a botões
bot_app.add_handler(CallbackQueryHandler(handle_callback_query))

# Postagens em canais
bot_app.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.ALL, new_post))

# FastAPI app para Webhook
telegram_bot = Bot(TOKEN)
app = FastAPI()

@app.on_event("startup")
async def startup():
    await bot_app.initialize()
    await telegram_bot.delete_webhook()
    await telegram_bot.set_webhook(url=WEBHOOK_URL)

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "Bot funcionando com Webhook!"}
