from telegram import Bot
import os

BOT = Bot(os.getenv("TELEGRAM_TOKEN"))

def forward(src_chat_id, dst_chat_id, message_id):
    BOT.forward_message(chat_id=dst_chat_id, from_chat_id=src_chat_id, message_id=message_id)
