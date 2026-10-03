# 🤖 Bot de Promoções — versão Render (configuração por comandos)

Bot de Telegram que coleta ofertas do **Promobit** (Amazon, Magalu, Shopee,
Mercado Livre, Kabum…) e publica automaticamente no seu canal.

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
| `/testar` | Publica a oferta mais recente no canal agora |
| `/intervalo 15` | Busca ofertas a cada 15 min (padrão 10) |
| `/maxposts 3` | Máx. de posts por ciclo (padrão 5) |
| `/desconto 30` | Só posta ofertas com 30%+ de desconto |
| `/bloquear capinha,película` | Ignora ofertas com esses termos |
| `/lojas amazon,kabum` | Só posta ofertas dessas lojas |
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
