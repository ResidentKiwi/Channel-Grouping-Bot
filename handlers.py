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
        if markup:
            return q.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
        return q.edit_message_text(text, parse_mode="Markdown")
    except BadRequest as e:
        if "not modified" in str(e).lower(): return
        logger.error("Falha ao editar mensagem: %s", e)
    except Exception as e:
        logger.error("Erro inesperado safe_edit: %s", e)

async def channel_authenticate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg or msg.chat.type != "channel": return
    sess = Session()
    try:
        admins = await ctx.bot.get_chat_administrators(msg.chat.id)
        creator = next((a.user for a in admins if a.status=="creator" and not a.user.is_bot), None)
    except Exception as e:
        logger.error("Erro get_chat_administrators: %s", e)
        return
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
    logger.info("✅ Canal autenticado: %s", msg.chat.title)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    sess = Session()
    sess.merge(User(id=uid, username=update.effective_user.username))
    sess.commit()

    owns = sess.query(Group).filter_by(owner_id=uid).count()>0
    participates = sess.query(GroupChannel).filter_by(channel_id=uid, accepted=True).count()>0

    kb = []
    if owns: kb.append([InlineKeyboardButton("🛠 Meus grupos", callback_data="menu_meus_grupos")])
    kb += [
        [InlineKeyboardButton("➕ Criar grupo", callback_data="criar_grupo")],
        [InlineKeyboardButton("📋 Meus canais", callback_data="menu_meus_canais")],
        [InlineKeyboardButton("🌐 Explorar grupos", callback_data="explorar_grupos")]
    ]
    if participates: kb.append([InlineKeyboardButton("🚪 Sair de grupo", callback_data="menu_sair_grupo")])
    kb.append([InlineKeyboardButton("❓ Ajuda", callback_data="menu_ajuda")])
    markup = InlineKeyboardMarkup(kb)

    txt = "Escolha uma opção:"
    if update.message:
        await update.message.reply_text(txt, reply_markup=markup)
    else:
        await update.callback_query.answer()
        await safe_edit(update.callback_query, txt, markup)
    user_states.pop(uid, None)

async def menu_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    txt = (
        "👋 Funcionalidades:\n"
        "- Criar grupos\n"
        "- Convidar canais (bot admin no canal)\n"
        "- Explorar grupos públicos\n"
        "- Solicitar entrada / sair de canais\n"
        "- Replicar posts entre canais\n\n"
        "Use /start para reiniciar."
    )
    await safe_edit(update.callback_query, txt,
        InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

async def menu_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    user_states[uid] = {"state":"awaiting_group_name"}
    await safe_edit(update.callback_query, "📌 Digite o nome do grupo:", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Cancelar", callback_data="start")]]))

async def handle_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    uid = update.effective_user.id
    state = user_states.get(uid); sess = Session()

    if state and state.get("state") == "awaiting_group_name":
        name = update.message.text.strip()
        sess.add(Group(name=name, owner_id=uid))
        sess.commit()
        await update.message.reply_text(f"✅ Grupo *{name}* criado!", parse_mode="Markdown")
        user_states.pop(uid)

    elif state and state.get("state") == "awaiting_channel_invite":
        m = re.search(r"@([\w\d_]+)", update.message.text) or re.search(r"t\.me/([\w\d_]+)", update.message.text)
        if not m:
            return await update.message.reply_text("❌ Envie @username ou t.me/... para o canal.")
        uname = m.group(1)

        try:
            chat = await ctx.bot.get_chat(uname)
            if chat.type != "channel":
                raise ValueError("Não é um canal.")
        except Exception as e:
            return await update.message.reply_text(f"❌ Não consegui encontrar canal {uname}: {e}")

        try:
            admins = await ctx.bot.get_chat_administrators(chat.id)
            owner = admins[0].user if admins else None
        except Exception:
            owner = None

        sess.merge(Channel(id=chat.id, owner_id=owner.id if owner else None,
                           username=uname, title=chat.title or uname, authenticated=False))
        gid = state["group_id"]
        sess.add(GroupChannel(group_id=gid, channel_id=chat.id, inviter_id=uid, accepted=None))
        sess.commit()
        await update.message.reply_text("✅ Convite encaminhado! O canal precisará aceitar.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
        user_states.pop(uid)

async def menu_meus_canais(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id; sess = Session()
    channels = sess.query(Channel).filter_by(owner_id=uid).all()
    if not channels:
        return await safe_edit(update.callback_query, "🚫 Você não tem canais autenticados.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    text = "📋 *Seus canais:*"
    for c in channels:
        link = f"https://t.me/{c.username}" if c.username else str(c.id)
        text += f"\n• {c.title} — {link}"
    await safe_edit(update.callback_query, text, InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id; sess = Session()
    grps = sess.query(Group).filter_by(owner_id=uid).all()
    if not grps:
        return await safe_edit(update.callback_query, "🚫 Você não criou grupos ainda.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [[InlineKeyboardButton(g.name, callback_data=f"gerenciar_{g.id}")] for g in grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "📂 Seus grupos:", InlineKeyboardMarkup(kb))

async def gerenciar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1]); sess = Session()
    g = sess.get(Group, gid)
    kb = [
        [InlineKeyboardButton("➕ Convidar canal", callback_data=f"convite_{gid}")],
        [InlineKeyboardButton("🗑 Remover canal", callback_data=f"remover_{gid}")],
        [InlineKeyboardButton("🗑❌ Apagar grupo", callback_data=f"delete_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]
    ]
    await safe_edit(update.callback_query, f"🎯 *{g.name}*", InlineKeyboardMarkup(kb))

async def convite_manual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.callback_query.from_user.id
    gid = int(update.callback_query.data.split("_")[1])
    user_states[uid] = {"state":"awaiting_channel_invite", "group_id":gid}
    await update.callback_query.answer()
    await safe_edit(update.callback_query, "📥 Envie @username ou link do canal:", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Cancelar", callback_data=f"gerenciar_{gid}")]]))

async def handle_convite_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data.split("_")
    _, action, gid, cid = data; gid, cid = int(gid), int(cid)
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=cid).first()
    if not gc:
        return await safe_edit(update.callback_query, "❌ Convite inválido.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="menu_meus_grupos")]]))

    ch = sess.get(Channel, cid); g = sess.get(Group, gid)

    if action == "aceitar":
        gc.accepted = True; sess.commit()
        await safe_edit(update.callback_query, f"✅ {ch.title} entrou no grupo.")
    else:
        sess.delete(gc); sess.commit()
        await safe_edit(update.callback_query, f"❌ {ch.title} recusou convite.")

    try:
        await ctx.bot.send_message(g.owner_id, f"📥 Canal *{ch.title}* {'aceitou' if action=='aceitar' else 'recusou'} convite no grupo *{g.name}*." )
    except (BadRequest, Forbidden) as e:
        logger.error("Falha ao notificar dono: %s", e)

async def explorar_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    sess = Session()
    grps = sess.query(Group).all()
    if not grps:
        return await safe_edit(update.callback_query, "🌍 Ainda sem grupos.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [[InlineKeyboardButton(f"{g.name} ({sess.query(GroupChannel).filter_by(group_id=g.id,accepted=True).count()} canais)", callback_data=f"vergrp_{g.id}")] for g in grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "🌐 Grupos públicos:", InlineKeyboardMarkup(kb))

async def ver_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1]); sess = Session()
    g = sess.get(Group, gid)
    parts = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    text = f"📁 *{g.name}*\nCanais:"
    for gc in parts:
        ch = sess.get(Channel, gc.channel_id)
        try:
            subs = await ctx.bot.get_chat_members_count(ch.id)
        except:
            subs = "?"
        link = f"https://t.me/{ch.username}" if ch.username else str(ch.id)
        text += f"\n- [{ch.title}]({link}) — {subs}"
    await safe_edit(update.callback_query, text, InlineKeyboardMarkup([
        [InlineKeyboardButton("📩 Solicitar entrada", callback_data=f"solicit_{gid}")],
        [InlineKeyboardButton("↩️ Voltar", callback_data="explorar_grupos")]
    ]))

async def solicitar_entrada(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session()
    if sess.query(GroupChannel).filter_by(group_id=gid,channel_id=uid).first():
        return await safe_edit(update.callback_query, "🚫 Você já está ou já solicitou.")
    ch = sess.get(Channel, uid)
    if not ch or not ch.authenticated:
        return await safe_edit(update.callback_query, "❌ Canal não autenticado.")
    sess.add(GroupChannel(group_id=gid,channel_id=uid,inviter_id=uid,accepted=None))
    sess.commit()
    g = sess.get(Group, gid)
    try:
        await ctx.bot.send_message(g.owner_id,
            f"📩 Canal *{ch.title}* solicita entrada no grupo *{g.name}*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Aceitar", callback_data=f"aceitar_ext_{gid}_{uid}"),
                                            InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_ext_{gid}_{uid}")]]))
    except (BadRequest, Forbidden):
        logger.error("Não foi possível notificar dono do grupo.")
    await safe_edit(update.callback_query, "✅ Solicitação enviada ao dono.")

async def handle_ext_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, action, gid, cid = update.callback_query.data.split("_")
    gid, cid = int(gid), int(cid)
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=cid).first()
    ch = sess.get(Channel, cid); g = sess.get(Group, gid)
    if action == "aceitar_ext":
        gc.accepted = True; sess.commit()
        await safe_edit(update.callback_query, "✅ Canal aceito.")
    else:
        sess.delete(gc); sess.commit()
        await safe_edit(update.callback_query, "❌ Solicitação recusada.")
    try:
        await ctx.bot.send_message(cid, f"{'✅ Aceito' if action=='aceitar_ext' else '❌ Recusado'} no grupo *{g.name}*")
    except:
        logger.error("Falha ao avisar o canal.")

async def remocao_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1]); sess = Session()
    chans = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    if not chans:
        return await safe_edit(update.callback_query, "🚫 Sem canais para remover.",
            InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")]]))
    kb = [[InlineKeyboardButton(sess.get(Channel, gc.channel_id).title, callback_data=f"remover_confirm_{gid}_{gc.channel_id}")] for gc in chans]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")])
    await safe_edit(update.callback_query, "Escolha canal para remover:", InlineKeyboardMarkup(kb))

async def remover_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, _, gid, cid = update.callback_query.data.split("_")
    sess = Session(); sess.query(GroupChannel).filter_by(group_id=int(gid),channel_id=int(cid)).delete()
    sess.commit()
    return await gerenciar_grupo(update, ctx)

async def prompt_delete_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    await safe_edit(update.callback_query, "⚠️ Confirmar exclusão do grupo?",
        InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Sim", callback_data=f"delete_confirm_{gid}")],
            [InlineKeyboardButton("❌ Não", callback_data=f"gerenciar_{gid}")]
        ]))

async def delete_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_")[1])
    sess = Session(); sess.query(GroupChannel).filter_by(group_id=gid).delete()
    sess.query(Group).filter_by(id=gid).delete()
    sess.commit()
    return await menu_meus_grupos(update, ctx)

async def menu_sair_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id; sess = Session()
    chans = sess.query(GroupChannel).filter_by(channel_id=uid, accepted=True).all()
    if not chans:
        return await safe_edit(update.callback_query, "🚫 Você não está em nenhum grupo.",
            InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
    kb = [[InlineKeyboardButton(sess.get(Group, gc.group_id).name, callback_data=f"sair_confirm_{gc.group_id}_{uid}")] for gc in chans]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "Escolha grupo para sair:", InlineKeyboardMarkup(kb))

async def sair_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, _, gid, cid = update.callback_query.data.split("_")
    sess = Session(); sess.query(GroupChannel).filter_by(group_id=int(gid),channel_id=int(cid)).delete()
    sess.commit()
    return await menu_sair_grupo(update, ctx)

async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg: return
    sess = Session()
    for gc in sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all():
        grp = sess.get(Group, gc.group_id)
        for tgt in grp.channels:
            if tgt.accepted and tgt.channel_id != msg.chat.id:
                try:
                    await forward(msg.chat.id, tgt.channel_id, msg.message_id)
                except Exception as e:
                    logger.error("Falha replicando %s → %s : %s", msg.chat.id, tgt.channel_id, e)

async def handle_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data or ""
    logger.info("Callback: %s", data)
    mapping = {
        "start": start, "menu_ajuda": menu_ajuda, "criar_grupo": menu_criar_grupo,
        "menu_meus_canais": menu_meus_canais, "menu_meus_grupos": menu_meus_grupos,
        "explorar_grupos": explorar_grupos, "menu_sair_grupo": menu_sair_grupo
    }
    if data in mapping:
        return await mapping[data](update, ctx)

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
    fn = route_map.get(prefix)
    if fn:
        return await fn(update, ctx)

    await update.callback_query.answer("❌ Ação não reconhecida.", show_alert=True)
