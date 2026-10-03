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
    "https://www.promobit.com.br/promocoes/recentes/?page=2",
    "https://www.promobit.com.br/promocoes/recentes/?page=3",
]

CUPONS_URL = "https://www.promobit.com.br/cupons/"
CUPONS_LOJA_URL = "https://www.promobit.com.br/cupons/loja/{slug}/"

# score de engajamento a partir do qual a oferta ganha o selo "BOMBANDO"
HOT_SCORE = 300

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
    "seen_cupons": [],
    "auto_cupons": False,
    "auto_puxar": False,
    "auto_puxar_min": 1,
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
        st["seen_cupons"] = st.get("seen_cupons", [])[-2000:]
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
API_BASE = "https://api.promobit.com.br"
API_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json",
    "Origin": "https://www.promobit.com.br",
    "Referer": "https://www.promobit.com.br/",
}


def _api_get(path, tries=2):
    """GET na API oficial do Promobit (com retry). None se falhar."""
    for i in range(tries):
        try:
            r = requests.get(API_BASE + path, headers=API_HEADERS, timeout=30)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print(f"[api] tentativa {i+1} falhou em {path}: {e}")
        time.sleep(2 * (i + 1))
    return None


def _snake_to_camel(k):
    parts = k.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _normalize(d):
    """Converte as chaves snake_case da API para o formato camelCase."""
    return {_snake_to_camel(k): v for k, v in d.items()} if isinstance(d, dict) else d


def _get_next_data(url, tries=2):
    """Baixa uma página do Promobit e devolve o pageProps (com retry)."""
    for i in range(tries):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            m = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                r.text, re.S)
            if m:
                return json.loads(m.group(1)).get("props", {}).get("pageProps", {})
        except Exception as e:
            print(f"[promobit] tentativa {i+1} falhou em {url}: {e}")
            time.sleep(2 * (i + 1))
    return {}


def community_ok(o):
    """Descarta ofertas mal avaliadas pela comunidade do Promobit."""
    r = o.get("ratings") or {}
    positivos = (r.get("great") or 0) + (r.get("good") or 0) + (r.get("amazing") or 0)
    negativos = r.get("bad") or 0
    return not (negativos >= 3 and negativos > positivos)


def real_discount(o):
    """% de desconto informado, ou calculado pelo preço antigo."""
    disc = o.get("offerDiscontPercentage") or 0
    if disc:
        return float(disc)
    try:
        price, old = float(o.get("offerPrice") or 0), float(o.get("offerOldPrice") or 0)
        if old > price > 0.02 and old > 0.02:
            return (1 - price / old) * 100
    except Exception:
        pass
    return 0


OK_STATUSES = {"APPROVED", "TOP_OFFER", "RECENTS"}


def _collect(raw_list, offers, ids):
    for raw in raw_list or []:
        o = _normalize(raw)
        oid = o.get("offerId")
        if (oid and oid not in ids
                and o.get("offerStatusName") in OK_STATUSES
                and community_ok(o)):
            ids.add(oid)
            offers.append(o)


def fetch_offers(pages=2):
    """Ofertas mais recentes — API oficial (2 páginas = ~100 ofertas),
    com fallback para o scraping do site se a API falhar."""
    offers, ids = [], set()

    cursor = ""
    for _ in range(pages):
        d = _api_get(f"/offers?limit=50&sort=latest"
                     + (f"&after={cursor}" if cursor else ""))
        if not d:
            break
        _collect(d.get("offers"), offers, ids)
        cursor = d.get("after") or ""
        if not cursor:
            break

    if not offers:  # fallback: scraping das páginas HTML
        print("[promobit] API indisponível, usando fallback HTML…")
        for url in PROMOBIT_URLS:
            pp = _get_next_data(url)
            _collect(pp.get("serverOffers", {}).get("offers", []), offers, ids)

    offers.sort(key=lambda o: o.get("offerPublished", ""), reverse=True)
    return offers


def fetch_hot_offers():
    """Ofertas mais quentes do momento (ranking 'hot' + destaques da API)."""
    offers, ids = [], set()
    d = _api_get("/offers?limit=50&sort=hot")
    if d:
        _collect(d.get("offers"), offers, ids)
    feat = _api_get("/offers/featured?limit=20")
    if isinstance(feat, list):
        _collect(feat, offers, ids)
    if not offers:
        offers = fetch_offers()
    offers.sort(key=lambda o: o.get("offerEngagementScore") or 0, reverse=True)
    return offers


# --------------------------------------------------------------- cupons ----
def fetch_coupons(store=None):
    """Cupons ativos do Promobit — API oficial (geral ou por loja),
    com fallback para o scraping do site."""
    raw = None
    if store:
        slug = re.sub(r"[^a-z0-9]+", "-",
                      store.lower().strip().replace("@", "")).strip("-")
        raw = _api_get(f"/stores/{slug}/coupons?limit=40")
    else:
        raw = _api_get("/coupon?limit=50")

    coupons = [_normalize(c) for c in raw] if isinstance(raw, list) else []

    if not coupons:  # fallback HTML
        url = CUPONS_LOJA_URL.format(slug=slug) if store else CUPONS_URL
        pp = _get_next_data(url)
        coupons = pp.get("serverCoupons", {}).get("coupons", [])

    out, ids = [], set()
    for c in coupons:
        cid = c.get("couponId")
        if cid and cid not in ids and c.get("couponStatusName") == "APPROVED" \
                and c.get("couponCode"):
            ids.add(cid)
            out.append(c)
    # mais recentemente verificados primeiro
    out.sort(key=lambda c: c.get("couponVerified") or "", reverse=True)
    return out


def build_coupon_message(c):
    store = html_mod.escape(c.get("storeName") or "")
    title = html_mod.escape(c.get("couponTitle") or f"Cupom {store}")
    disc = html_mod.escape(c.get("couponDiscountShort") or
                           c.get("couponDiscountValue") or "")
    on = html_mod.escape(c.get("couponDiscountOn") or "")
    code = html_mod.escape(str(c.get("couponCode") or ""))
    instr = (c.get("couponInstructions") or "").strip()
    until = c.get("couponUntil")

    lines = [f"🎟️ <b>CUPOM {store.upper()}</b>", "", f"<b>{title}</b>"]
    if disc:
        lines.append(f"💸 {disc}" + (f" em {on}" if on else ""))
    lines.append(f"🔑 Código: <code>{code}</code>  (toque para copiar)")
    if instr:
        if len(instr) > 180:
            instr = instr[:177] + "…"
        lines.append(f"📋 {html_mod.escape(instr.capitalize())}")
    if until:
        try:
            d = until[:10].split("-")
            lines.append(f"⏳ Válido até {d[2]}/{d[1]}/{d[0]}")
        except Exception:
            pass
    lines += ["", "📣 Promoções e cupons todo dia — compartilhe o canal!"]

    keyboard = {"inline_keyboard": [[
        {"text": f"🛒 Usar cupom na {c.get('storeName', 'loja')}",
         "url": f"https://www.promobit.com.br/Redirect/cupom/{c.get('couponId')}"},
    ]]}
    return "\n".join(lines), keyboard


def post_coupon(c, chat_id):
    text, keyboard = build_coupon_message(c)
    res = tg("sendMessage", chat_id=chat_id, text=text,
             parse_mode="HTML", reply_markup=keyboard)
    if not res.get("ok"):
        print(f"[cupom] ERRO: {res.get('description')}")
    return res.get("ok", False)


# -------------------------------------------------------------- filtros ----
def _norm_store(s):
    """'Mercado Livre' / 'mercado-livre' / 'MercadoLivre' → 'mercadolivre'"""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def passes_filters(o, st):
    title = (o.get("offerTitle") or "").lower()
    store = _norm_store(o.get("storeName"))

    for w in st.get("palavras_bloqueadas", []):
        if w.lower() in title:
            return False

    lojas = [_norm_store(s) for s in st.get("lojas_permitidas", [])]
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
    categoria = html_mod.escape(o.get("categoryName") or "")
    price = o.get("offerPrice")
    old = o.get("offerOldPrice") or 0
    disc = real_discount(o)
    coupon = o.get("offerCoupon")
    slug = o.get("offerSlug") or ""
    oid = o.get("offerId")
    hot = (o.get("offerEngagementScore") or 0) >= HOT_SCORE

    header = "🚨🔥 <b>BOMBANDO!</b>\n" if hot else ""
    lines = [f"{header}🔥 <b>{title}</b>", ""]
    if price and float(price) > 0.02:
        p = f"💰 <b>{fmt_price(price)}</b>"
        if o.get("offerPriceType") == "STARTING_AT":
            p = f"💰 A partir de <b>{fmt_price(price)}</b>"
        if old and float(old) > float(price) and float(old) > 0.02:
            p += f"  <s>{fmt_price(old)}</s>"
        if disc:
            p += f"  (-{round(disc)}%)"
        lines.append(p)
    if coupon:
        lines.append(f"🎟️ Cupom: <code>{html_mod.escape(str(coupon))}</code>  (toque para copiar)")
    if store:
        loja = f"🏪 Loja: {store}"
        if categoria:
            loja += f"  |  📂 {categoria}"
        lines.append(loja)
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

            # ------- cupons automáticos (se ativado com /autocupons on) ----
            if channel and st.get("auto_cupons"):
                seen_cup = set(st.get("seen_cupons", []))
                novos_cup = [c for c in fetch_coupons()
                             if c["couponId"] not in seen_cup][:2]
                if not seen_cup and novos_cup:
                    # 1ª vez: marca tudo como visto e posta só 1
                    seen_cup = {c["couponId"] for c in fetch_coupons()}
                    novos_cup = novos_cup[:1]
                for c in novos_cup:
                    if post_coupon(c, channel):
                        print(f"[cupom] ✔ {c.get('storeName')}: {c.get('couponCode')}")
                        seen_cup.add(c["couponId"])
                        time.sleep(4)
                st["seen_cupons"] = list(seen_cup)

            st["seen"] = list(seen)
            save_state(st)
        except Exception as e:
            print(f"[scraper] erro inesperado: {e}")

        st = load_state()
        time.sleep(max(2, int(st.get("intervalo", 10))) * 60)


# ------------------------------------------------------ loop auto-puxar ----
def auto_puxar_loop():
    """Com /autopuxar on: publica 1 promoção quente a cada X min (padrão 1).
    Nunca repete — quando não há oferta inédita, simplesmente pula a vez."""
    while True:
        st = load_state()
        minutos = max(1, min(60, int(st.get("auto_puxar_min", 1))))
        time.sleep(minutos * 60)
        try:
            st = load_state()
            if not (st.get("auto_puxar") and st.get("channel_id")):
                continue
            seen = set(st.get("seen", []))
            fila = [o for o in fetch_hot_offers()
                    if o["offerId"] not in seen and passes_filters(o, st)]
            if not fila:  # sem quente inédita → tenta as recentes
                fila = [o for o in fetch_offers(pages=1)
                        if o["offerId"] not in seen and passes_filters(o, st)]
            if not fila:
                print("[autopuxar] nenhuma oferta inédita agora — pulando.")
                continue
            o = fila[0]
            if post_offer(o, st["channel_id"]):
                print(f"[autopuxar] ✔ {o.get('offerTitle', '')[:60]}")
                seen.add(o["offerId"])
                _stats["postadas"] += 1
                st["seen"] = list(seen)
                save_state(st)
        except Exception as e:
            print(f"[autopuxar] erro: {e}")


# ------------------------------------------------------------- comandos ----
HELP = (
    "👋 <b>Bot de Promoções</b>\n\n"
    "Eu coleto ofertas do Promobit (Amazon, Magalu, Shopee, Mercado Livre, "
    "Kabum e mais) e publico no seu canal.\n\n"
    "<b>🔛 Para ativar num canal:</b>\n"
    "1. Me adicione como <b>administrador</b> do canal\n"
    "2. Poste <code>/ativar</code> dentro do canal\n"
    "   (ou me mande aqui: <code>/ativar @seucanal</code>)\n\n"
    "<b>🔥 Promoções:</b>\n"
    "/puxar 3 — publica as 3 promos mais quentes do momento\n"
    "/autopuxar on — posta 1 promo quente a cada 1 min 🤖\n"
    "/autopuxar 5 — mesma coisa, a cada 5 min\n"
    "/autopuxar off — desliga\n"
    "/testar — publica a oferta mais recente agora\n\n"
    "<b>🎟️ Cupons:</b>\n"
    "/cupons — vê os cupons ativos do momento (aqui no chat)\n"
    "/cupons amazon — cupons de uma loja específica\n"
    "/puxarcupons 3 — publica 3 cupons no canal\n"
    "/autocupons on — posta cupons novos automaticamente\n\n"
    "<b>⚙️ Configurações (aqui no privado):</b>\n"
    "/status — configuração atual e estatísticas\n"
    "/intervalo 15 — minutos entre buscas\n"
    "/maxposts 3 — máx. de posts por busca\n"
    "/desconto 30 — % mínimo de desconto\n"
    "/bloquear capinha,película — bloquear termos\n"
    "/lojas amazon,kabum,aliexpress — só essas lojas\n"
    "/lojas add aliexpress — adiciona uma loja à lista\n"
    "/lojas remover shopee — tira uma loja da lista\n"
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
               "/autopuxar", "/cupons", "/puxarcupons", "/autocupons",
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
              f"Cupons automáticos: {'ligado ✅' if st.get('auto_cupons') else 'desligado ❌'}\n"
              f"Puxa automático: {('ligado ✅ (' + str(st.get('auto_puxar_min', 1)) + ' min)') if st.get('auto_puxar') else 'desligado ❌'}\n"
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
        offers = [o for o in fetch_hot_offers() if passes_filters(o, st)]
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

    elif cmd == "/autopuxar":
        v = args.lower().strip()
        if v in ("on", "ligado", "sim", "1min", "ligar"):
            st["auto_puxar"] = True
            st["auto_puxar_min"] = 1
            save_state(st)
            reply(chat_id, "🤖 Puxa automático <b>ATIVADO</b>: vou postar "
                           "1 promoção quente a cada <b>1 minuto</b>.\n"
                           "Quando não houver promo inédita, eu pulo a vez "
                           "(nunca repito oferta).\n\n"
                           "Para mudar o ritmo: <code>/autopuxar 5</code> | "
                           "Para desligar: <code>/autopuxar off</code>")
        elif v in ("off", "desligado", "nao", "não", "0", "desligar"):
            st["auto_puxar"] = False
            save_state(st)
            reply(chat_id, "🤖 Puxa automático <b>desligado</b>.")
        elif v.isdigit():
            minutos = max(1, min(60, int(v)))
            st["auto_puxar"] = True
            st["auto_puxar_min"] = minutos
            save_state(st)
            reply(chat_id, f"🤖 Puxa automático <b>ATIVADO</b>: 1 promoção "
                           f"quente a cada <b>{minutos} min</b>.")
        else:
            atual = (f"ligado ✅ (a cada {st.get('auto_puxar_min', 1)} min)"
                     if st.get("auto_puxar") else "desligado ❌")
            reply(chat_id, f"Puxa automático: <b>{atual}</b>\n"
                           "Use: <code>/autopuxar on</code> (1 min), "
                           "<code>/autopuxar 5</code> (5 min) ou "
                           "<code>/autopuxar off</code>")

    elif cmd == "/cupons":
        loja = args.split()[0] if args else None
        reply(chat_id, "🔎 Buscando cupons ativos…")
        cupons = fetch_coupons(loja)
        if not cupons:
            reply(chat_id, f"❌ Nenhum cupom encontrado"
                  + (f" para <b>{html_mod.escape(loja)}</b>. Tente o nome como "
                     "aparece no Promobit (ex: amazon, magazine-luiza, "
                     "mercado-livre, shopee, aliexpress, kabum)." if loja else "."))
            return
        linhas = [f"🎟️ <b>Cupons ativos{' — ' + html_mod.escape(cupons[0].get('storeName','')) if loja else ''}</b>\n"]
        for c in cupons[:10]:
            disc = c.get("couponDiscountShort") or c.get("couponDiscountValue") or ""
            on = c.get("couponDiscountOn") or ""
            linha = (f"• <b>{html_mod.escape(c.get('storeName',''))}</b> — "
                     f"{html_mod.escape(disc)}"
                     + (f" em {html_mod.escape(on)}" if on else "")
                     + f"\n  🔑 <code>{html_mod.escape(str(c.get('couponCode')))}</code>")
            linhas.append(linha)
        linhas.append("\n💡 Use /puxarcupons 3 para publicar no canal.")
        reply(chat_id, "\n".join(linhas))

    elif cmd == "/puxarcupons":
        if not st.get("channel_id"):
            reply(chat_id, "❌ Nenhum canal ativado. Use /ativar primeiro.")
            return
        partes = args.split() if args else []
        n, loja = 3, None
        for p in partes:
            if p.isdigit():
                n = max(1, min(10, int(p)))
            else:
                loja = p
        reply(chat_id, f"🔎 Publicando {n} cupom(ns) no canal…")
        cupons = fetch_coupons(loja)
        seen_cup = set(st.get("seen_cupons", []))
        fila = ([c for c in cupons if c["couponId"] not in seen_cup] +
                [c for c in cupons if c["couponId"] in seen_cup])[:n]
        postados = 0
        for c in fila:
            if post_coupon(c, st["channel_id"]):
                postados += 1
                seen_cup.add(c["couponId"])
                time.sleep(4)
        st["seen_cupons"] = list(seen_cup)
        save_state(st)
        reply(chat_id, f"🎟️ Publiquei <b>{postados}</b> cupom(ns) no canal!"
              if postados else "❌ Não consegui publicar nenhum cupom.")

    elif cmd == "/autocupons":
        v = args.lower().strip()
        if v in ("on", "ligado", "sim", "1"):
            st["auto_cupons"] = True
            save_state(st)
            reply(chat_id, "🎟️ Cupons automáticos <b>ATIVADOS</b> — vou postar "
                           "cupons novos a cada ciclo de busca.")
        elif v in ("off", "desligado", "nao", "não", "0"):
            st["auto_cupons"] = False
            save_state(st)
            reply(chat_id, "🎟️ Cupons automáticos <b>desativados</b>.")
        else:
            atual = "ligado ✅" if st.get("auto_cupons") else "desligado ❌"
            reply(chat_id, f"Cupons automáticos: <b>{atual}</b>\n"
                           "Use: <code>/autocupons on</code> ou <code>/autocupons off</code>")

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
        sub, _, resto = args.partition(" ")
        sub = sub.lower()
        if sub in ("add", "adicionar", "+") and resto.strip():
            novas = [w.strip().lower() for w in resto.split(",") if w.strip()]
            lojas = [l.lower() for l in st.get("lojas_permitidas", [])]
            for n in novas:
                if n not in lojas:
                    lojas.append(n)
            st["lojas_permitidas"] = lojas
            save_state(st)
            reply(chat_id, "🏪 Loja(s) adicionada(s)! Lista atual: "
                  f"<b>{html_mod.escape(', '.join(lojas))}</b>")
        elif sub in ("remover", "remove", "tirar", "-") and resto.strip():
            tirar = [w.strip().lower() for w in resto.split(",") if w.strip()]
            lojas = [l for l in st.get("lojas_permitidas", [])
                     if l.lower() not in tirar]
            st["lojas_permitidas"] = lojas
            save_state(st)
            reply(chat_id, "🏪 Removida(s)! Lista atual: "
                  f"<b>{html_mod.escape(', '.join(lojas)) or 'todas as lojas'}</b>")
        elif args:
            st["lojas_permitidas"] = [w.strip() for w in args.split(",") if w.strip()]
            save_state(st)
            reply(chat_id, "🏪 Só vou postar ofertas de: "
                  f"<b>{html_mod.escape(', '.join(st['lojas_permitidas']))}</b>")
        else:
            atual = ", ".join(st.get("lojas_permitidas", [])) or "todas"
            reply(chat_id, f"Lojas permitidas: <b>{html_mod.escape(atual)}</b>\n"
                           "Exemplos:\n"
                           "<code>/lojas amazon,kabum,aliexpress</code> — define a lista\n"
                           "<code>/lojas add aliexpress</code> — adiciona\n"
                           "<code>/lojas remover shopee</code> — remove")

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
    cmd = text.split()[0] if text else ""

    if cmd == "/puxar":
        parts = text.split()
        try:
            n = max(1, min(10, int(parts[1]))) if len(parts) > 1 else 3
        except ValueError:
            n = 3
        offers = [o for o in fetch_hot_offers() if passes_filters(o, st)]
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

    if cmd == "/puxarcupons":
        parts = text.split()
        n, loja = 3, None
        for p in parts[1:]:
            if p.isdigit():
                n = max(1, min(10, int(p)))
            else:
                loja = p
        cupons = fetch_coupons(loja)
        seen_cup = set(st.get("seen_cupons", []))
        fila = ([c for c in cupons if c["couponId"] not in seen_cup] +
                [c for c in cupons if c["couponId"] in seen_cup])[:n]
        for c in fila:
            if post_coupon(c, chat_id):
                seen_cup.add(c["couponId"])
                time.sleep(4)
        st["seen_cupons"] = list(seen_cup)
        save_state(st)
        return

    if cmd == "/ativar":
        activate_channel(st, chat_id, chat.get("title", ""))
        tg("sendMessage", chat_id=chat_id,
           text="✅ Canal ativado! As promoções serão publicadas aqui. 🔥")
    elif cmd == "/desativar":
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
                    or text.startswith("/puxar")
                    or text.startswith("/puxarcupons")):
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
    threading.Thread(target=auto_puxar_loop, daemon=True).start()
    threading.Thread(target=updates_loop, daemon=True).start()
    http_server()
