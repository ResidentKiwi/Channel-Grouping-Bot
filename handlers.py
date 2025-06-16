import re, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
user_states = {}

# 1️⃣ Autenticar canal quando ele posta como admin
async def channel_authenticate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg or not getattr(msg, "chat", None) or msg.chat.type != "channel":
        return
    sess = Session()
    ch = sess.get(Channel, msg.chat.id)
    admins = await ctx.bot.get_chat_administrators(msg.chat.id)
    creator = next((a.user for a in admins if a.status == "creator" and not a.user.is_bot), None)
    owner_id = creator.id if creator else None
    if not owner_id:
        return
    if not ch:
        ch = Channel(
            id=msg.chat.id,
            owner_id=owner_id,
            username=msg.chat.username,
            title=msg.chat.title,
            authenticated=True,
        )
        sess.add(ch)
    else:
        ch.authenticated = True
    sess.commit()
    logger.info("Canal autenticado: %s", msg.chat.title)

# 2️⃣ Menu de comandos
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    sess = Session()
    if not sess.get(User, uid):
        sess.add(User(id=uid, username=update.effective_user.username))
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
    markup = InlineKeyboardMarkup(kb)

    if update.message:
        await update.message.reply_text("Escolha uma opção:", reply_markup=markup)
    else:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Escolha uma opção:", reply_markup=markup)
    user_states.pop(uid, None)

# 3️⃣ Ajuda
async def menu_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    texto = (
        "👋 Use o bot para:\n"
        "• Criar grupos e canais\n"
        "• Convidar canais (bot deve ser admin)\n"
        "• Explorar grupos públicos\n"
        "• Solicitar entrada em grupo\n"
        "• Replicar posts entre canais\n"
        "• Sair de grupos\n"
        "Volte com /start"
    )
    kb = [[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]
    await update.callback_query.edit_message_text(texto, reply_markup=InlineKeyboardMarkup(kb))

# 4️⃣ Criar grupo (nome)
async def menu_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    user_states[uid] = {"state": "awaiting_group_name"}
    kb = [[InlineKeyboardButton("↩️ Cancelar", callback_data="start")]]
    await update.callback_query.edit_message_text("📌 Envie o nome do grupo:", reply_markup=InlineKeyboardMarkup(kb))

# 5️⃣ Meus grupos
async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    grps = sess.query(Group).filter_by(owner_id=uid).all()
    if not grps:
        return await update.callback_query.edit_message_text(
            "🚫 Você não tem grupos.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]])
        )
    kb = [[InlineKeyboardButton(g.name, callback_data=f"gerenciar_{g.id}")] for g in grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await update.callback_query.edit_message_text("Seus grupos:", reply_markup=InlineKeyboardMarkup(kb))

# 6️⃣ Explorar grupos públicos
async def explorar_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    sess = Session()
    grps = sess.query(Group).all()
    if not grps:
        return await update.callback_query.edit_message_text("Ainda não há grupos criados.")
    text = "🌐 Grupos disponíveis:\n"
    kb = []
    for g in grps:
        count = sess.query(GroupChannel).filter_by(group_id=g.id, accepted=True).count()
        kb.append([InlineKeyboardButton(f"{g.name} ({count} canais)", callback_data=f"vergrp_{g.id}")])
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

# 7️⃣ Ver grupo
async def ver_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    g = sess.get(Group, gid)
    participants = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    text = f"📁 *{g.name}*\nCanais:\n"
    kb = []
    for gc in participants:
        ch = sess.get(Channel, gc.channel_id)
        subs = await ctx.bot.get_chat_members_count(ch.id)
        link = f"https://t.me/{ch.username}" if ch.username else f"ID:{ch.id}"
        text += f"- [{ch.title}]({link}) — {subs} inscritos\n"
    text += "\nSolicite entrada:"
    kb = [[InlineKeyboardButton("📩 Solicitar entrada", callback_data=f"solicit_{gid}")],
          [InlineKeyboardButton("↩️ Voltar", callback_data="explorar_grupos")]]
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# 8️⃣ Solicitar entrada
async def solicitar_entrada(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    # verificar se já no grupo
    exists = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=uid).first()
    if exists:
        return await update.callback_query.edit_message_text("Você já participa ou solicitou.")
    g = sess.get(Group, gid)
    inviter_chan = sess.get(Channel, uid)
    if not inviter_chan or not inviter_chan.authenticated:
        return await update.callback_query.edit_message_text("Canal não autenticado.")
    dono = sess.get(User, g.owner_id)
    requester_name = inviter_chan.title
    link = f"https://t.me/{inviter_chan.username}" if inviter_chan.username else f"ID:{inviter_chan.id}"
    await ctx.bot.send_message(
        dono.id,
        f"📩 *Solicitação de entrada*\nCanal: {requester_name}\nGrupo: {g.name}\nLink: {link}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Aceitar", callback_data=f"aceitar_ext_{gid}_{uid}"),
            InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_ext_{gid}_{uid}")
        ]]),
        parse_mode="Markdown",
    )
    await update.callback_query.edit_message_text("✅ Solicitação enviada ao dono do grupo.")

# 9️⃣ Aceitar/recusar externo
async def handle_ext_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    action, gid, cid = update.callback_query.data.split("_")[1:]
    gid, cid = int(gid), int(cid)
    sess = Session()
    chan = sess.get(Channel, cid)
    user = sess.get(User, sess.get(Group, gid).owner_id)
    if action == "aceitar":
        sess.add(GroupChannel(group_id=gid, channel_id=cid, inviter_id=user.id, accepted=True))
        sess.commit()
        await ctx.bot.send_message(cid, f"✅ Você foi aceito no grupo `{sess.get(Group, gid).name}`.")
        await update.callback_query.edit_message_text("✅ Canal aceito.")
    else:
        await update.callback_query.edit_message_text("❌ Solicitação recusada.")

# 1️⃣0️⃣ Restantes: convites, resposta ao convite, remoção, replicação… (mantidos já)
# [Aqui você reutiliza os handlers já prontos de convite interno, remocao, sair, delete, new_post, handle_callback_query…]

# 🚀 Inclua no final:
async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    logger.info("CB: %s", data)
    if data == "start": return await start(update, ctx)
    if data == "menu_ajuda": return await menu_ajuda(update, ctx)
    if data == "criar_grupo": return await menu_criar_grupo(update, ctx)
    if data == "menu_meus_grupos": return await menu_meus_grupos(update, ctx)
    if data == "explorar_grupos": return await explorar_grupos(update, ctx)
    if data.startswith("vergrp_"): return await ver_grupo(update, ctx)
    if data.startswith("solicit_"): return await solicitar_entrada(update, ctx)
    if data.startswith(("aceitar_ext_", "recusar_ext_")): return await handle_ext_response(update, ctx)
    # + Reuse your existing callbacks for: convite, handle_convite_response, remocao, delete, sair, new_post
