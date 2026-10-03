# -*- coding: utf-8 -*-
"""
Bot de Promoções para Telegram — versão Render (configuração por comandos)
===========================================================================
Coleta ofertas do Promobit (Amazon, Magalu, Shopee, Mercado Livre, Kabum…)
e publica automaticamente em um canal/grupo do Telegram.

ÚNICA variável de ambiente necessária:
  BOT_TOKEN  → token do @BotFather

Todo o resto é configurado por comandos no Telegram:

  ATIVAÇÃO DO CANAL
    1. Adicione o bot como ADMIN do canal (permissão de publicar)
    2. Poste /ativar dentro do canal  → pronto!
       (ou, no privado do bot: /ativar @seucanal)
    /desativar — para de postar no canal

  CONFIGURAÇÕES (no privado do bot — só o dono pode)
    /intervalo 15        → busca ofertas a cada 15 min (padrão 10)
    /maxposts 3          → máx. de posts por ciclo (padrão 5)
    /desconto 30         → só posta ofertas com 30%+ de desconto
    /bloquear capinha,película  → ignora ofertas com esses termos
    /lojas amazon,kabum  → só posta ofertas dessas lojas
    /limparfiltros       → remove todos os filtros
    /status              → mostra tudo
    /testar              → publica a oferta mais recente agora

O primeiro usuário que falar com o bot no privado vira o DONO
(único autorizado a mudar configurações).

Dependência: requests
"""

import json
import os
import re
import sys
import time
import threading
import html as html_mod
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE_DIR, "state.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

PROMOBIT_URLS = [
    "https://www.promobit.com.br/",
    "https://www.promobit.com.br/promocoes/recentes/",
]

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

DEFAULT_STATE = {
    "channel_id": "",
    "channel_title": "",
    "owner_id": None,
    "owner_name": "",
    "intervalo": 10,
    "max_posts": 5,
    "desconto_minimo": 0,
    "palavras_bloqueadas": [],
    "lojas_permitidas": [],
    "seen": [],
}

_lock = threading.Lock()
_stats = {"inicio": time.time(), "ciclos": 0, "postadas": 0, "ultimo_ciclo": None}


# ----------------------------------------------------------------- estado --
def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    st = dict(DEFAULT_STATE)
    st.update(data)
    return st


def save_state(st):
    with _lock:
        st["seen"] = st["seen"][-5000:]
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)


# ---------------------------------------------------------- telegram api ---
def tg(method, **params):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        r = requests.post(url, json=params, timeout=35)
        return r.json()
    except Exception as e:
        print(f"[tg] erro em {method}: {e}")
        return {"ok": False, "description": str(e)}


def reply(chat_id, text):
    tg("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")


# ------------------------------------------------------------- promobit ----
def fetch_offers():
    offers, ids = [], set()
    for url in PROMOBIT_URLS:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            m = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                r.text, re.S)
            if not m:
                continue
            data = json.loads(m.group(1))
            page = (data.get("props", {}).get("pageProps", {})
                        .get("serverOffers", {}).get("offers", []))
            for o in page:
                oid = o.get("offerId")
                if oid and oid not in ids and o.get("offerStatusName") == "APPROVED":
                    ids.add(oid)
                    offers.append(o)
        except Exception as e:
            print(f"[promobit] erro em {url}: {e}")
    offers.sort(key=lambda o: o.get("offerPublished", ""), reverse=True)
    return offers


# -------------------------------------------------------------- filtros ----
def passes_filters(o, st):
    title = (o.get("offerTitle") or "").lower()
    store = (o.get("storeName") or "").lower()

    for w in st.get("palavras_bloqueadas", []):
        if w.lower() in title:
            return False

    lojas = [s.lower() for s in st.get("lojas_permitidas", [])]
    if lojas and store not in lojas:
        return False

    desc_min = st.get("desconto_minimo", 0) or 0
    if desc_min and (o.get("offerDiscontPercentage") or 0) < desc_min:
        return False

    return True


# ------------------------------------------------------------- mensagem ----
def fmt_price(v):
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(v)


def build_message(o):
    title = html_mod.escape(o.get("offerTitle") or "Oferta")
    store = html_mod.escape(o.get("storeName") or "")
    price = o.get("offerPrice")
    old = o.get("offerOldPrice") or 0
    disc = o.get("offerDiscontPercentage") or 0
    coupon = o.get("offerCoupon")
    slug = o.get("offerSlug") or ""
    oid = o.get("offerId")

    lines = [f"🔥 <b>{title}</b>", ""]
    if price and float(price) > 0.02:
        p = f"💰 <b>{fmt_price(price)}</b>"
        if o.get("offerPriceType") == "STARTING_AT":
            p = f"💰 A partir de <b>{fmt_price(price)}</b>"
        if old and float(old) > float(price) and float(old) > 0.02:
            p += f"  <s>{fmt_price(old)}</s>"
        if disc:
            p += f"  (-{round(float(disc))}%)"
        lines.append(p)
    if coupon:
        lines.append(f"🎟️ Cupom: <code>{html_mod.escape(str(coupon))}</code>")
    if store:
        lines.append(f"🏪 Loja: {store}")
    lines += ["", "📣 Promoções todo dia — compartilhe o canal!"]

    caption = "\n".join(lines)
    keyboard = {"inline_keyboard": [[
        {"text": "🛒 Ir para a loja",
         "url": f"https://www.promobit.com.br/Redirect/to/{oid}/"},
        {"text": "ℹ️ Ver oferta",
         "url": f"https://www.promobit.com.br/oferta/{slug}/"},
    ]]}
    photo = o.get("offerPhoto") or ""
    photo_url = f"https://i.promobit.com.br/400{photo}" if photo.startswith("/") else None
    return caption, keyboard, photo_url


def post_offer(o, chat_id):
    caption, keyboard, photo_url = build_message(o)
    if photo_url:
        res = tg("sendPhoto", chat_id=chat_id, photo=photo_url,
                 caption=caption, parse_mode="HTML", reply_markup=keyboard)
        if res.get("ok"):
            return True
        print(f"[post] sendPhoto falhou ({res.get('description')}), tentando texto…")
    res = tg("sendMessage", chat_id=chat_id, text=caption,
             parse_mode="HTML", reply_markup=keyboard)
    if not res.get("ok"):
        print(f"[post] ERRO: {res.get('description')}")
    return res.get("ok", False)


# ----------------------------------------------------------- loop scraper --
def scraper_loop():
    st = load_state()
    seen = set(st.get("seen", []))
    first_run = not seen

    while True:
        try:
            st = load_state()
            seen |= set(st.get("seen", []))  # sincroniza com /puxar e /testar
            channel = st.get("channel_id", "")
            offers = fetch_offers()
            novos = [o for o in offers if o["offerId"] not in seen]
            _stats["ciclos"] += 1
            _stats["ultimo_ciclo"] = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[scraper] {len(offers)} ofertas na página, {len(novos)} novas.")

            if first_run and novos:
                # 1ª execução: marca tudo como visto e posta só as 2 últimas
                for o in novos:
                    seen.add(o["offerId"])
                novos = novos[:2]
                first_run = False
            else:
                novos = novos[: st.get("max_posts", 5)]

            if not channel:
                print("[scraper] canal não ativado — use /ativar no canal.")
            else:
                for o in reversed(novos):
                    if not passes_filters(o, st):
                        seen.add(o["offerId"])
                        continue
                    if post_offer(o, channel):
                        print(f"[post] ✔ {o.get('offerTitle', '')[:60]}")
                        seen.add(o["offerId"])
                        _stats["postadas"] += 1
                        time.sleep(4)  # rate-limit do Telegram
                    else:
                        time.sleep(4)

            st["seen"] = list(seen)
            save_state(st)
        except Exception as e:
            print(f"[scraper] erro inesperado: {e}")

        st = load_state()
        time.sleep(max(2, int(st.get("intervalo", 10))) * 60)


# ------------------------------------------------------------- comandos ----
HELP = (
    "👋 <b>Bot de Promoções</b>\n\n"
    "Eu coleto ofertas do Promobit (Amazon, Magalu, Shopee, Mercado Livre, "
    "Kabum e mais) e publico no seu canal.\n\n"
    "<b>🔛 Para ativar num canal:</b>\n"
    "1. Me adicione como <b>administrador</b> do canal\n"
    "2. Poste <code>/ativar</code> dentro do canal\n"
    "   (ou me mande aqui: <code>/ativar @seucanal</code>)\n\n"
    "<b>⚙️ Configurações (aqui no privado):</b>\n"
    "/status — configuração atual e estatísticas\n"
    "/puxar 3 — publica as 3 promos mais quentes do momento 🔥\n"
    "/testar — publica a oferta mais recente agora\n"
    "/intervalo 15 — minutos entre buscas\n"
    "/maxposts 3 — máx. de posts por busca\n"
    "/desconto 30 — % mínimo de desconto\n"
    "/bloquear capinha,película — bloquear termos\n"
    "/lojas amazon,kabum — só essas lojas\n"
    "/limparfiltros — remove todos os filtros\n"
    "/desativar — para de postar no canal\n"
    "/id — chat_id desta conversa"
)


def activate_channel(st, chat_id, title=""):
    st["channel_id"] = str(chat_id)
    st["channel_title"] = title or str(chat_id)
    save_state(st)
    print(f"[config] Canal ativado: {title} ({chat_id})")


def is_owner(st, user_id):
    return st.get("owner_id") == user_id


def handle_private(msg, st):
    """Comandos no privado (e em grupos, se mencionado)."""
    text = (msg.get("text") or "").strip()
    chat_id = msg["chat"]["id"]
    user = msg.get("from", {}) or {}
    user_id = user.get("id")

    # remove sufixo @nomedobot dos comandos
    text = re.sub(r"^(/\w+)@\w+", r"\1", text)
    cmd, _, args = text.partition(" ")
    args = args.strip()

    # primeiro humano que falar com o bot vira o dono
    if st.get("owner_id") is None and user_id:
        st["owner_id"] = user_id
        st["owner_name"] = user.get("first_name", "")
        save_state(st)
        print(f"[config] Dono registrado: {st['owner_name']} ({user_id})")

    if cmd == "/start" or cmd == "/ajuda":
        reply(chat_id, HELP)
        return
    if cmd == "/id":
        reply(chat_id, f"🆔 chat_id: <code>{chat_id}</code>")
        return

    if cmd in ("/ativar", "/desativar", "/status", "/testar", "/puxar",
               "/intervalo", "/maxposts", "/desconto", "/bloquear", "/lojas",
               "/limparfiltros"):
        if not is_owner(st, user_id):
            reply(chat_id, "🔒 Apenas o dono do bot pode usar este comando.")
            return

    if cmd == "/ativar":
        if not args:
            reply(chat_id,
                  "Use: <code>/ativar @seucanal</code>\n\n"
                  "Ou então poste <code>/ativar</code> direto dentro do canal "
                  "(eu preciso ser admin lá).")
            return
        alvo = args.split()[0]
        if not alvo.startswith("@") and not re.match(r"^-?\d+$", alvo):
            alvo = "@" + alvo
        chk = tg("getChat", chat_id=alvo)
        if not chk.get("ok"):
            reply(chat_id, f"❌ Não encontrei <code>{html_mod.escape(alvo)}</code>. "
                           "Verifique o nome e se sou membro/admin do canal.")
            return
        chat = chk["result"]
        test = tg("sendMessage", chat_id=chat["id"],
                  text="✅ Canal ativado! As promoções serão publicadas aqui. 🔥")
        if not test.get("ok"):
            reply(chat_id, "❌ Achei o canal, mas não consigo publicar nele. "
                           "Me adicione como <b>administrador</b> com permissão "
                           "de publicar e tente de novo.")
            return
        activate_channel(st, chat["id"], chat.get("title", alvo))
        reply(chat_id, f"✅ Ativado no canal <b>{html_mod.escape(chat.get('title', alvo))}</b>! "
                       "As ofertas novas serão postadas automaticamente.")

    elif cmd == "/desativar":
        if st.get("channel_id"):
            nome = st.get("channel_title", st["channel_id"])
            st["channel_id"] = ""
            st["channel_title"] = ""
            save_state(st)
            reply(chat_id, f"🛑 Desativado. Não vou mais postar em <b>{html_mod.escape(nome)}</b>.")
        else:
            reply(chat_id, "Nenhum canal estava ativado.")

    elif cmd == "/status":
        canal = st.get("channel_title") or st.get("channel_id") or "❌ nenhum (use /ativar)"
        up_h = (time.time() - _stats["inicio"]) / 3600
        bloq = ", ".join(st.get("palavras_bloqueadas", [])) or "—"
        lojas = ", ".join(st.get("lojas_permitidas", [])) or "todas"
        reply(chat_id,
              f"📊 <b>Status</b>\n"
              f"Canal: <b>{html_mod.escape(str(canal))}</b>\n"
              f"Intervalo: {st.get('intervalo')} min\n"
              f"Max posts/ciclo: {st.get('max_posts')}\n"
              f"Desconto mínimo: {st.get('desconto_minimo') or 'sem filtro'}\n"
              f"Termos bloqueados: {html_mod.escape(bloq)}\n"
              f"Lojas: {html_mod.escape(lojas)}\n"
              f"Online há: {up_h:.1f} h\n"
              f"Ciclos: {_stats['ciclos']} | Postadas: {_stats['postadas']}")

    elif cmd == "/testar":
        if not st.get("channel_id"):
            reply(chat_id, "❌ Nenhum canal ativado. Use /ativar primeiro.")
            return
        offers = fetch_offers()
        if offers and post_offer(offers[0], st["channel_id"]):
            reply(chat_id, "✅ Oferta de teste publicada no canal!")
        else:
            reply(chat_id, "❌ Não consegui publicar. Ainda sou admin do canal?")

    elif cmd == "/puxar":
        if not st.get("channel_id"):
            reply(chat_id, "❌ Nenhum canal ativado. Use /ativar primeiro.")
            return
        try:
            n = max(1, min(10, int(args))) if args else 3
        except ValueError:
            n = 3
        reply(chat_id, f"🔎 Buscando as {n} promoções mais quentes do momento…")
        offers = [o for o in fetch_offers() if passes_filters(o, st)]
        # mais quentes primeiro (engajamento no Promobit)
        offers.sort(key=lambda o: o.get("offerEngagementScore") or 0, reverse=True)
        seen = set(st.get("seen", []))
        # prioriza as que ainda não foram postadas no canal
        fila = ([o for o in offers if o["offerId"] not in seen] +
                [o for o in offers if o["offerId"] in seen])[:n]
        postadas = 0
        for o in fila:
            if post_offer(o, st["channel_id"]):
                postadas += 1
                seen.add(o["offerId"])
                _stats["postadas"] += 1
                time.sleep(4)  # rate-limit do Telegram
        st["seen"] = list(seen)
        save_state(st)
        if postadas:
            reply(chat_id, f"🔥 Publiquei <b>{postadas}</b> promoção(ões) do momento no canal!")
        else:
            reply(chat_id, "❌ Não consegui publicar. Ainda sou admin do canal?")

    elif cmd == "/intervalo":
        try:
            v = max(2, min(240, int(args)))
            st["intervalo"] = v
            save_state(st)
            reply(chat_id, f"⏱️ Intervalo ajustado para <b>{v} min</b>.")
        except ValueError:
            reply(chat_id, "Use: <code>/intervalo 15</code> (minutos, 2 a 240)")

    elif cmd == "/maxposts":
        try:
            v = max(1, min(20, int(args)))
            st["max_posts"] = v
            save_state(st)
            reply(chat_id, f"📦 Máximo de <b>{v} posts</b> por ciclo.")
        except ValueError:
            reply(chat_id, "Use: <code>/maxposts 3</code> (1 a 20)")

    elif cmd == "/desconto":
        try:
            v = max(0, min(99, int(args)))
            st["desconto_minimo"] = v
            save_state(st)
            reply(chat_id, f"🏷️ Só vou postar ofertas com <b>{v}%+</b> de desconto."
                  if v else "🏷️ Filtro de desconto removido.")
        except ValueError:
            reply(chat_id, "Use: <code>/desconto 30</code> (0 remove o filtro)")

    elif cmd == "/bloquear":
        if args:
            st["palavras_bloqueadas"] = [w.strip() for w in args.split(",") if w.strip()]
            save_state(st)
            reply(chat_id, "🚫 Termos bloqueados: "
                  f"<b>{html_mod.escape(', '.join(st['palavras_bloqueadas']))}</b>")
        else:
            atual = ", ".join(st.get("palavras_bloqueadas", [])) or "nenhum"
            reply(chat_id, f"Termos bloqueados: <b>{html_mod.escape(atual)}</b>\n"
                           "Use: <code>/bloquear capinha,película</code>")

    elif cmd == "/lojas":
        if args:
            st["lojas_permitidas"] = [w.strip() for w in args.split(",") if w.strip()]
            save_state(st)
            reply(chat_id, "🏪 Só vou postar ofertas de: "
                  f"<b>{html_mod.escape(', '.join(st['lojas_permitidas']))}</b>")
        else:
            atual = ", ".join(st.get("lojas_permitidas", [])) or "todas"
            reply(chat_id, f"Lojas permitidas: <b>{html_mod.escape(atual)}</b>\n"
                           "Use: <code>/lojas amazon,kabum</code>")

    elif cmd == "/limparfiltros":
        st["palavras_bloqueadas"] = []
        st["lojas_permitidas"] = []
        st["desconto_minimo"] = 0
        save_state(st)
        reply(chat_id, "🧹 Todos os filtros foram removidos.")


def handle_channel_post(msg, st):
    """Comandos postados dentro do canal/grupo: /ativar, /desativar, /puxar."""
    text = (msg.get("text") or "").strip()
    text = re.sub(r"^(/\w+)@\w+", r"\1", text)
    chat = msg.get("chat", {})
    chat_id = chat.get("id")

    if text.startswith("/puxar"):
        parts = text.split()
        try:
            n = max(1, min(10, int(parts[1]))) if len(parts) > 1 else 3
        except ValueError:
            n = 3
        offers = [o for o in fetch_offers() if passes_filters(o, st)]
        offers.sort(key=lambda o: o.get("offerEngagementScore") or 0, reverse=True)
        seen = set(st.get("seen", []))
        fila = ([o for o in offers if o["offerId"] not in seen] +
                [o for o in offers if o["offerId"] in seen])[:n]
        for o in fila:
            if post_offer(o, chat_id):
                seen.add(o["offerId"])
                _stats["postadas"] += 1
                time.sleep(4)
        st["seen"] = list(seen)
        save_state(st)
        return

    if text.startswith("/ativar"):
        activate_channel(st, chat_id, chat.get("title", ""))
        tg("sendMessage", chat_id=chat_id,
           text="✅ Canal ativado! As promoções serão publicadas aqui. 🔥")
    elif text.startswith("/desativar"):
        if str(st.get("channel_id")) == str(chat_id):
            st["channel_id"] = ""
            st["channel_title"] = ""
            save_state(st)
            tg("sendMessage", chat_id=chat_id,
               text="🛑 Desativado. Não vou mais postar aqui.")


def handle_update(upd):
    st = load_state()

    # bot virou admin de canal → só orienta (ativação é via /ativar)
    mcm = upd.get("my_chat_member")
    if mcm:
        chat = mcm.get("chat", {})
        status = mcm.get("new_chat_member", {}).get("status")
        if status == "administrator" and chat.get("type") == "channel":
            tg("sendMessage", chat_id=chat["id"],
               text="👋 Fui adicionado como admin! Poste /ativar aqui no "
                    "canal para eu começar a publicar as promoções.")
        return

    post = upd.get("channel_post")
    if post:
        handle_channel_post(post, st)
        return

    msg = upd.get("message")
    if msg:
        ctype = msg.get("chat", {}).get("type")
        if ctype == "private":
            handle_private(msg, st)
        elif ctype in ("group", "supergroup"):
            text = (msg.get("text") or "")
            if (text.startswith("/ativar") or text.startswith("/desativar")
                    or text.startswith("/puxar")):
                handle_channel_post(msg, st)


def updates_loop():
    offset = 0
    while True:
        res = tg("getUpdates", offset=offset, timeout=25,
                 allowed_updates=["message", "channel_post", "my_chat_member"])
        if res.get("ok"):
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    handle_update(upd)
                except Exception as e:
                    print(f"[updates] erro: {e}")
        else:
            time.sleep(5)


# --------------------------------------------- servidor http (health) ------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        st = load_state()
        up_h = (time.time() - _stats["inicio"]) / 3600
        body = json.dumps({
            "ok": True,
            "bot": "promo-bot",
            "canal": st.get("channel_title") or st.get("channel_id") or "não ativado",
            "online_horas": round(up_h, 2),
            "ciclos": _stats["ciclos"],
            "postadas": _stats["postadas"],
            "ultimo_ciclo": _stats["ultimo_ciclo"],
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def http_server():
    port = int(os.environ.get("PORT", "10000"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"[http] health check ouvindo na porta {port}")
    srv.serve_forever()


# ---------------------------------------------------------------- main -----
if __name__ == "__main__":
    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        sys.exit("Defina a variável de ambiente BOT_TOKEN!")

    tg("deleteWebhook")  # garante que o polling funcione

    me = tg("getMe")
    if not me.get("ok"):
        sys.exit(f"Token inválido: {me.get('description')}")
    print(f"🤖 Bot @{me['result']['username']} iniciado!")

    st = load_state()
    if st.get("channel_id"):
        print(f"📣 Canal ativo: {st.get('channel_title') or st['channel_id']}")
    else:
        print("⚠️  Nenhum canal ativado. Adicione o bot como admin e poste "
              "/ativar no canal.")

    threading.Thread(target=scraper_loop, daemon=True).start()
    threading.Thread(target=updates_loop, daemon=True).start()
    http_server()
