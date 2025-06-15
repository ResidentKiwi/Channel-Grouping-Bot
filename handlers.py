from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward  # Chamada direta, sem fila

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sess = Session()
    user = sess.get(User, update.effective_user.id)
    if not user:
        user = User(id=update.effective_user.id, username=update.effective_user.username)
        sess.add(user)
        sess.commit()
    await update.message.reply_text("Bem-vindo! Use /criar_grupo para começar.")

async def criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.split(maxsplit=1)
    if len(text) == 1:
        return await update.message.reply_text("Use /criar_grupo NomeDoGrupo")
    name = text[1]
    sess = Session()
    group = Group(name=name, owner_id=update.effective_user.id)
    sess.add(group)
    sess.commit()
    await update.message.reply_text(f"Grupo '{name}' criado. Use /adicionar_canal @canal para adicionar canais.")

async def adicionar_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) != 1:
        return await update.message.reply_text("Use /adicionar_canal <@canal>")
    channel_username = args[0]
    sess = Session()
    user = update.effective_user
    group = sess.query(Group).filter_by(owner_id=user.id).order_by(Group.id.desc()).first()

    try:
        channel_chat = await ctx.bot.get_chat(channel_username)
    except:
        return await update.message.reply_text("Canal inválido ou sem permissão.")

    channel = sess.get(Channel, channel_chat.id)
    if not channel:
        channel = Channel(id=channel_chat.id, owner_id=None)
        sess.add(channel)
        sess.commit()

    gc = GroupChannel(group_id=group.id, channel_id=channel.id, accepted=False)
    sess.add(gc)
    sess.commit()

    admins = await ctx.bot.get_chat_administrators(channel_chat.id)
    owner = next((a.user for a in admins if a.status == "creator"), None)

    if owner:
        channel.owner_id = owner.id
        sess.commit()

        # Se o dono for o mesmo que adicionou, aceita automaticamente
        if owner.id == user.id:
            gc.accepted = True
            sess.commit()
            await update.message.reply_text("Você é o dono do canal. Ele foi adicionado automaticamente ao grupo.")
        else:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Aceitar", callback_data=f"aceitar_{group.id}_{channel.id}"),
                 InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_{group.id}_{channel.id}")]
            ])

            await ctx.bot.send_message(
                chat_id=owner.id,
                text=f"O canal @{channel_username} foi convidado para o grupo '{group.name}'. Deseja aceitar?",
                reply_markup=keyboard
            )
            await update.message.reply_text("Solicitação enviada ao dono do canal.")
    else:
        await update.message.reply_text("Não foi possível identificar o dono do canal.")

async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    sess = Session()

    if data.startswith("aceitar_"):
        _, gid, cid = data.split("_")
        gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
        if gc:
            gc.accepted = True
            sess.commit()
            await query.edit_message_text("✅ Convite aceito! Canal adicionado ao grupo.")
    elif data.startswith("recusar_"):
        _, gid, cid = data.split("_")
        gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
        if gc:
            sess.delete(gc)
            sess.commit()
            await query.edit_message_text("❌ Convite recusado.")

async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if msg is None:
        return

    sess = Session()
    groupch = sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all()
    for gc in groupch:
        group = gc.group
        for target in group.channels:
            if target.accepted and target.channel_id != msg.chat.id:
                forward(msg.chat.id, target.channel_id, msg.message_id)
