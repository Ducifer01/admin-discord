# BotServerNoBody

Bot de moderação em Discord com arquitetura modular de *cogs*, cada uma com seu próprio arquivo de configuração JSON e variáveis sensíveis em `.env`.

## Estrutura
```
config/
  global.json               # Configurações gerais do bot
  cogs/
    ban.json                # Config da cog de banimentos
    castigo.json            # Config da cog de castigos (timeout)
    buscarmembro.json       # Config da cog de busca de membro em call
    clearchat.json          # Config da cog de limpeza de chat
config_loader.py            # Utilitário central de carregamento de configs e .env
bot.py                      # Inicialização e carregamento automático das cogs
cogs/                       # Cogs (comandos)
.tests/                     # Testes unitários simples
```

## Pré-requisitos
- Python 3.10+
- Criar `.env` baseado em `.env.example`.

Exemplo de `.env`:
```
DISCORD_TOKEN=COLOQUE_SEU_TOKEN_AQUI
BOT_PREFIX=!
OWNER_ID=000000000000000000
GUILD_ID=000000000000000000
```

## Instalação
```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Executar o Bot
```powershell
python bot.py
```
Se `GUILD_ID` estiver definido e `sync_commands_guild_only = true` em `global.json`, os *slash commands* serão sincronizados apenas para aquela guild (mais rápido). Caso contrário, serão globais (podem levar até 1h para propagar).

## Personalização de Cogs
Cada cog possui seu JSON em `config/cogs/`. Ajuste IDs de cargos (`authorized_roles`), canais de log (`log_channel_id`), cores e mensagens. Cores são hex sem `#`.

Após editar um JSON você pode recarregar a cog manualmente no console do bot:
```python
await bot.reload_extension('cogs.mod_ban')
```

## Testes
```powershell
python -m unittest discover -s tests
```

## Extensão
Para adicionar uma nova cog:
1. Criar `cogs/minha_cog.py` com função `async def setup(bot)`.
2. Criar `config/cogs/minha_cog.json` com a estrutura necessária.
3. Reiniciar o bot (carregamento automático) ou usar `await bot.load_extension('cogs.minha_cog')`.

## Segurança
Nunca commitar `.env` real. Use apenas `.env.example`. Tokens e IDs sensíveis ficam no `.env`.

## Próximos Passos
- Adicionar logs estruturados para ações de moderação.
- Internacionalização (i18n) por arquivo de idioma.
- Sistema de permissões avançadas em JSON.

---
Feito com discord.py 2.x.
