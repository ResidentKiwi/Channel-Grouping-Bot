from telegram import Bot
from telegram.request import AiohttpSession
import os

session = AiohttpSession()
BOT = Bot(os.getenv("TELEGRAM_TOKEN"), request=session)

async def forward(src_chat_id, dst_chat_id, message_id):
    await BOT.forward_message(chat_id=dst_chat_id, from_chat_id=src_chat_id, message_id=message_id)
