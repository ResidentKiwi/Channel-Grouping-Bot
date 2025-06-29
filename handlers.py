# handlers.py
import re, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest, Forbidden
from db import Session, User, Channel, Group, GroupChannel
from queue_worker import forward

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
user_states: dict[int, dict] = {}

def safe_edit(q, text, markup=None):
    try:
        return q.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        logger.error("safe_edit error: %s", e)

# 1️⃣ Autenticar canal ao postar (bot deve ser admin no canal)
async def channel_authenticate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg or msg.chat.type != "channel":
        return
    sess = Session()
    try:
        admins = await ctx.bot.get_chat_administrators(msg.chat.id)
        creator = next(a.user for a in admins if a.status == "creator" and not a.user.is_bot)
    except Exception as e:
        logger.error("Erro ao obter admins do canal %s: %s", msg.chat.id, e)
        return
    sess.merge(User(id=creator.id, username=creator.username or ""))
    ch = sess.get(Channel, msg.chat.id)
    if not ch:
        sess.add(Channel(id=msg.chat.id, owner_id=creator.id,
                         username=msg.chat.username or "", title=msg.chat.title or "",
                         authenticated=True))
    else:
        ch.owner_id = creator.id
        ch.authenticated = True
    sess.commit()
    logger.info("✅ Canal autenticado: %s (%s)", msg.chat.title, msg.chat.id)

# 2️⃣ Comando /start ou botão "start"
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    sess = Session()
    sess.merge(User(id=uid, username=update.effective_user.username or ""))
    sess.commit()
    owns = sess.query(Group).filter_by(owner_id=uid).count() > 0
    participates = sess.query(GroupChannel).filter_by(channel_id=uid, accepted=True).count() > 0

    buttons = []
    if owns:
        buttons.append([InlineKeyboardButton("🛠 Meus grupos", callback_data="menu_meus_grupos")])
    buttons += [
        [InlineKeyboardButton("➕ Criar grupo", callback_data="criar_grupo")],
        [InlineKeyboardButton("📋 Meus canais", callback_data="menu_meus_canais")],
        [InlineKeyboardButton("🌐 Explorar grupos", callback_data="explorar_grupos")]
    ]
    if participates:
        buttons.append([InlineKeyboardButton("🚪 Sair de grupo", callback_data="menu_sair_grupo")])
    buttons.append([InlineKeyboardButton("❓ Ajuda", callback_data="menu_ajuda")])

    text = "Escolha uma opção:"
    markup = InlineKeyboardMarkup(buttons)
    if update.message:
        await update.message.reply_text(text, reply_markup=markup)
    else:
        await update.callback_query.answer()
        await safe_edit(update.callback_query, text, markup)
    user_states.pop(uid, None)

# 3️⃣ Menu de ajuda
async def menu_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    txt = (
        "👋 *Ajuda do Bot de Grupos de Canais*\n\n"
        "• /start ou ❓ Ajuda: este menu\n"
        "• Criar um grupo para adicionar canais\n"
        "• Convidar canais via @username ou link\n"
        "• Explorar e solicitar entrada em grupos públicos\n"
        "• Remover canais ou apagar grupos\n"
        "• Sair de grupo (se seu canal participa)\n"
        "• Replicação automática de posts entre canais do grupo\n\n"
        "➡️ Qualquer dúvida, envie /start"
    )
    await safe_edit(update.callback_query, txt, InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

# 4️⃣ Opção apagar grupo
async def menu_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    user_states[uid] = {"state": "awaiting_group_name"}
    await safe_edit(update.callback_query, "📌 Digite o nome do novo grupo:", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Cancelar", callback_data="start")]]))

# 5️⃣ Processar texto (criação ou convite)
async def handle_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    state = user_states.get(uid)
    sess = Session()

    # Criar grupo
    if state and state.get("state") == "awaiting_group_name":
        name = update.message.text.strip()
        sess.add(Group(name=name, owner_id=uid))
        sess.commit()
        await update.message.reply_text(f"✅ Grupo *{name}* criado!", parse_mode="Markdown")
        user_states.pop(uid)

    # Enviar convite para canal
    elif state and state.get("state") == "awaiting_channel_invite":
        text = update.message.text.strip()
        match = re.search(r"@([\w\d_]+)", text) or re.search(r"t\.me/([\w\d_]+)", text)
        if not match:
            return await update.message.reply_text("❌ Envie um @username ou link t.me válido.")
        username = match.group(1)

        try:
            chat = await ctx.bot.get_chat(username)
            if chat.type != "channel":
                return await update.message.reply_text("❌ Este usuário não é um canal.")
        except Forbidden:
            return await update.message.reply_text("❌ Bot não é admin no canal ou não tem permissão.")
        except BadRequest as e:
            return await update.message.reply_text(f"❌ Canal não encontrado: {e}")
        except Exception as e:
            return await update.message.reply_text(f"❌ Erro inesperado: {e}")

        # Obter dono/criador do canal (opcional)
        owner = None
        try:
            admins = await ctx.bot.get_chat_administrators(chat.id)
            owner = next((a.user for a in admins if a.status == "creator"), None)
        except:
            owner = None

        sess.merge(Channel(id=chat.id, owner_id=owner.id if owner else None,
                           username=username, title=chat.title or username,
                           authenticated=False))
        gid = state["group_id"]
        sess.add(GroupChannel(group_id=gid, channel_id=chat.id, inviter_id=uid, accepted=None))
        sess.commit()

        await update.message.reply_text(
            "✅ Convite enviado ao canal. Agora ele precisa aceitar!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]])
        )
        user_states.pop(uid)

# 6️⃣ Menu Meus Canais
async def menu_meus_canais(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    chans = sess.query(Channel).filter_by(owner_id=uid).all()
    if not chans:
        return await safe_edit(update.callback_query,
                               "🚫 Você não tem canais autenticados.",
                               InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    text = "📋 *Seus canais:*"
    for c in chans:
        link = f"https://t.me/{c.username}" if c.username else str(c.id)
        text += f"\n• {c.title} — {link}"
    await safe_edit(update.callback_query, text, InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

# 7️⃣ Menu Meus Grupos (proprietário)
async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    grps = sess.query(Group).filter_by(owner_id=uid).all()
    if not grps:
        return await safe_edit(update.callback_query,
                               "🚫 Você ainda não criou nenhum grupo.",
                               InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [[InlineKeyboardButton(g.name, callback_data=f"gerenciar_{g.id}")] for g in grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "📂 Seus grupos:", InlineKeyboardMarkup(kb))

# 8️⃣ Menu Gerenciar Grupo
async def gerenciar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_", 1)[1])
    sess = Session(); g = sess.get(Group, gid)
    parts = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()

    text = f"🎯 *{g.name}*\n\n📢 Canais participantes:"
    if not parts:
        text += "\n_Nenhum canal no grupo._"
    else:
        for gc in parts:
            ch = sess.get(Channel, gc.channel_id)
            link = f"https://t.me/{ch.username}" if ch.username else str(ch.id)
            text += f"\n• [{ch.title}]({link})\n  ID: `{ch.id}`"

    kb = [
        [InlineKeyboardButton("➕ Convidar canal", callback_data=f"convite_{gid}")],
        [InlineKeyboardButton("🗑 Remover canal", callback_data=f"remover_{gid}")],
        [InlineKeyboardButton("🗑❌ Apagar grupo", callback_data=f"delete_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]
    ]
    await safe_edit(update.callback_query, text, InlineKeyboardMarkup(kb))

# 9️⃣ Convidar canal (modo texto)
async def convite_manual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_", 1)[1])
    user_states[update.callback_query.from_user.id] = {"state": "awaiting_channel_invite", "group_id": gid}
    await safe_edit(update.callback_query,
                    "📥 Envie @username ou link do canal:",
                    InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")]]))

# 🔟 Aceitar/Recusar Convite Interno
async def handle_convite_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    parts = update.callback_query.data.split("_")
    # convite_123 => chama convite_manual
    if parts[0] == "convite" and len(parts) == 2:
        return await convite_manual(update, ctx)

    # aceitar_12345_6789 ou recusar_...
    _, action, gid, cid = parts
    gid, cid = int(gid), int(cid)
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=cid).first()
    if not gc:
        return await safe_edit(update.callback_query, "❌ Convite inválido.")
    ch = sess.get(Channel, cid); g = sess.get(Group, gid)

    if action == "aceitar":
        gc.accepted = True; sess.commit()
        await safe_edit(update.callback_query, f"✅ Canal *{ch.title}* aceitou convite.")
    else:
        sess.delete(gc); sess.commit()
        await safe_edit(update.callback_query, f"❌ Canal *{ch.title}* recusou convite.")

    # notificando o dono
    try:
        await ctx.bot.send_message(g.owner_id,
                                   f"📥 Canal *{ch.title}* "
                                   f"{'aceitou' if action=='aceitar' else 'recusou'} convite "
                                   f"no grupo *{g.name}*")
    except Exception:
        logger.error("Erro ao notificar dono do grupo")

# ✅ Explorar grupos públicos
async def explorar_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    sess = Session()
    grps = sess.query(Group).all()
    if not grps:
        return await safe_edit(update.callback_query,
                               "🌍 Ainda não há grupos públicos.",
                               InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [
        [InlineKeyboardButton(f"{g.name} ({sess.query(GroupChannel).filter_by(group_id=g.id,accepted=True).count()})",
                              callback_data=f"vergrp_{g.id}")]
        for g in grps
    ]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "🌐 Grupos públicos:", InlineKeyboardMarkup(kb))

# 1️⃣1️⃣ Visualizar grupo público
async def ver_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_", 1)[1])
    sess = Session(); g = sess.get(Group, gid)
    parts = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    text = f"📁 *{g.name}*\nCanais:"
    for gc in parts:
        ch = sess.get(Channel, gc.channel_id)
        subs = await ctx.bot.get_chat_members_count(ch.id) if ch.username else "?"
        link = f"https://t.me/{ch.username}" if ch.username else str(ch.id)
        text += f"\n- [{ch.title}]({link}) — {subs}"
    await safe_edit(update.callback_query, text, InlineKeyboardMarkup([
        [InlineKeyboardButton("📩 Solicitar entrada", callback_data=f"solicit_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="explorar_grupos")]
    ]))

# 1️⃣2️⃣ Solicitar entrada externa
async def solicitar_entrada(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    gid = int(update.callback_query.data.split("_", 1)[1])
    sess = Session()
    if sess.query(GroupChannel).filter_by(group_id=gid, channel_id=uid).first():
        return await safe_edit(update.callback_query, "🚫 Já está no grupo ou já solicitou.")
    ch = sess.get(Channel, uid)
    if not ch or not ch.authenticated:
        return await safe_edit(update.callback_query, "❌ Seu canal não está autenticado.")
    sess.add(GroupChannel(group_id=gid, channel_id=uid, inviter_id=uid, accepted=None))
    sess.commit(); g = sess.get(Group, gid)
    try:
        await ctx.bot.send_message(
            g.owner_id,
            f"📩 Canal *{ch.title}* solicita entrada no grupo *{g.name}*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Aceitar", callback_data=f"aceitar_ext_{gid}_{uid}"),
                 InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_ext_{gid}_{uid}")]
            ])
        )
    except:
        logger.error("Erro ao notificar dono do grupo")

    await safe_edit(update.callback_query, "✅ Solicitação enviada ao dono.")

# 1️⃣3️⃣ Responder solicitação externa
async def handle_ext_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    parts = update.callback_query.data.split("_")
    if len(parts) != 4:
        return await update.callback_query.answer("⚠️ Formato incorreto.", show_alert=True)
    _, action, gid, cid = parts
    gid, cid = int(gid), int(cid)
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=cid).first()
    ch = sess.get(Channel, cid); g = sess.get(Group, gid)

    if action == "aceitar_ext":
        gc.accepted = True; sess.commit()
        await safe_edit(update.callback_query, "✅ Canal aceito no grupo.")
        msg = f"✅ Seu canal foi aceito no grupo *{g.name}*"
    else:
        sess.delete(gc); sess.commit()
        await safe_edit(update.callback_query, "❌ Solicitação recusada.")
        msg = f"❌ Seu canal foi recusado no grupo *{g.name}*"
    try:
        await ctx.bot.send_message(cid, msg)
    except:
        logger.error("Não foi possível notificar o canal")

# 1️⃣4️⃣ Remover canal do grupo
async def remocao_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_", 1)[1])
    sess = Session()
    chans = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    if not chans:
        return await safe_edit(update.callback_query, "🚫 Sem canais para remover.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")]]))
    kb = [[InlineKeyboardButton(sess.get(Channel, gc.channel_id).title, callback_data=f"remover_confirm_{gid}_{gc.channel_id}")] for gc in chans]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")])
    await safe_edit(update.callback_query, "Escolha canal para remover:", InlineKeyboardMarkup(kb))

# 1️⃣5️⃣ Confirmar remoção
async def remover_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, _, gid, cid = update.callback_query.data.split("_")
    gid, cid = int(gid), int(cid)
    sess = Session()
    sess.query(GroupChannel).filter_by(group_id=gid, channel_id=cid).delete()
    sess.commit()
    return await gerenciar_grupo(update, ctx)

# 1️⃣6️⃣ Apagar grupo
async def prompt_delete_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_", 1)[1])
    await safe_edit(update.callback_query, "⚠️ Confirmar exclusão do grupo?", InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sim", callback_data=f"delete_confirm_{gid}")],
        [InlineKeyboardButton("❌ Não", callback_data=f"gerenciar_{gid}")]
    ]))

async def delete_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_", 1)[1])
    sess = Session()
    sess.query(GroupChannel).filter_by(group_id=gid).delete()
    sess.query(Group).filter_by(id=gid).delete()
    sess.commit()
    return await menu_meus_grupos(update, ctx)

# 1️⃣7️⃣ Sair de grupo (canal participante)
async def menu_sair_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    chans = sess.query(GroupChannel).filter_by(channel_id=uid, accepted=True).all()
    if not chans:
        return await safe_edit(update.callback_query, "🚫 Você não participa de nenhum grupo.",
                               InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [[InlineKeyboardButton(sess.get(Group, gc.group_id).name, callback_data=f"sair_confirm_{gc.group_id}_{uid}")] for gc in chans]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "Selecione o grupo para sair:", InlineKeyboardMarkup(kb))

async def sair_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, _, gid, cid = update.callback_query.data.split("_")
    sess = Session()
    sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).delete()
    sess.commit()
    return await menu_sair_grupo(update, ctx)

# 1️⃣8️⃣ Replicar posts entre canais do grupo
async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    sess = Session()
    gcs = sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all()
    for gc in gcs:
        for tgt in sess.get(Group, gc.group_id).channels:
            if tgt.accepted and tgt.channel_id != msg.chat.id:
                try:
                    await forward(msg.chat.id, tgt.channel_id, msg.message_id)
                except Exception as e:
                    logger.error("Erro replicando post para canal %s: %s", tgt.channel_id, e)

# Centralizador de callbacks
async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data or ""
    logger.info("Callback recebido: %s", data)

    simple = {
        "start": start, "menu_ajuda": menu_ajuda, "criar_grupo": menu_criar_grupo,
        "menu_meus_canais": menu_meus_canais, "menu_meus_grupos": menu_meus_grupos,
        "explorar_grupos": explorar_grupos, "menu_sair_grupo": menu_sair_grupo
    }
    if data in simple:
        return await simple[data](update, ctx)

    prefix = data.split("_", 1)[0]

    if prefix in ("convite", "aceitar", "recusar"):
        return await handle_convite_response(update, ctx)

    routes = {
        "gerenciar": gerenciar_grupo, "vergrp": ver_grupo, "solicit": solicitar_entrada,
        "aceitar_ext": handle_ext_response, "recusar_ext": handle_ext_response,
        "remover": remocao_canal, "remover_confirm": remover_confirm,
        "delete": prompt_delete_group, "delete_confirm": delete_confirm,
        "sair_confirm": sair_confirm
    }
    fn = routes.get(prefix)
    if fn:
        return await fn(update, ctx)

    await update.callback_query.answer("❌ Ação desconhecida.", show_alert=True)
