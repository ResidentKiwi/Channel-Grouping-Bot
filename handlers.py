import re, logging, asyncio
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
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

# 1️⃣ Autenticar canal automaticamente
async def channel_authenticate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg or msg.chat.type != "channel":
        return
    sess = Session()
    try:
        admins = await ctx.bot.get_chat_administrators(msg.chat.id)
        creator = next((a.user for a in admins if a.status == "creator" and not a.user.is_bot), None)
        if not creator:
            logger.warning("Canal %s sem criador válido", msg.chat.id)
            return
    except Exception as e:
        logger.error("Erro ao obter admins do canal %s: %s", msg.chat.id, e)
        return

    sess.merge(User(id=creator.id, username=creator.username or ""))
    canal = sess.get(Channel, msg.chat.id)
    if not canal:
        canal = Channel(
            id=msg.chat.id,
            owner_id=creator.id,
            username=msg.chat.username or "",
            title=msg.chat.title or "",
            authenticated=True
        )
        sess.add(canal)
    else:
        canal.owner_id = creator.id
        canal.authenticated = True
    sess.commit()
    logger.info("✅ Canal autenticado: %s (%s)", msg.chat.title, msg.chat.id)

# 2️⃣ Menu /start
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
        "➡️ Use /start a qualquer momento para retornar aqui."
    )
    await safe_edit(update.callback_query, txt,
                    InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

# 4️⃣ Criar grupo
async def menu_criar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    user_states[uid] = {"state": "awaiting_group_name"}
    await safe_edit(update.callback_query, "📌 Digite o nome do novo grupo:",
                    InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Cancelar", callback_data="start")]]))

# 5️⃣ Processar texto (criação ou convite)
async def handle_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    text = update.message.text.strip()
    state = user_states.get(uid)
    sess = Session()

    # criar grupo
    if state and state.get("state") == "awaiting_group_name":
        name = text
        sess.add(Group(name=name, owner_id=uid))
        sess.commit()
        await update.message.reply_text(f"✅ Grupo *{name}* criado!", parse_mode="Markdown")
        user_states.pop(uid)
        return

    # convite de canal
    elif state and state.get("state") == "awaiting_channel_invite":
        match = re.search(r"@([\w\d_]+)", text) or re.search(r"t\.me/([\w\d_]+)", text)
        if not match:
            await update.message.reply_text("❌ Envie um @username ou link t.me válido.")
            return
        username = match.group(1)

        existing = sess.query(Channel).filter_by(username=username).first()
        if existing:
            chat_id = existing.id
            chat_title = existing.title
            is_owner = (existing.owner_id == uid)
        else:
            try:
                chat = await ctx.bot.get_chat(f"@{username}")
                if chat.type != "channel":
                    await update.message.reply_text("❌ Esse usuário não é um canal.")
                    return
                chat_id = chat.id
                chat_title = chat.title or username
                try:
                    admins = await ctx.bot.get_chat_administrators(chat.id)
                    owner = next((a.user for a in admins if a.status == "creator"), None)
                except:
                    owner = None
                sess.merge(Channel(
                    id=chat.id,
                    owner_id=owner.id if owner else None,
                    username=username,
                    title=chat_title,
                    authenticated=False
                ))
                is_owner = (owner and owner.id == uid)
            except Forbidden:
                await update.message.reply_text("❌ Bot não é admin ou não tem permissão no canal.")
                return
            except BadRequest as e:
                await update.message.reply_text(f"❌ Canal não encontrado: {e.message}")
                return
            except Exception as e:
                await update.message.reply_text(f"❌ Erro inesperado: {e}")
                return

        gid = state["group_id"]
        already = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=chat_id).first()
        if already:
            await update.message.reply_text("⚠️ Este canal já foi convidado ou adicionado.")
        else:
            sess.add(GroupChannel(
                group_id=gid,
                channel_id=chat_id,
                inviter_id=uid,
                accepted=True if is_owner else None
            ))
            sess.commit()
            if is_owner:
                await update.message.reply_text("✅ Canal adicionado automaticamente ao grupo!")
            else:
                await update.message.reply_text(
                    f"✅ Convite enviado ao canal *{chat_title}*! Agora ele precisa aceitar.",
                    parse_mode="Markdown"
                )
        user_states.pop(uid)

# 6️⃣ Meus canais
async def menu_meus_canais(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    chans = sess.query(Channel).filter_by(owner_id=uid).all()
    if not chans:
        await safe_edit(update.callback_query,
                        "🚫 Você não tem canais autenticados.",
                        InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
        return
    text = "📋 *Seus canais:*"
    for c in chans:
        link = f"https://t.me/{c.username}" if c.username else str(c.id)
        text += f"\n• {c.title} — {link}"
    await safe_edit(update.callback_query, text,
                    InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))

# 7️⃣ Meus grupos (proprietário)
async def menu_meus_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    grps = sess.query(Group).filter_by(owner_id=uid).all()
    if not grps:
        await safe_edit(update.callback_query,
                        "🚫 Você ainda não criou nenhum grupo.",
                        InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
        return
    kb = [[InlineKeyboardButton(g.name, callback_data=f"gerenciar_{g.id}")] for g in grps]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "📂 Seus grupos:", InlineKeyboardMarkup(kb))

# 8️⃣ Gerenciar grupo (lista canais)
async def gerenciar_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_", 1)[1])
    sess = Session()
    g = sess.get(Group, gid)
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

# 9️⃣ Convidar canal (via texto)
async def convite_manual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_", 1)[1])
    user_states[update.callback_query.from_user.id] = {"state": "awaiting_channel_invite", "group_id": gid}
    await safe_edit(update.callback_query,
                    "📥 Envie @username ou link do canal:",
                    InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")]]))

# 🔟 Aceitar/Recusar convite interno
async def handle_convite_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    parts = update.callback_query.data.split("_")
    if parts[0] == "convite" and len(parts) == 2:
        return await convite_manual(update, ctx)
    if len(parts) != 4:
        return await update.callback_query.answer("❌ Callback inválido", show_alert=True)
    _, action, gid, cid = parts
    gid, cid = int(gid), int(cid)
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=cid).first()
    if not gc:
        await safe_edit(update.callback_query, "❌ Convite inválido.")
        return
    ch = sess.get(Channel, cid)
    g = sess.get(Group, gid)

    if action == "aceitar":
        gc.accepted = True
        sess.commit()
        await safe_edit(update.callback_query, f"✅ Canal *{ch.title}* aceitou convite.")
    else:
        sess.delete(gc)
        sess.commit()
        await safe_edit(update.callback_query, f"❌ Canal *{ch.title}* recusou convite.")

    try:
        await ctx.bot.send_message(
            g.owner_id,
            f"📥 Canal *{ch.title}* {'aceitou' if action=='aceitar' else 'recusou'} convite no grupo *{g.name}*",
            parse_mode="Markdown"
        )
    except Exception:
        logger.error("Erro ao notificar dono do grupo")

# 1️⃣1️⃣ Explorar grupos públicos
async def explorar_grupos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    sess = Session()
    grps = sess.query(Group).all()
    if not grps:
        await safe_edit(update.callback_query,
                        "🌍 Ainda não há grupos públicos.",
                        InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
        return
    kb = [
        [InlineKeyboardButton(f"{g.name} ({sess.query(GroupChannel).filter_by(group_id=g.id,accepted=True).count()})",
                              callback_data=f"vergrp_{g.id}")]
        for g in grps
    ]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "🌐 Grupos públicos:", InlineKeyboardMarkup(kb))

# 1️⃣2️⃣ Visualizar grupo público
async def ver_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_", 1)[1])
    sess = Session()
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
    await safe_edit(update.callback_query, text,
                    InlineKeyboardMarkup([
                        [InlineKeyboardButton("📩 Solicitar entrada", callback_data=f"solicit_{gid}")],
                        [InlineKeyboardButton("↩️ Voltar", callback_data="explorar_grupos")]
                    ]))

# 1️⃣3️⃣ Solicitar entrada externa
async def solicitar_entrada(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    gid = int(update.callback_query.data.split("_", 1)[1])
    sess = Session()
    if sess.query(GroupChannel).filter_by(group_id=gid, channel_id=uid).first():
        await safe_edit(update.callback_query, "🚫 Já está no grupo ou já solicitou.")
        return
    ch = sess.get(Channel, uid)
    if not ch or not ch.authenticated:
        await safe_edit(update.callback_query, "❌ Seu canal não está autenticado.")
        return
    sess.add(GroupChannel(group_id=gid, channel_id=uid, inviter_id=uid, accepted=None))
    sess.commit()
    g = sess.get(Group, gid)
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

# 1️⃣4️⃣ Responder solicitação externa
async def handle_ext_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    parts = update.callback_query.data.split("_")
    if len(parts) != 4:
        await update.callback_query.answer("⚠️ Callback inválido.", show_alert=True)
        return
    _, action, gid, cid = parts
    gid, cid = int(gid), int(cid)
    sess = Session()
    gc = sess.query(GroupChannel).filter_by(group_id=gid, channel_id=cid).first()
    if not gc:
        await safe_edit(update.callback_query, "⚠️ Solicitação inválida.")
        return
    ch = sess.get(Channel, cid)
    g = sess.get(Group, gid)

    if action == "aceitar_ext":
        gc.accepted = True
        sess.commit()
        await safe_edit(update.callback_query, "✅ Canal aceito no grupo.")
        msg = f"✅ Seu canal foi aceito no grupo *{g.name}*"
    else:
        sess.delete(gc)
        sess.commit()
        await safe_edit(update.callback_query, "❌ Solicitação recusada.")
        msg = f"❌ Seu canal foi recusado no grupo *{g.name}*"
    try:
        await ctx.bot.send_message(cid, msg, parse_mode="Markdown")
    except:
        logger.error("Não foi possível notificar o canal")

# 1️⃣5️⃣ Remover canal do grupo
async def remocao_canal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_", 1)[1])
    sess = Session()
    chans = sess.query(GroupChannel).filter_by(group_id=gid, accepted=True).all()
    if not chans:
        await safe_edit(update.callback_query,
                        "🚫 Sem canais para remover.",
                        InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")]]))
        return
    kb = [[InlineKeyboardButton(sess.get(Channel, gc.channel_id).title,
                                callback_data=f"remover_confirm_{gid}_{gc.channel_id}")] for gc in chans]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data=f"gerenciar_{gid}")])
    await safe_edit(update.callback_query, "Escolha canal para remover:", InlineKeyboardMarkup(kb))

# 1️⃣6️⃣ Confirmar remoção
async def remover_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, _, gid, cid = update.callback_query.data.split("_")
    gid, cid = int(gid), int(cid)
    sess = Session()
    sess.query(GroupChannel).filter_by(group_id=gid, channel_id=cid).delete()
    sess.commit()
    return await gerenciar_grupo(update, ctx)

# 1️⃣7️⃣ Apagar grupo
async def prompt_delete_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    gid = int(update.callback_query.data.split("_", 1)[1])
    await safe_edit(update.callback_query,
                    "⚠️ Confirmar exclusão do grupo?",
                    InlineKeyboardMarkup([
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
# 1️⃣8️⃣ Sair de grupo (canal participante)
async def menu_sair_grupo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.callback_query.from_user.id
    sess = Session()
    chans = sess.query(GroupChannel).filter_by(channel_id=uid, accepted=True).all()
    if not chans:
        await safe_edit(update.callback_query,
                        "🚫 Você não participa de nenhum grupo.",
                        InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Voltar", callback_data="start")]]))
        return
    kb = [[InlineKeyboardButton(sess.get(Group, gc.group_id).name,
                                callback_data=f"sair_confirm_{gc.group_id}_{uid}")]
          for gc in chans]
    kb.append([InlineKeyboardButton("↩️ Voltar", callback_data="start")])
    await safe_edit(update.callback_query, "Escolha o grupo para sair:", InlineKeyboardMarkup(kb))

async def sair_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, _, gid, cid = update.callback_query.data.split("_")
    sess = Session()
    try:
        sess.query(GroupChannel).filter_by(group_id=int(gid), channel_id=int(cid)).delete()
        sess.commit()
    finally:
        sess.close()
    return await menu_sair_grupo(update, ctx)

# 1️⃣9️⃣ Replicar posts entre canais (mensagens simples e álbuns)
media_group_buffer: dict[str, list[Message]] = defaultdict(list)
media_group_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

async def new_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg:
        return

    sess = Session()
    gcs = sess.query(GroupChannel).filter_by(channel_id=msg.chat.id, accepted=True).all()
    if not gcs:
        return

    # 🔁 Encaminhar álbuns (media_group)
    if msg.media_group_id:
        group_id = msg.media_group_id
        lock = media_group_locks[group_id]

        async with lock:
            media_group_buffer[group_id].append(msg)
            await asyncio.sleep(2.5)

            album = sorted(media_group_buffer[group_id], key=lambda m: m.message_id)
            del media_group_buffer[group_id]
            del media_group_locks[group_id]

            for gc in gcs:
                grupo = sess.get(Group, gc.group_id)
                if not grupo:
                    continue
                for destino in grupo.channels:
                    if destino.accepted and destino.channel_id != msg.chat.id:
                        for part in album:
                            try:
                                await forward(part.chat.id, destino.channel_id, part.message_id)
                            except Exception as e:
                                print(f"Erro ao encaminhar parte de álbum para {destino.channel_id}: {e}")
        return

    # 🔁 Encaminhar mensagens individuais
    for gc in gcs:
        grupo = sess.get(Group, gc.group_id)
        if not grupo:
            continue
        for destino in grupo.channels:
            if destino.accepted and destino.channel_id != msg.chat.id:
                try:
                    await forward(msg.chat.id, destino.channel_id, msg.message_id)
                except Exception as e:
                    print(f"Erro ao encaminhar para {destino.channel_id}: {e}")
                    
# 2️⃣0️⃣ Central de callbacks
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
    if prefix == "convite":
        parts = data.split("_")
        if len(parts) == 2:
            return await convite_manual(update, ctx)
        else:
            return await handle_convite_response(update, ctx)

    routes = {
        "gerenciar": gerenciar_grupo, "aceitar": handle_convite_response,
        "recusar": handle_convite_response, "vergrp": ver_grupo,
        "solicit": solicitar_entrada, "aceitar_ext": handle_ext_response,
        "recusar_ext": handle_ext_response, "remover": remocao_canal,
        "remover_confirm": remover_confirm, "delete": prompt_delete_group,
        "delete_confirm": delete_confirm, "sair_confirm": sair_confirm
    }
    if prefix in routes:
        return await routes[prefix](update, ctx)

    await update.callback_query.answer("❌ Ação desconhecida.", show_alert=True)
            
