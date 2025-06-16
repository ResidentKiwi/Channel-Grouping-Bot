import re, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
user_states: dict[int, dict] = {}

def safe_edit(q, text, markup=None, parse_mode="Markdown"):
    try:
        return q.edit_message_text(text, reply_markup=markup, parse_mode=parse_mode) if markup else q.edit_message_text(text, parse_mode=parse_mode)
    except BadRequest as e:
        if "not modified" in str(e).lower(): return
        logger.exception("Erro ao editar mensagem")
        return

# ✅ 1. Autenticar canal ao postar (bot como admin)
async def channel_authenticate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg or msg.chat.type != "channel": return
    sess = Session()
    admins = await ctx.bot.get_chat_administrators(msg.chat.id)
    creator = next((a.user for a in admins if a.status=="creator" and not a.user.is_bot), None)
    if not creator: return
    sess.merge(User(id=creator.id, username=creator.username))
    ch = sess.get(Channel, msg.chat.id)
    if not ch:
        ch = Channel(id=msg.chat.id, owner_id=creator.id,
                     username=msg.chat.username, title=msg.chat.title,
                     authenticated=True)
        sess.add(ch)
    else:
        ch.owner_id = creator.id
        ch.authenticated = True
    sess.commit()
    logger.info("🔐 Canal autenticado: %s", msg.chat.title)

# ✅ 2. Menu Principal
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    sess = Session()
    sess.merge(User(id=uid, username=update.effective_user.username))
    sess.commit()
    owns = sess.query(Group).filter_by(owner_id=uid).count() > 0
    participates = sess.query(GroupChannel).filter_by(channel_id=uid, accepted=True).count() > 0

    kb = []
    if owns: kb.append([InlineKeyboardButton("🛠 Meus grupos", callback_data="menu_meus_grupos")])
    kb += [
        [InlineKeyboardButton("➕ Criar grupo", callback_data="criar_grupo")],
        [InlineKeyboardButton("📋 Meus canais", callback_data="menu_meus_canais")],
        [InlineKeyboardButton("🌐 Explorar grupos", callback_data="explorar_grupos")],
    ]
    if participates: kb.append([InlineKeyboardButton("🚪 Sair de grupo", callback_data="menu_sair_grupo")])
    kb.append([InlineKeyboardButton("❓ Ajuda", callback_data="menu_ajuda")])

    if update.message:
        await update.message.reply_text("Escolha uma opção:", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.callback_query.answer()
        await safe_edit(update.callback_query, "Escolha uma opção:", InlineKeyboardMarkup(kb))
    user_states.pop(uid, None)

# ✅ 3. Guia de ajuda
async def menu_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    txt = (
        "👋 Funcionalidades:\n"
        "• Criar grupos\n"
        "• Convidar canais (bot admin)\n"
        "• Explorar grupos públicos\n"
        "• Solicitar entrada / sair\n"
        "• Replicar posts entre canais\n\n"
        "Use /start para voltar"
    )
    await safe_edit(update.callback_query, txt, InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

# ✅ 4. Iniciar criação de grupo
async def menu_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    user_states[uid] = {"state": "awaiting_group_name"}
    await safe_edit(update.callback_query, "📌 Envie o nome do novo grupo:", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Cancelar", callback_data="start")]]))

# ✅ 5. Processar texto (criar grupo ou convidar canal)
async def handle_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    uid = update.effective_user.id
    st = user_states.get(uid)
    sess = Session()

    # Criar grupo
    if st and st.get("state") == "awaiting_group_name":
        nome = update.message.text.strip()
        sess.add(Group(name=nome, owner_id=uid))
        sess.commit()
        await update.message.reply_text(f"✅ Grupo *{nome}* criado!", parse_mode="Markdown")
        user_states.pop(uid)

    # Convidar canal via username/link
    elif st and st.get("state") == "awaiting_channel_invite":
        m = re.search(r"@([\w\d_]+)", update.message.text) or re.search(r"t\.me/([\w\d_]+)", update.message.text)
        if not m:
            return await update.message.reply_text("❌ Envie @username ou t.me/...")
        uname = m.group(1)
        chat = await ctx.bot.get_chat(uname)
        admins = await ctx.bot.get_chat_administrators(chat.id)
        chan_owner = admins[0].user if admins else None
        sess.merge(Channel(id=chat.id, owner_id=chan_owner.id if chan_owner else None,
                           username=uname, title=chat.title, authenticated=False))
        sess.flush()
        stg = st["group_id"]
        sess.add(GroupChannel(group_id=stg, channel_id=chat.id, inviter_id=uid, accepted=None))
        sess.commit()
        await update.message.reply_text("✅ Convite enviado ao canal.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]]))
        user_states.pop(uid)

# ✅ 6. Mostrar canais do usuário
async def menu_meus_canais(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    chans = sess.query(Channel).filter_by(owner_id=uid).all()
    if not chans:
        return await safe_edit(update.callback_query, "🚫 Você não tem canais autenticados.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    text = "📋 *Seus canais:*"
    for c in chans:
        link = f"https://t.me/{c.username}" if c.username else str(c.id)
        text += f"\n• {c.title} — {link}"
    await safe_edit(update.callback_query, text, InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

# ✅ 7. Mostrar grupos do usuário
async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    grps = sess.query(Group).filter_by(owner_id=uid).all()
    if not grps:
        return await safe_edit(update.callback_query, "🚫 Você ainda não criou nenhum grupo.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [[InlineKeyboardButton(g.name, callback_data=f"gerenciar_{g.id}")] for g in grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "📂 Seus grupos:", InlineKeyboardMarkup(kb))

# ✅ 8. Gerenciar um grupo (menu interno)
async def gerenciar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    g = sess.get(Group, gid)
    kb = [
        [InlineKeyboardButton("➕ Convidar canal", callback_data=f"convite_{gid}")],
        [InlineKeyboardButton("🗑 Remover canal", callback_data=f"remover_{gid}")],
        [InlineKeyboardButton("🗑❌ Apagar grupo", callback_data=f"delete_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")],
    ]
    await safe_edit(update.callback_query, f"🎯 Grupo: *{g.name}*", InlineKeyboardMarkup(kb))

# ✅ 9. Convidar canal – definição e resposta
async def handle_convite_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data.split("_")
    prefix = data[0]
    if prefix == "convite":
        return await convite_manual(update, ctx)
    _, action, gid, cid = data
    gid, cid = int(gid), int(cid)
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=cid).first()
    ch = sess.get(Channel, cid); g = sess.get(Group, gid)
    if action == "aceitar":
        gc.accepted = True; sess.commit()
        await safe_edit(update.callback_query, f"✅ {ch.title} entrou no grupo.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]]))
        await ctx.bot.send_message(g.owner_id, f"✅ Canal {ch.title} aceitou convite.")
    else:
        sess.delete(gc); sess.commit()
        await safe_edit(update.callback_query, f"❌ {ch.title} recusou convite.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]]))
        await ctx.bot.send_message(g.owner_id, f"❌ Canal {ch.title} recusou convite.")

async def convite_manual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    gid = int(update.callback_query.data.split("_")[1])
    user_states[update.callback_query.from_user.id] = {"state":"awaiting_channel_invite","group_id":gid}
    await update.callback_query.answer()
    await safe_edit(update.callback_query, "📥 Envie @username ou link do canal:", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Cancelar", callback_data=f"gerenciar_{gid}")]]))

# ✅ 10. Explorar grupos públicos
async def explorar_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    sess = Session()
    grps = sess.query(Group).all()
    if not grps:
        return await safe_edit(update.callback_query, "Ainda sem grupos.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [[InlineKeyboardButton(f"{g.name} ({sess.query(GroupChannel).filter_by(group_id=g.id, accepted=True).count()} canais)", callback_data=f"vergrp_{g.id}")] for g in grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "🌐 Grupos públicos:", InlineKeyboardMarkup(kb))

# ✅ 11. Ver grupo público
async def ver_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    g = sess.get(Group, gid)
    participants = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    text = f"📁 *{g.name}*\nCanais:"
    for gc in participants:
        ch = sess.get(Channel, gc.channel_id)
        subs = await ctx.bot.get_chat_members_count(ch.id) if ch.username else "?"
        link = f"https://t.me/{ch.username}" if ch.username else str(ch.id)
        text += f"\n- [{ch.title}]({link}) — {subs}"
    await safe_edit(update.callback_query, text, InlineKeyboardMarkup([[InlineKeyboardButton("📩 Solicitar entrada", callback_data=f"solicit_{gid}")],
                                                                       [InlineKeyboardButton("↩️ Voltar", callback_data="explorar_grupos")]]))

# ✅ 12. Solicitar entrada externa
async def solicitar_entrada(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    if sess.query(GroupChannel).filter_by(group_id=gid, channel_id=uid).first():
        return await safe_edit(update.callback_query, "Você já está ou solicitou.")
    ch = sess.get(Channel, uid)
    if not ch or not ch.authenticated:
        return await safe_edit(update.callback_query, "❌ Canal não autenticado.")
    sess.add(GroupChannel(group_id=gid, channel_id=uid, inviter_id=uid, accepted=None))
    sess.commit()
    g = sess.get(Group, gid)
    await ctx.bot.send_message(g.owner_id, f"📩 Canal *{ch.title}* solicita entrada no grupo *{g.name}*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Aceitar", callback_data=f"aceitar_ext_{gid}_{uid}"),InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_ext_{gid}_{uid}")]]))
    await safe_edit(update.callback_query, "✅ Solicitação enviada.")

# ✅ 13. Responder entrada externa
async def handle_ext_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, action, gid, cid = update.callback_query.data.split("_")
    gid, cid = int(gid), int(cid)
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=cid).first()
    if action == "aceitar_ext":
        gc.accepted = True; sess.commit()
        await safe_edit(update.callback_query, "✅ Canal aceito.")
        await ctx.bot.send_message(cid, f"✅ Seu canal foi aceito no grupo *{sess.get(Group, gid).name}*")
    else:
        sess.delete(gc); sess.commit()
        await safe_edit(update.callback_query, "❌ Solicitação recusada.")

# ✅ 14. Remover canal existente
async def remocao_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    chans = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    if not chans:
        return await safe_edit(update.callback_query, "🚫 Nenhum canal para remover.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")]]))
    kb = [[InlineKeyboardButton(sess.get(Channel, gc.channel_id).title, callback_data=f"remover_confirm_{gid}_{gc.channel_id}")] for gc in chans]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")])
    await safe_edit(update.callback_query, "Escolha canal para remover:", InlineKeyboardMarkup(kb))

async def remover_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, _, gid, cid = update.callback_query.data.split("_")
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if gc:
        sess.delete(gc); sess.commit()
    return await gerenciar_grupo(update, ctx)

# ✅ 15. Apagar grupo
async def prompt_delete_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    await safe_edit(update.callback_query, "⚠️ Tem certeza?", InlineKeyboardMarkup([[InlineKeyboardButton("✅ Sim", callback_data=f"delete_confirm_{gid}"),InlineKeyboardButton("❌ Cancelar", callback_data=f"gerenciar_{gid}")]]))

async def delete_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    sess.query(GroupChannel).filter_by(group_id=gid).delete()
    sess.query(Group).filter_by(id=gid).delete()
    sess.commit()
    return await menu_meus_grupos(update, ctx)

# ✅ 16. Sair de grupo
async def menu_sair_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback.query.from_user.id
    sess = Session()
    chans = sess.query(GroupChannel).filter_by(channel_id=uid, accepted=True).all()
    if not chans:
        return await safe_edit(update.callback_query, "🚫 Não está em nenhum grupo.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [[InlineKeyboardButton(sess.get(Group, gc.group_id).name, callback_data=f"sair_confirm_{gc.group_id}_{uid}")] for gc in chans]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "Escolha grupo para sair:", InlineKeyboardMarkup(kb))

async def sair_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, _, gid, cid = update.callback_query.data.split("_")
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).first()
    if gc:
        sess.delete(gc); sess.commit()
    return await menu_sair_grupo(update, ctx)

# ✅ 17. Replicar postagem de canal
async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    sess = Session()
    for gc in sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all():
        grp = sess.get(Group, gc.group_id)
        for tgt in grp.channels:
            if tgt.accepted and tgt.channel_id != msg.chat.id:
                await forward(msg.chat.id, tgt.channel_id, msg.message_id)

# ✅ Centralizador de callbacks
async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data or ""
    logger.info("Callback:", data)
    mapping = {
        "start": start, "menu_ajuda": menu_ajuda, "criar_grupo": menu_criar_grupo,
        "menu_meus_canais": menu_meus_canais, "menu_meus_grupos": menu_meus_grupos,
        "explorar_grupos": explorar_grupos, "menu_sair_grupo": menu_sair_grupo
    }
    if data in mapping: return await mapping[data](update, ctx)
    prefix = data.split("_")[0]
    route_map = {
        "gerenciar": gerenciar_grupo, "convite": handle_convite_response,
        "aceitar": handle_convite_response, "recusar": handle_convite_response,
        "vergrp": ver_grupo, "solicit": solicitar_entrada,
        "aceitar_ext": handle_ext_response, "recusar_ext": handle_ext_response,
        "remover": remocao_canal, "remover_confirm": remover_confirm,
        "delete": prompt_delete_group, "delete_confirm": delete_confirm,
        "sair_confirm": sair_confirm
    }
    if prefix in route_map: return await route_map[prefix](update, ctx)
    await update.callback_query.answer("⚠️ Ação não reconhecida.")
