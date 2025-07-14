import os
from telegram import Bot, Message
from telegram.constants import ParseMode

BOT = Bot(token=os.getenv("TELEGRAM_TOKEN"))

async def forward(src_chat_id: int, dst_chat_id: int, message: Message):
    """
    Encaminha corretamente qualquer tipo de mensagem para outro canal, preservando o conteúdo.
    Se for um álbum (media_group_id), a lógica de agrupamento deve ocorrer fora desta função.
    """
    if message.text:
        await BOT.send_message(dst_chat_id, message.text, parse_mode=ParseMode.HTML)

    elif message.photo:
        await BOT.send_photo(
            dst_chat_id,
            photo=message.photo[-1].file_id,  # maior resolução
            caption=message.caption_html if message.caption else None,
            parse_mode=ParseMode.HTML
        )

    elif message.video:
        await BOT.send_video(
            dst_chat_id,
            video=message.video.file_id,
            caption=message.caption_html if message.caption else None,
            parse_mode=ParseMode.HTML
        )

    elif message.audio:
        await BOT.send_audio(
            dst_chat_id,
            audio=message.audio.file_id,
            caption=message.caption_html if message.caption else None,
            parse_mode=ParseMode.HTML
        )

    elif message.document:
        await BOT.send_document(
            dst_chat_id,
            document=message.document.file_id,
            caption=message.caption_html if message.caption else None,
            parse_mode=ParseMode.HTML
        )

    elif message.voice:
        await BOT.send_voice(
            dst_chat_id,
            voice=message.voice.file_id,
            caption=message.caption_html if message.caption else None,
            parse_mode=ParseMode.HTML
        )

    elif message.animation:
        await BOT.send_animation(
            dst_chat_id,
            animation=message.animation.file_id,
            caption=message.caption_html if message.caption else None,
            parse_mode=ParseMode.HTML
        )

    elif message.video_note:
        await BOT.send_video_note(
            dst_chat_id,
            video_note=message.video_note.file_id
        )

    else:
        # fallback para encaminhamento bruto (ex: polls, stickers, etc)
        await BOT.forward_message(dst_chat_id, src_chat_id, message.message_id)
