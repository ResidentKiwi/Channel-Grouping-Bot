import re, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
user_states: dict[int, dict] = {}

# — Menu inicial e navegação —
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    logger.info("start() called by %s", uid)
    sess = Session()
    user = sess.get(User, uid)
    if not user:
        user = User(id=uid, username=update.effective_user.username)
        sess.add(user); sess.commit()

    owns = sess.query(Group).filter_by(owner_id=uid).count() > 0
    participates = sess.query(GroupChannel).filter_by(channel_id=uid, accepted=True).count() > 0

    kb = []
    if owns: kb.append([InlineKeyboardButton("🛠 Meus grupos", callback_data="menu_meus_grupos")])
    kb += [
        [InlineKeyboardButton("➕ Criar grupo", callback_data="criar_grupo")],
        [InlineKeyboardButton("📋 Meus canais", callback_data="menu_meus_canais")],
    ]
    if participates:
        kb.append([InlineKeyboardButton("🚪 Sair de grupo", callback_data="menu_sair_grupo")])
    kb.append([InlineKeyboardButton("❓ Ajuda", callback_data="menu_ajuda")])

    markup = InlineKeyboardMarkup(kb)
    if update.message:
        await update.message.reply_text("Escolha uma opção:", reply_markup=markup)
    else:
        await update.callback_query.edit_message_text("Escolha uma opção:", reply_markup=markup)
    user_states.pop(uid, None)

async def menu_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    texto = (
        "👋 Use o bot para:\n"
        "• Criar e gerenciar grupos\n"
        "• Convidar canais por @username ou t.me/link\n"
        "• Sair de grupos que participa\n"
        "• Posts são replicados entre canais do grupo\n"
        "Use /start ou botões para navegar."
    )
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]
    await update.callback_query.edit_message_text(texto, reply_markup=InlineKeyboardMarkup(kb))

# — Fluxo criar grupo —
async def menu_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    user_states[uid] = {"state": "awaiting_group_name"}
    kb = [[InlineKeyboardButton("↩️ Cancelar", callback_data="start")]]
    await update.callback_query.edit_message_text("📌 Envie o *nome do grupo*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# — Listar e gerenciar grupos do dono —
async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    groups = sess.query(Group).filter_by(owner_id=uid).all()
    if not groups:
        return await update.callback_query.edit_message_text("🚫 Você não tem grupos.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [[InlineKeyboardButton(g.name, callback_data=f"gerenciar_{g.id}")] for g in groups]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await update.callback_query.edit_message_text("Seus grupos:", reply_markup=InlineKeyboardMarkup(kb))

async def handle_grupo_actions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[-1])
    sess = Session()
    g = sess.get(Group, gid)
    kb = [
        [InlineKeyboardButton("➕ Convidar canal", callback_data=f"convite_{gid}")],
        [InlineKeyboardButton("🗑 Remover canal", callback_data=f"remover_{gid}")],
        [InlineKeyboardButton("🗑❌ Apagar grupo", callback_data=f"delete_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")],
    ]
    await update.callback_query.edit_message_text(f"🎯 Gerenciando *{g.name}*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# — Convidar canal —
async def convite_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    gid = int(update.callback_query.data.split("_")[-1])
    user_states[uid] = {"state": "awaiting_channel_invite", "group_id": gid}
    kb = [[InlineKeyboardButton("↩️ Cancelar", callback_data=f"gerenciar_{gid}")]]
    await update.callback_query.edit_message_text("📥 Envie @canal ou link t.me:", reply_markup=InlineKeyboardMarkup(kb))

# — Sair de grupo —
async def menu_sair_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    grps = sess.query(GroupChannel).filter_by(channel_id=uid, accepted=True).all()
    if not grps:
        return await update.callback_query.edit_message_text("🚫 Você não participa de nenhum grupo.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [[InlineKeyboardButton(sess.get(Group, gc.group_id).name, callback_data=f"sair_{gc.group_id}")] for gc in grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await update.callback_query.edit_message_text("Selecione um grupo para sair:", reply_markup=InlineKeyboardMarkup(kb))

async def sair_grupo_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[-1])
    uid = update.callback_query.from_user.id
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=uid).first()
    if gc:
        sess.delete(gc); sess.commit()
    return await start(update, ctx)

# — Mensagens de texto: grupo ou convite —
async def handle_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    uid = update.effective_user.id
    st = user_states.get(uid)
    if not st: return
    sess = Session()
    text = update.message.text.strip()

    if st["state"] == "awaiting_group_name":
        user = sess.get(User, uid) or User(id=uid, username=update.effective_user.username)
        sess.add(user); sess.flush()
        sess.add(Group(name=text, owner_id=uid)); sess.commit()
        await update.message.reply_text(f"✅ Grupo *{text}* criado!", parse_mode="Markdown")
        user_states.pop(uid)
        return

    if st["state"] == "awaiting_channel_invite":
        match = re.search(r"@([\w_]+)|t\.me/([\w_]+)", text)
        if not match:
            return await update.message.reply_text("❌ Formato inválido. Envie @username ou t.me/username")
        username = match.group(1) or match.group(2)
        chat = await ctx.bot.get_chat(username)
        admins = await ctx.bot.get_chat_administrators(chat.id)
        chan_owner_id = admins[0].user.id
        gid = st["group_id"]
        chan = sess.get(Channel, chat.id) or Channel(id=chat.id, owner_id=chan_owner_id, username=username, title=chat.title)
        sess.add(chan); sess.flush()
        sess.add(GroupChannel(group_id=gid, channel_id=chat.id, accepted=None)); sess.commit()
        group = sess.get(Group, gid)
        canais = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
        lista = "\n".join(f"- {sess.get(Channel, c.channel_id).title}" for c in canais) or "(nenhum)"
        kb = [
            InlineKeyboardButton("✅ Aceitar", callback_data=f"aceitar_{gid}_{chat.id}"),
            InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_{gid}_{chat.id}")
        ]
        await ctx.bot.send_message(chat_id=chan_owner_id,
            text=f"📨 Convite para entrar no grupo *{group.name}*.\nCanais atuais:\n{lista}",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([kb]))
        await update.message.reply_text("✅ Convite enviado!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]]))
        user_states.pop(uid)
        return

# — Aceitar/Recusar convite —
async def handle_convite_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, action, gid, cid = update.callback_query.data.split("_")
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if not gc: return await update.callback_query.edit_message_text("❌ Convite inválido.")
    chan = sess.get(Channel, cid)
    group = sess.get(Group, gid)
    dono = sess.get(User, group.owner_id)
    if action == "aceitar":
        gc.accepted = True; sess.commit()
        await update.callback_query.edit_message_text("✅ Canal entrou no grupo!")
        await ctx.bot.send_message(dono.id, f"✅ Canal {chan.title} aceitou convite para *{group.name}*.")
    else:
        sess.delete(gc); sess.commit()
        await update.callback_query.edit_message_text("❌ Canal recusou o convite.")
        await ctx.bot.send_message(dono.id, f"❌ Canal {chan.title} recusou convite para *{group.name}*.")

# — Remoção de canal (dono do grupo) —
async def remocao_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[-1])
    sess = Session()
    canais = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    if not canais:
        return await update.callback_query.edit_message_text("🚫 Sem canais.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]]))
    kb = [[InlineKeyboardButton(sess.get(Channel, c.channel_id).title, callback_data=f"remover_confirm_{gid}_{c.channel_id}")] for c in canais]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="gerenciar_{}".format(gid))])
    await update.callback_query.edit_message_text("Selecione um canal para remover:", reply_markup=InlineKeyboardMarkup(kb))

async def remover_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, _, gid, cid = update.callback_query.data.split("_")
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if gc: sess.delete(gc); sess.commit()
    return await handle_grupo_actions(update, ctx)

# — Apagar grupo —
async def prompt_delete_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[-1])
    kb = [
        [InlineKeyboardButton("✅ Sim, apagar", callback_data=f"delete_confirm_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")]
    ]
    await update.callback_query.edit_message_text("⚠️ Apagar este grupo?", reply_markup=InlineKeyboardMarkup(kb))

async def delete_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[-1])
    sess = Session()
    sess.query(GroupChannel).filter_by(group_id=gid).delete()
    sess.query(Group).filter_by(id=gid).delete()
    sess.commit()
    return await menu_meus_grupos(update, ctx)

# — Meus canais —
async def menu_meus_canais(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    chans = sess.query(Channel).filter_by(owner_id=uid).all()
    if not chans:
        return await update.callback_query.edit_message_text("🚫 Você não tem canais.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    text = "📋 Meus canais:"
    for c in chans:
        text += f"\n• {c.title} @{c.username or 'ID:'+str(c.id)}"
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

# — Replicação de posts —
async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    logger.info("new_post from %s", msg.chat.id)
    sess = Session()
    targets = sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all()
    for gc in targets:
        others = sess.query(GroupChannel).filter_by(group_id=gc.group_id, accepted=True).all()
        for tc in others:
            if tc.channel_id != msg.chat.id:
                await forward(msg.chat.id, tc.channel_id, msg.message_id)

# — Callback router —
async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    logger.info("CB: %s", data)

    # Navegação simples
    if data == "start": return await start(update, ctx)
    if data == "menu_ajuda": return await menu_ajuda(update, ctx)
    if data == "criar_grupo": return await menu_criar_grupo(update, ctx)
    if data == "menu_meus_grupos": return await menu_meus_grupos(update, ctx)
    if data == "menu_meus_canais": return await menu_meus_canais(update, ctx)
    if data == "menu_sair_grupo": return await menu_sair_grupo(update, ctx)

    # Ações com prefixos
    if data.startswith("gerenciar_"): return await handle_grupo_actions(update, ctx)
    if data.startswith("convite_"): return await convite_canal(update, ctx)
    if data.startswith(("aceitar_", "recusar_")): return await handle_convite_response(update, ctx)
    if data.startswith("sair_"): return await sair_grupo_confirm(update, ctx)
    if data.startswith("remover_confirm_"): return await remover_confirm(update, ctx)
    if data.startswith("remover_"): return await remocao_canal(update, ctx)
    if data.startswith("delete_confirm_"): return await delete_confirm(update, ctx)
    if data.startswith("delete_"): return await prompt_delete_group(update, ctx)
