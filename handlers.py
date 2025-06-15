from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward  # Chamada direta, sem fila

def start(update: Update, ctx: CallbackContext):
    sess = Session()
    user = sess.get(User, update.effective_user.id)
    if not user:
        user = User(id=update.effective_user.id, username=update.effective_user.username)
        sess.add(user)
        sess.commit()
    update.message.reply_text("Bem-vindo! Use /criar_grupo para começar.")

def criar_grupo(update: Update, ctx: CallbackContext):
    text = update.message.text.split(maxsplit=1)
    if len(text) == 1:
        return update.message.reply_text("Use /criar_grupo NomeDoGrupo")
    name = text[1]
    sess = Session()
    group = Group(name=name, owner_id=update.effective_user.id)
    sess.add(group)
    sess.commit()
    update.message.reply_text(f"Grupo '{name}' criado. Use /adicionar_canal @canal para adicionar canais.")

def adicionar_canal(update: Update, ctx: CallbackContext):
    args = ctx.args
    if len(args) != 1:
        return update.message.reply_text("Use /adicionar_canal <@canal>")
    channel_username = args[0]
    sess = Session()
    user = update.effective_user
    group = sess.query(Group).filter_by(owner_id=user.id).order_by(Group.id.desc()).first()

    try:
        channel_chat = ctx.bot.get_chat(channel_username)
    except:
        return update.message.reply_text("Canal inválido ou sem permissão.")

    channel = sess.get(Channel, channel_chat.id)
    if not channel:
        channel = Channel(id=channel_chat.id, owner_id=None)
        sess.add(channel)
        sess.commit()

    gc = GroupChannel(group_id=group.id, channel_id=channel.id, accepted=False)
    sess.add(gc)
    sess.commit()

    admins = ctx.bot.get_chat_administrators(channel_chat.id)
    owner = next((a.user for a in admins if a.status == "creator"), None)

    if owner:
        channel.owner_id = owner.id
        sess.commit()

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Aceitar", callback_data=f"aceitar_{group.id}_{channel.id}"),
             InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_{group.id}_{channel.id}")]
        ])

        ctx.bot.send_message(
            chat_id=owner.id,
            text=f"O canal @{channel_username} foi convidado para o grupo '{group.name}'. Deseja aceitar?",
            reply_markup=keyboard
        )

    update.message.reply_text("Solicitação enviada ao dono do canal.")

def handle_callback_query(update: Update, ctx: CallbackContext):
    query = update.callback_query
    query.answer()

    data = query.data
    sess = Session()

    if data.startswith("aceitar_"):
        _, gid, cid = data.split("_")
        gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
        if gc:
            gc.accepted = True
            sess.commit()
            query.edit_message_text("✅ Convite aceito! Canal adicionado ao grupo.")
    elif data.startswith("recusar_"):
        _, gid, cid = data.split("_")
        gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
        if gc:
            sess.delete(gc)
            sess.commit()
            query.edit_message_text("❌ Convite recusado.")

def new_post(update: Update, ctx: CallbackContext):
    sess = Session()
    groupch = sess.query(GroupChannel).filter_by(channel_id=update.effective_chat.id, accepted=True).all()
    for gc in groupch:
        group = gc.group
        for target in group.channels:
            if target.accepted and target.channel_id != update.effective_chat.id:
                forward(update.message.chat.id, target.channel_id, update.message.message_id)
                
