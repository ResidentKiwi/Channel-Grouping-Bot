from telegram import Bot
import os

BOT = Bot(token=os.getenv("TELEGRAM_TOKEN"))

async def forward(src_chat_id, dst_chat_id, message_id):
    await BOT.forward_message(chat_id=dst_chat_id, from_chat_id=src_chat_id, message_id=message_id)
