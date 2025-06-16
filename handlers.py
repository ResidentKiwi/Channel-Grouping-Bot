from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward

# ─── Menu principal refinado ───
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sess = Session()
    owns = sess.query(Group).filter_by(owner_id=update.effective_user.id).count() > 0
    menu = []
    if owns:
        menu.append([InlineKeyboardButton("🛠 Gerenciar meus grupos", callback_data="menu_meus_grupos")])
    menu.append([InlineKeyboardButton("➕ Criar novo grupo", callback_data="criar_grupo")])
    menu.append([InlineKeyboardButton("📋 Meus canais", callback_data="menu_meus_canais")])
    menu.append([InlineKeyboardButton("❓ Ajuda", callback_data="menu_ajuda")])
    await update.message.reply_text("Escolha uma opção:", reply_markup=InlineKeyboardMarkup(menu))

# ─── Menu de ajuda ───
async def menu_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    texto = (
        "👋 *Como usar o CanalSyncBot:*\n\n"
        "• Crie um grupo para compartilhar canais entre si.\n"
        "• Convidar canais permite ao dono aceitar ou recusar.\n"
        "• Qualquer canal aceito replicará as postagens automaticamente.\n"
        "• Use ‘Gerenciar meus grupos’ para convidar ou remover canais.\n"
        "• ‘Meus canais’ mostra seus canais vinculados.\n\n"
        "Navegue pelos botões acima. Em qualquer passo, use ↩️ Voltar ou /start."
    )
    await query.edit_message_text(texto, parse_mode="Markdown")
    # Botão voltar
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

# ─── Fluxo ‘Criar grupo’ ───
async def prompt_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    texto = "📌 Envie agora o comando no formato:\n`/criar_grupo NomeDoGrupo`"
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]
    await query.edit_message_text(texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return await update.message.reply_text("❌ Use: /criar_grupo NomeDoGrupo")
    name = parts[1]
    sess = Session()
    g = Group(name=name, owner_id=update.effective_user.id)
    sess.add(g); sess.commit()
    await update.message.reply_text(f"✅ Grupo *{name}* criado com sucesso!", parse_mode="Markdown")

# ─── Gerenciamento de grupos (dono) ───
async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    sess = Session()
    owns = sess.query(Group).filter_by(owner_id=update.effective_user.id).all()
    if not owns:
        return await query.edit_message_text("❌ Você não possui grupos.")
    buttons = [[InlineKeyboardButton(f"{g.name}", callback_data=f"gerenciar_grupo_{g.id}")] for g in owns]
    buttons.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await query.edit_message_text("Seus grupos:", reply_markup=InlineKeyboardMarkup(buttons))

async def handle_grupo_actions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    gid = int(query.data.split("_")[-1])
    sess = Session(); g = sess.get(Group, gid)
    menu = [
        [InlineKeyboardButton("➕ Convidar canal", callback_data=f"convite_{gid}")],
        [InlineKeyboardButton("🗑️ Remover canal", callback_data=f"remove_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]
    ]
    await query.edit_message_text(f"Grupo: *{g.name}*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(menu))

async def convite_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    gid = query.data.split("_")[1]
    texto = f"📌 Envie o comando:\n`/adicionar_canal @Canal` \n*para adicionar ao grupo ID {gid}*"
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_grupo_{gid}")]]
    await query.edit_message_text(texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def remocao_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    gid = int(query.data.split("_")[1])
    sess = Session(); g = sess.get(Group, gid)
    buttons = []
    for gc in sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all():
        chat = await ctx.bot.get_chat(gc.channel_id)
        label = chat.title or str(gc.channel_id)
        buttons.append([InlineKeyboardButton(f"❌ {label}", callback_data=f"remove_confirm_{gid}_{gc.channel_id}")])
    buttons.append([InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_grupo_{gid}")])
    await query.edit_message_text(f"Escolha canal para remover do *{g.name}*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def remove_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    _, _, gid, cid = query.data.split("_")
    sess = Session(); gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if gc:
        sess.delete(gc); sess.commit()
    # Volta ao menu do grupo
    return await handle_grupo_actions(update, ctx)

# ─── Vejo meus canais ───
async def menu_meus_canais(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    sess = Session()
    canais = sess.query(Channel).filter_by(owner_id=update.effective_user.id).all()
    if not canais:
        return await query.edit_message_text("❌ Você não possui canais.")
    lines = [f"📋 *Seus canais:*"]
    for c in canais:
        chat = await ctx.bot.get_chat(c.id)
        uname = f"(@{chat.username})" if chat.username else ""
        lines.append(f"• *{chat.title}* {uname} — `ID:{c.id}`")
    lines.append("\nUse /start para voltar ao menu principal.")
    await query.edit_message_text("\n".join(lines), parse_mode="Markdown")

# ─── Convites (aceitar/recusar) ───
async def handle_convite(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    _, action, gid, cid = query.data.split("_")
    sess = Session(); gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if not gc:
        return await query.edit_message_text("❌ Convite inválido ou expirado.")
    group = gc.group; owner = update.effective_user
    owner_canal = sess.query(Channel).filter_by(id=int(cid), owner_id=owner.id).first()
    inviter = sess.get(User, group.owner_id)
    if action == "aceitar":
        gc.accepted = True; sess.commit()
        await query.edit_message_text(f"✅ Você adicionou o canal ao *{group.name}*.", parse_mode="Markdown")
        if inviter:
            await ctx.bot.send_message(inviter.id, f"✅ Canal {owner_canal.id} entrou no grupo *{group.name}*.", parse_mode="Markdown")
    else:
        sess.delete(gc); sess.commit()
        await query.edit_message_text("❌ Convite recusado.")
        if inviter:
            await ctx.bot.send_message(inviter.id, f"❌ Canal {owner_canal.id} recusou o convite.", parse_mode="Markdown")

# ─── Replicação de posts ───
async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    sess = Session()
    for gc in sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all():
        for t in gc.group.channels:
            if t.accepted and t.channel_id != msg.chat.id:
                await forward(msg.chat.id, t.channel_id, msg.message_id)

# ─── Roteamento de callbacks ───
async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data.startswith(("aceitar_", "recusar_")): return await handle_convite(update, ctx)
    if data == "criar_grupo": return await prompt_criar_grupo(update, ctx)
    if data == "menu_ajuda": return await menu_ajuda(update, ctx)
    if data == "start": return await start(update, ctx)
    if data == "menu_meus_grupos": return await menu_meus_grupos(update, ctx)
    if data.startswith("gerenciar_grupo_"): return await handle_grupo_actions(update, ctx)
    if data.startswith("convite_"): return await convite_canal(update, ctx)
    if data.startswith("remove_"): return await remocao_canal(update, ctx)
    if data.startswith("remove_confirm_"): return await remove_confirm(update, ctx)
    if data == "menu_meus_canais": return await menu_meus_canais(update, ctx)
