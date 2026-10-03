# 🤖 Bot de Promoções — versão Render (configuração por comandos)

Bot de Telegram que coleta ofertas e **cupons** do **Promobit** (Amazon,
Magalu, Shopee, Mercado Livre, Kabum…) e publica automaticamente no seu canal.

**Como ele encontra as promoções:**
- Usa a **API oficial do Promobit** (~100 ofertas recentes por ciclo, com
  fallback automático para scraping do site se a API falhar)
- Ranking **"hot"** real do Promobit para o `/puxar` (promos do momento)
- Filtra ofertas **mal avaliadas pela comunidade** (muitos votos negativos)
- Selo 🚨🔥 **BOMBANDO!** nas ofertas com alto engajamento
- Cupons com código copiável, desconto, validade e link direto da loja

**Única variável de ambiente: `BOT_TOKEN`.** Todo o resto — canal, intervalo,
filtros — é configurado por **comandos no próprio Telegram**.

## 🚀 Deploy no Render

1. Suba estes arquivos num repositório do GitHub
   (`bot.py`, `requirements.txt`, `render.yaml`)
2. Em [render.com](https://render.com): **New → Web Service** → selecione o
   repositório (o `render.yaml` configura tudo; plano **Free**)
3. Em **Environment**, adicione só:
   ```
   BOT_TOKEN=seu_token_do_botfather
   ```
4. **Create Web Service** e aguarde o deploy ✅

### Evitar a hibernação do plano free ⏰

O free do Render hiberna após ~15 min sem tráfego. O bot tem um endpoint de
health check — crie um monitor gratuito no [UptimeRobot](https://uptimerobot.com)
(ou cron-job.org) pingando a URL do serviço
(`https://promo-bot-xxxx.onrender.com`) **a cada 5 min**.

## 🔛 Ativando o bot num canal (por comandos!)

1. Adicione o bot como **administrador** do canal (permissão de publicar)
2. Poste **`/ativar`** dentro do canal → pronto, ele começa a postar! 🔥

Alternativa pelo privado do bot: `/ativar @seucanal`

Para parar: `/desativar` (no canal ou no privado).

💡 O `/puxar` também funciona postado direto no canal/grupo:
`/puxar 5` → publica na hora as 5 promoções mais quentes do momento.

> 🔒 **Dono do bot:** o primeiro usuário que falar com o bot no privado vira
> o dono — só ele pode usar os comandos de configuração. Então mande um
> `/start` para o bot logo após o deploy!

## ⚙️ Comandos de configuração (no privado do bot)

| Comando | O que faz |
|---|---|
| `/status` | Mostra canal ativo, filtros e estatísticas |
| `/puxar 3` | Publica as 3 promos mais quentes do momento 🔥 (1 a 10) |
| `/autopuxar on` | 🤖 Posta 1 promo quente a cada **1 min** (nunca repete) |
| `/autopuxar 5` | Mesma coisa, a cada 5 min (1 a 60) |
| `/autopuxar off` | Desliga o puxa automático |
| `/testar` | Publica a oferta mais recente no canal agora |
| `/cupons` | Lista os cupons ativos do momento (no chat) 🎟️ |
| `/cupons amazon` | Cupons de uma loja (amazon, shopee, mercado-livre…) |
| `/puxarcupons 3` | Publica 3 cupons no canal (aceita loja: `/puxarcupons 3 amazon`) |
| `/autocupons on` | Liga a postagem automática de cupons novos |
| `/intervalo 15` | Busca ofertas a cada 15 min (padrão 10) |
| `/maxposts 3` | Máx. de posts por ciclo (padrão 5) |
| `/desconto 30` | Só posta ofertas com 30%+ de desconto |
| `/bloquear capinha,película` | Ignora ofertas com esses termos |
| `/lojas amazon,kabum,aliexpress` | Só posta ofertas dessas lojas |
| `/lojas add aliexpress` | Adiciona uma loja à lista (sem apagar as outras) |
| `/lojas remover shopee` | Remove uma loja da lista |
| `/limparfiltros` | Remove todos os filtros |
| `/desativar` | Para de postar no canal |
| `/id` | Mostra o chat_id da conversa |

## 🩺 Monitoramento

Abra a URL do serviço no navegador: JSON com status (canal ativo, tempo
online, ciclos, ofertas postadas). Logs completos na aba **Logs** do Render.

## ⚠️ Observações do plano free

- O disco é **efêmero**: se o serviço reiniciar (deploy novo, manutenção),
  as configurações feitas por comando podem zerar — basta mandar `/ativar`
  de novo no canal. O bot nunca inunda o canal após reiniciar (posta no
  máximo 2 ofertas na primeira execução).
- 750 h/mês grátis — dá para 1 serviço o mês inteiro.

## 🔐 Segurança

Se o token vazar, revogue no [@BotFather](https://t.me/BotFather) com
`/revoke` e atualize a env `BOT_TOKEN` no Render.
