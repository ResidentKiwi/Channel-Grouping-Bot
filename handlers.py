from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sess = Session()
    user = sess.get(User, update.effective_user.id)
    if not user:
        user = User(id=update.effective_user.id, username=update.effective_user.username)
        sess.add(user); sess.commit()
    await update.message.reply_text(
        "👋 Olá! Bem-vindo ao CanalSyncBot.\n"
        "Use os comandos abaixo para gerenciar seus grupos de canais:\n"
        "/criar_grupo  /adicionar_canal  /meuscanais  /meusgrupos  /sair_grupo"
    )

async def criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().split(maxsplit=1)
    if len(text) < 2:
        return await update.message.reply_text("📌 Use: /criar_grupo NomeDoGrupo")
    name = text[1]
    sess = Session()
    group = Group(name=name, owner_id=update.effective_user.id)
    sess.add(group); sess.commit()
    await update.message.reply_text(f"✅ Grupo '{name}' criado com sucesso!")

async def adicionar_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) != 1:
        return await update.message.reply_text("📌 Use: /adicionar_canal @Canal")
    username = ctx.args[0]
    sess = Session()
    user = update.effective_user
    group = sess.query(Group).filter_by(owner_id=user.id).order_by(Group.id.desc()).first()
    if not group:
        return await update.message.reply_text("❌ Você ainda não criou nenhum grupo. Use /criar_grupo primeiro.")
    try:
        channel_chat = await ctx.bot.get_chat(username)
    except:
        return await update.message.reply_text("❌ Canal inválido ou sem acesso (o bot precisa ser admin).")
    channel = sess.get(Channel, channel_chat.id)
    if not channel:
        channel = Channel(id=channel_chat.id, owner_id=None)
        sess.add(channel); sess.commit()
    gc = GroupChannel(group_id=group.id, channel_id=channel.id, accepted=False)
    sess.add(gc); sess.commit()
    admins = await ctx.bot.get_chat_administrators(channel_chat.id)
    owner = next((a.user for a in admins if a.status == "creator"), None)
    if owner:
        channel.owner_id = owner.id
        sess.commit()
        if owner.id == user.id:
            gc.accepted = True; sess.commit()
            await update.message.reply_text(f"✅ Seu canal {username} foi adicionado automaticamente ao grupo '{group.name}'.")
        else:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Aceitar", callback_data=f"aceitar_{group.id}_{channel.id}"),
                InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_{group.id}_{channel.id}")
            ]])
            await ctx.bot.send_message(
                chat_id=owner.id,
                text=f"🔔 Canal {username} solicitado para entrar no grupo '{group.name}'.",
                reply_markup=keyboard
            )
            await update.message.reply_text(f"✅ Solicitação enviada ao dono do canal {username}.")
    else:
        await update.message.reply_text("⚠️ Não foi possível encontrar dono do canal.")

async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    sess = Session()
    _, gid, cid = data.split("_")
    gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if not gc:
        return await query.edit_message_text("❌ Convite inválido ou expirado.")
    group = gc.group
    channel = gc.channel
    inviter = sess.get(User, group.owner_id)
    if data.startswith("aceitar_"):
        gc.accepted = True; sess.commit()
        await query.edit_message_text(f"✅ Canal @{channel.id if channel.id else 'sem username'} adicionado ao grupo '{group.name}'.")
        if inviter:
            await ctx.bot.send_message(
                chat_id=inviter.id,
                text=f"✅ O canal @{channel.id if channel.id else channel.id} foi aceito e entrou no grupo '{group.name}'."
            )
    else:
        sess.delete(gc); sess.commit()
        await query.edit_message_text("❌ Solicitação recusada.")
        if inviter:
            await ctx.bot.send_message(
                chat_id=inviter.id,
                text=f"❌ O canal @{channel.id} recusou entrar no grupo '{group.name}'."
            )

async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    sess = Session()
    groupch = sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all()
    for gc in groupch:
        for target in gc.group.channels:
            if target.accepted and target.channel_id != msg.chat.id:
                await forward(msg.chat.id, target.channel_id, msg.message_id)

async def meuscanais(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sess = Session()
    canais = sess.query(Channel).filter_by(owner_id=update.effective_user.id).all()
    if not canais:
        return await update.message.reply_text("🚫 Você não administra nenhum canal.")
    lines = ["📋 Seus canais:"]
    for c in canais:
        # tentar nome público
        chat = await ctx.bot.get_chat(c.id)
        uname = f"(@{chat.username})" if chat.username else ""
        lines.append(f"• {chat.title} {uname} — ID: {c.id}")
    await update.message.reply_text("\n".join(lines))

async def meusgrupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sess = Session()
    canais = sess.query(Channel).filter_by(owner_id=update.effective_user.id).all()
    lines = ["📋 Seus grupos:"]
    for c in canais:
        for gc in sess.query(GroupChannel).filter_by(channel_id=c.id, accepted=True).all():
            grp = gc.group
            lines.append(f"• {grp.name} (ID:{grp.id}) — canal: {c.id}")
    await update.message.reply_text("\n".join(lines) if len(lines)>1 else "🚫 Você não está em nenhum grupo.")

async def sair_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sess = Session()
    linhas = ["🔻 Cancelar participação de canal em grupo:"]
    for gc in sess.query(GroupChannel).filter_by(accepted=True).all():
        ch = gc.channel
        grp = gc.group
        # filtrar canais que o usuário é dono
        if ch.owner_id == update.effective_user.id:
            linhas.append(f"{grp.id} — {grp.name} (canal {ch.id})")
    linhas.append("\nDigite `/sair_grupo <ID_DO_GRUPO>` para sair.")
    await update.message.reply_text("\n".join(linhas))

async def sair_grupo_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text("❌ Use: /sair_grupo <ID_DO_GRUPO>")
    gid = int(ctx.args[0])
    sess = Session()
    # encontrar registro
    gc = sess.query(GroupChannel).filter_by(group_id=gid).join(Channel).filter(Channel.owner_id==update.effective_user.id).first()
    if not gc:
        return await update.message.reply_text("❌ Grupo não encontrado ou canal não autorizado.")
    sess.delete(gc); sess.commit()
    await update.message.reply_text(f"✅ Canal removido do grupo '{gc.group.name}'.")
