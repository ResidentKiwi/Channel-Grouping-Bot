from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward

# ─────── Início / Menu principal ───────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    sess = Session()
    owns = sess.query(Group).filter_by(owner_id=user.id).all()
    menu = []
    if owns:
        menu.append([InlineKeyboardButton("🛠 Gerenciar meus grupos", callback_data="menu_meus_grupos")])
    menu.append([InlineKeyboardButton("➕ Criar novo grupo", callback_data="criar_grupo")])
    menu.append([InlineKeyboardButton("📋 Meus canais", callback_data="menu_meus_canais")])
    await update.message.reply_text("Escolha uma opção:", reply_markup=InlineKeyboardMarkup(menu))

async def prompt_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    await query.edit_message_text("📌 Envie agora o comando no formato:\n/criar_grupo NomeDoGrupo")

async def criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return await update.message.reply_text("❌ Use: /criar_grupo NomeDoGrupo")
    name = parts[1]
    sess = Session()
    g = Group(name=name, owner_id=update.effective_user.id)
    sess.add(g); sess.commit()
    await update.message.reply_text(f"✅ Grupo '{name}' criado com sucesso!")

# ─────── Gerenciar Grupos ───────

async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sess = Session()
    owns = sess.query(Group).filter_by(owner_id=update.effective_user.id).all()
    if not owns:
        return await query.edit_message_text("Você não é dono de nenhum grupo.")
    buttons = [[InlineKeyboardButton(f"{g.name} (ID:{g.id})", callback_data=f"gerenciar_grupo_{g.id}")] for g in owns]
    await query.edit_message_text("Seus grupos:", reply_markup=InlineKeyboardMarkup(buttons))

async def handle_grupo_actions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    _, _, gid = query.data.split("_")
    sess = Session()
    g = sess.get(Group, int(gid))
    menu = [
        [InlineKeyboardButton("➕ Convidar canal", callback_data=f"convite_{gid}")],
        [InlineKeyboardButton("🗑️ Remover canal", callback_data=f"remove_{gid}")],
        [InlineKeyboardButton("🔙 Voltar", callback_data="menu_meus_grupos")]
    ]
    await query.edit_message_text(f"Grupo: {g.name} (ID:{g.id})", reply_markup=InlineKeyboardMarkup(menu))

async def convite_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    gid = query.data.split("_")[1]
    await query.edit_message_text(f"Envie agora:\n/adicionar_canal @Canal (isso adiciona ao grupo ID {gid})")

async def remocao_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    gid = int(query.data.split("_")[1])
    sess = Session()
    g = sess.get(Group, gid)
    buttons = []
    for gc in sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all():
        chat = await ctx.bot.get_chat(gc.channel_id)
        name = chat.title or f"ID:{gc.channel_id}"
        buttons.append([InlineKeyboardButton(f"❌ {name}", callback_data=f"remove_confirm_{gid}_{gc.channel_id}")])
    buttons.append([InlineKeyboardButton("🔙 Voltar", callback_data=f"gerenciar_grupo_{gid}")])
    await query.edit_message_text(f"Escolha canal para remover do grupo '{g.name}':", reply_markup=InlineKeyboardMarkup(buttons))

async def remove_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    _, _, gid, cid = query.data.split("_")
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if gc:
        sess.delete(gc); sess.commit()
    await query.edit_message_text("✅ Canal removido.")
    return await handle_grupo_actions(update, ctx)

# ─────── Meus Canais ───────

async def menu_meus_canais(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    sess = Session()
    canais = sess.query(Channel).filter_by(owner_id=update.effective_user.id).all()
    if not canais:
        return await query.edit_message_text("Você não possui canais.")
    lines = ["📋 Seus canais:"]
    for c in canais:
        chat = await ctx.bot.get_chat(c.id)
        uname = f" (@{chat.username})" if chat.username else ""
        lines.append(f"• {chat.title}{uname} — ID: {c.id}")
    await query.edit_message_text("\n".join(lines))

# ─────── Convites ───────

async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data.startswith("aceitar_") or data.startswith("recusar_"):
        return await handle_convite(update, ctx)
    if data == "criar_grupo":
        return await prompt_criar_grupo(update, ctx)
    if data == "menu_meus_grupos":
        return await menu_meus_grupos(update, ctx)
    if data.startswith("gerenciar_grupo_"):
        return await handle_grupo_actions(update, ctx)
    if data.startswith("convite_"):
        return await convite_canal(update, ctx)
    if data.startswith("remove_"):
        return await remocao_canal(update, ctx)
    if data.startswith("remove_confirm_"):
        return await remove_confirm(update, ctx)
    if data == "menu_meus_canais":
        return await menu_meus_canais(update, ctx)

async def handle_convite(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    data = query.data
    _, action, gid, cid = data.split("_")
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if not gc:
        return await query.edit_message_text("Convite inválido.")
    group = gc.group
    owner = update.effective_user
    owner_canal = sess.query(Channel).filter_by(id=int(cid), owner_id=owner.id).first()
    inviter = sess.get(User, group.owner_id)
    if action == "aceitar":
        gc.accepted = True; sess.commit()
        await query.edit_message_text(f"✅ Canal aceito no grupo '{group.name}'.")
        if inviter:
            await ctx.bot.send_message(inviter.id, f"✅ O canal {owner_canal.id} entrou no grupo '{group.name}'.")
    else:
        sess.delete(gc); sess.commit()
        await query.edit_message_text("❌ Convite recusado.")
        if inviter:
            await ctx.bot.send_message(inviter.id, f"❌ O canal {owner_canal.id} recusou o convite.")

# ─────── Postagens ───────

async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    sess = Session()
    groupch = sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all()
    for gc in groupch:
        for t in gc.group.channels:
            if t.accepted and t.channel_id != msg.chat.id:
                await forward(msg.chat.id, t.channel_id, msg.message_id)
