from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward

# Estado temporário de criação de grupo
user_states: dict[int, str] = {}

# ─── Início / Menu Principal ───
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
    user_states.pop(update.effective_user.id, None)

# ─── Ajuda ───
async def menu_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    texto = (
        "👋 *Como usar o CanalSyncBot:*\n\n"
        "• Gerencie grupos de canais com facilidade.\n"
        "• Dê nome ao grupo, convide canais, remova ou apague.\n"
        "• Ao convidar um canal, o dono decide se aceita!\n"
        "• Postagens serão replicadas automaticamente.\n\n"
        "Use os botões para navegar. Sempre há opção “↩️ Voltar” ou envie /start."
    )
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]
    await query.edit_message_text(texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# ─── Fluxo de criação de grupo via botão → estado aguardando nome ───
async def menu_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    user_id = update.effective_user.id
    user_states[user_id] = "awaiting_group_name"
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]
    await query.edit_message_text("📌 Envie agora o *nome do novo grupo*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def receive_group_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_states.get(user_id) != "awaiting_group_name":
        return
    name = update.message.text.strip()
    sess = Session()
    g = Group(name=name, owner_id=user_id)
    sess.add(g); sess.commit()
    await update.message.reply_text(f"✅ Grupo *{name}* criado com sucesso!", parse_mode="Markdown")
    user_states.pop(user_id, None)

# ─── Gerenciamento de grupos ───
async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    sess = Session()
    owns = sess.query(Group).filter_by(owner_id=update.effective_user.id).all()
    buttons = [[InlineKeyboardButton(g.name, callback_data=f"gerenciar_grupo_{g.id}")] for g in owns]
    buttons.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await query.edit_message_text("Seus grupos:", reply_markup=InlineKeyboardMarkup(buttons))

async def handle_grupo_actions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    gid = int(query.data.split("_")[-1])
    sess = Session(); g = sess.get(Group, gid)
    menu = [
        [InlineKeyboardButton("➕ Convidar canal", callback_data=f"convite_{gid}")],
        [InlineKeyboardButton("🗑️ Remover canal", callback_data=f"remove_{gid}")],
        [InlineKeyboardButton("🗑️❌ Apagar grupo", callback_data=f"deletegroup_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]
    ]
    await query.edit_message_text(f"Grupo: *{g.name}*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(menu))

async def prompt_delete_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    gid = int(query.data.split("_")[-1])
    kb = [
        [InlineKeyboardButton("✅ Sim, apagar", callback_data=f"delete_confirm_{gid}")],
        [InlineKeyboardButton("❌ Cancelar", callback_data=f"gerenciar_grupo_{gid}")]
    ]
    await query.edit_message_text("⚠️ Tem certeza que deseja *apagar esse grupo*? Esta ação é irreversível.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def delete_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    gid = int(query.data.split("_")[-1])
    sess = Session()
    group = sess.get(Group, gid)
    sess.query(GroupChannel).filter_by(group_id=gid).delete()
    sess.delete(group); sess.commit()
    await query.edit_message_text("✅ Grupo apagado com sucesso.")
    return await start(update, ctx)

# ─── Convite, remoção de canal ───
async def convite_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    gid = int(query.data.split("_")[-1])
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_grupo_{gid}")]]
    await query.edit_message_text(
        f"Para adicionar, envie: `/adicionar_canal @Canal` (isso adiciona ao grupo ID:{gid})",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
    )

async def remocao_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    gid = int(query.data.split("_")[-1])
    sess = Session(); g = sess.get(Group, gid)
    buttons = [[InlineKeyboardButton(chat.title or str(gc.channel_id),
                                     callback_data=f"remove_confirm_{gid}_{gc.channel_id}")]
               for gc in sess.query(GroupChannel).filter_by(group_id=gid, accepted=True)]
    buttons.append([InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_grupo_{gid}")])
    await query.edit_message_text(f"Escolha canal para remover do *{g.name}*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def remove_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    _, _, gid, cid = query.data.split("_")
    sess = Session(); gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if gc:
        sess.delete(gc); sess.commit()
    return await handle_grupo_actions(update, ctx)

# ─── Mostrar canais do usuário ───
async def menu_meus_canais(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    sess = Session()
    canais = sess.query(Channel).filter_by(owner_id=update.effective_user.id).all()
    lines = ["📋 *Seus canais:*"]
    for c in canais:
        chat = await ctx.bot.get_chat(c.id)
        uname = f"(@{chat.username})" if chat.username else ""
        lines.append(f"• *{chat.title}* {uname} — `ID:{c.id}`")
    lines.append("\nUse /start para voltar ao menu")
    await query.edit_message_text("\n".join(lines), parse_mode="Markdown")

# ─── Convite: aceitar/recusar ───
async def handle_convite(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    _, action, gid, cid = query.data.split("_")
    sess = Session(); gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if not gc: return await query.edit_message_text("❌ Convite inválido.")
    group, owner = gc.group, update.effective_user
    owner_canal = sess.query(Channel).filter_by(id=int(cid), owner_id=owner.id).first()
    inviter = sess.get(User, group.owner_id)
    if action == "aceitar":
        gc.accepted = True; sess.commit()
        await query.edit_message_text(f"✅ Canal entrou no *{group.name}*.", parse_mode="Markdown")
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
    for gc in sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True):
        for t in gc.group.channels:
            if t.accepted and t.channel_id != msg.chat.id:
                await forward(msg.chat.id, t.channel_id, msg.message_id)

# ─── Roteador de callbacks ───
async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data in ("start", "menu_ajuda"): return await globals()[data](update, ctx)
    if data in ("criar_grupo",): return await menu_criar_grupo(update, ctx)
    if data.startswith("menu_meus_grupos"): return await menu_meus_grupos(update, ctx)
    if data.startswith("gerenciar_grupo_"): return await handle_grupo_actions(update, ctx)
    if data.startswith("deletegroup_"): return await prompt_delete_group(update, ctx)
    if data.startswith("delete_confirm_"): return await delete_confirm(update, ctx)
    if data.startswith("convite_"): return await convite_canal(update, ctx)
    if data.startswith("remove_confirm_"): return await remove_confirm(update, ctx)
    if data.startswith(("aceitar_","recusar_")): return await handle_convite(update, ctx)
    if data == "menu_meus_canais": return await menu_meus_canais(update, ctx)
