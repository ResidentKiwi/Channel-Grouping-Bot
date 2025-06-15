from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters
import os
from handlers import start, criar_grupo, adicionar_canal, new_post, handle_callback_query

TOKEN = os.getenv("TELEGRAM_TOKEN")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("criar_grupo", criar_grupo))
app.add_handler(CommandHandler("adicionar_canal", adicionar_canal))
app.add_handler(CallbackQueryHandler(handle_callback_query))
app.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.ALL, new_post))

app.run_polling()
