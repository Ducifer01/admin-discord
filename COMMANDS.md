# Documentação de Comandos

Este documento descreve os comandos disponíveis no bot, suas permissões, finalidade e exemplos de uso. Ele também lista parâmetros de configuração relevantes nos arquivos JSON em `config/cogs/`.

> Prefixo padrão (configurável em `.env` via `BOT_PREFIX`): `!`
> Slash commands podem demorar para propagar globalmente. Use `GUILD_ID` para sync rápido local.

---
## Sumário
- [Moderação: Ban](#moderação-ban)
- [Moderação: Castigo (Timeout)](#moderação-castigo-timeout)
- [Moderação: Busca de Membro em Call](#moderação-busca-de-membro-em-call)
- [Utilitário: Limpar Chat](#utilitário-limpar-chat)
- [Sincronização Cargo de Mutado em Call](#sincronização-cargo-de-mutado-em-call)
- [Proteção: AntiBot](#proteção-antibot)
- [Automod: AntiSpam/AntiFlood](#automod-antispamantiflood)
- [Proteção: Anti-Raid](#proteção-anti-raid)
- [Automod: NoMention](#automod-nomention)
- [Proteção: AntiBot](#proteção-antibot)
- [Configuração via JSON](#configuração-via-json)
- [Recarregando Cogs](#recarregando-cogs)

---
## Moderação: Ban
**Prefix Commands**:
- `!ban @membro [motivo]`
  - Bane um membro do servidor.
- `!unban <id_usuario> [motivo]`
  - Remove banimento de um usuário pelo ID numérico.

**Permissões**: Controladas por IDs de cargos em `config/cogs/ban.json -> ban.authorized_roles`.

**Config Chave** (`ban.json`):
```json
{
  "ban": {"authorized_roles": [123], "log_channel_id": 456},
  "embed_settings": {"delete_message_delay": 8, "colors": {"ban": "FF0000", "unban": "00FF7F"}, "footer_text": "Sistema de Moderação"},
  "notifications": {"enviar_dm_banidos": true, "user_notificados": [], "cargos_notificados": []}
}
```

**Exemplo**: `/banir @Fulano Spam no chat`

---
## Moderação: Castigo (Timeout)
**Prefix Commands**:
- `!castigo @membro <duração> [motivo]` ex: `!castigo @Fulano 5m spam`
  - Aplica timeout (mute global de mensagens) por período.
- `!removercastigo @membro [motivo]`
  - Remove timeout ativo.

**Formato duração**: `<n><s|m|d>` (segundos, minutos, dias). Exemplos: `30s`, `5m`, `2d`.

**Permissões**: `config/cogs/castigo.json -> castigo.authorized_roles`.

**Config Chave** (`castigo.json`):
```json
{
  "castigo": {"authorized_roles": [123], "log_channel_id": 456},
  "embed_settings": {"delete_message_delay": 8, "colors": {"castigo": "FFA500", "remove_castigo": "1E90FF"}, "footer_text": "Sistema de Moderação"},
  "notifications": {"enviar_dm_castigos": true}
}
```

**Exemplo**: `/castigo @Fulano 5m Flood de emojis`

---
## Moderação: Busca de Membro em Call
**Prefix Command**:
- `!buscarmembro @membro`
  - Indica se o membro está em canal de voz e qual.

**Permissões**: `config/cogs/buscarmembro.json -> buscarmembro.authorized_roles`.

**Mensagens** personalizáveis (`mensagens`): `bot`, `sem_permissao`, `encontrado`, `nao_encontrado`.

**Exemplo**: `/buscarmembro @DJ` → retorna call atual ou ausência.

---
## Utilitário: Limpar Chat
**Prefix Command**:
- `!clearchat <quantidade>`
  - Limpa até `quantidade` mensagens do canal de texto associado à call do autor (via tópico) ou o canal atual.

**Permissões**: Requer permissão nativa `manage_messages` + regras do JSON.

**Config** (`clearchat.json`):
```json
{
  "clearchat": {
    "max_messages": 999,
    "delete_delay": 5,
    "require_voice": true,
    "messages": {"need_amount": "...", "range_error": "..."}
  }
}
```
`require_voice`: força usuário a estar em canal de voz.

**Exemplo**: `!clearchat 50`

---
## Sincronização / Mute de Call
Cog: `mod_mutecall` (processo automático + comandos manual).

**Função**: Garante que usuários com mute aplicado pelo servidor tenham o cargo definido e adiciona comandos para mutar/desmutar temporariamente.
**Prefix Commands**:
- `!mutecall @membro <duração> [motivo]` — Aplica server mute e cargo de mutado, agenda remoção automática.
- `!unmutecall @membro [motivo]` — Remove mute antes do fim.

**Config** (`mutecall.json`):
```json
{
  "mutecall": {
    "muted_role_id": 0,
    "interval_seconds": 10,
    "batch_sleep_every": 10,
    "batch_sleep_seconds": 1,
    "enable_debug": false
  }
}
```
- `muted_role_id`: ID do cargo a aplicar quando mutado no servidor.
- `batch_sleep_every`: pausa após processar N membros (rate limit friendly).
- `batch_sleep_seconds`: duração da pausa.
- `enable_debug`: logs adicionais se `true`.

**Eventos Monitorados**:
- `on_ready`: sincronização inicial.
- Loop periódico (a cada 10s): revalida estados.
- `on_voice_state_update`: atualização imediata.
- `on_member_update`: reatribui cargo removido indevidamente se ainda mutado.

---
## Proteção: AntiBot
Cog: `protect_antibot`

**Função**: Bloqueia automaticamente bots adicionados sem autorização. Verifica se o bot recém-entrado ou quem o convidou está em whitelist; caso negativo, executa `kick` ou `ban` conforme configurado.

**Eventos Monitorados**:
- `on_member_join` (apenas se `member.bot` verdadeiro).
- Audit Log `bot_add` para identificar o usuário que adicionou o bot.

**Prefix Commands**:
- `!antibotreload` — Recarrega o JSON desta proteção.
- `!antibotstatus` — Mostra resumo (ação, canal de log, listas de whitelist).

**Permissão para comandos**: Usuário precisa `manage_guild`.

**Config** (`protect_antibot.json`):
```json
{
  "protect_antibot": {
    "enabled": true,
    "action": "kick",
    "log_channel_id": 0,
    "whitelist_bot_ids": [],
    "whitelist_inviter_ids": [],
    "whitelist_inviter_role_ids": [],
    "block_if_missing_inviter": true,
    "dm_inviter": true,
    "dm_inviter_message": "Você não pode adicionar bots ao servidor.",
    "dm_delete_delay": 8,
    "reason_template": "Bot não autorizado: {bot} (ID {bot_id}) adicionado por {inviter}.",
    "messages": {
      "log_action": "{action_title}: {bot} ({bot_id}) ação={action} por {inviter}",
      "log_fail": "Falha ao {action} bot {bot} ({bot_id}): {error}",
      "status_header": "Proteção AntiBot — resumo",
      "status_main": "Habilitado: {enabled} | Ação: {action} | Canal log: {log_channel_id}",
      "status_whitelists": "Bots permitidos: {bots} | Inviters permitidos: {users} | Roles inviters: {roles}",
      "dm_inviter": "{user}, você não está autorizado(a) a adicionar bots aqui.",
      "log_missing_inviter": "Não foi possível determinar o autor do add do bot {bot_id}."
    },
    "log_embed": {
      "enabled": true,
      "color": "FF8800",
      "title_action": "Bot bloqueado",
      "title_fail": "Falha AntiBot",
      "title_allowed": "Bot permitido (whitelist)"
    },
    "debug": false
  }
}
```

**Boas práticas**:
- Defina `log_channel_id` para auditar ações.
- Use whitelist de cargos para delegar quem pode convidar bots (ex: equipe de desenvolvimento).
- Mantenha o cargo do bot com permissão de `Kick Members` (e `Ban Members` se usar ban).
- Ative `debug` apenas para testes (gera mensagens extras).

**Exemplo**: Usuário não autorizado adiciona um bot → bot entra, cog detecta, executa kick, loga embed.

---
## Automod: AntiSpam/AntiFlood
Cog: `automod_spam`

**Função**: Detecta e mitiga padrões de spam/flood no chat:
- Flood: muitas mensagens em poucos segundos.
- Repetição: mesmo conteúdo repetido várias vezes.
- Menções excessivas: @users/@roles/@everyone/@here acima do limite.
- Emojis excessivos (custom + unicode heurístico).
- Abuso de CAPS (percentual de letras maiúsculas numa mensagem longa).

**Ações suportadas** (`action`):
- `delete`: apenas deleta a mensagem.
- `delete_warn`: deleta e envia aviso temporário.
- `delete_punish`: deleta, avisa e aplica `timeout` (configurado em `punishment`).

**Config** (`automod_spam.json`):
```json
{
  "automod_spam": {
    "enabled": true,
    "debug": false,
    "log_channel_id": 0,
    "action": "delete_warn",
    "punishment": {"type": "timeout", "duration_seconds": 300, "reason": "Spam/Flood no chat"},
    "thresholds": {
      "flood_messages": 6,
      "flood_interval_seconds": 5,
      "repeat_same_content": 3,
      "repeat_interval_seconds": 12,
      "max_mentions": 6,
      "max_emojis": 15,
      "caps_ratio_trigger": 0.7,
      "min_caps_length": 15
    },
    "ignore": {"channel_ids": [], "user_ids": [], "role_ids": []},
    "warn": {"message": "{user} detectado spam/flood: {reason}", "delete_delay": 6, "dm_user": false},
    "messages": {"log_violation": "Spam/Flood: {user} tipo={type} razão={reason}"}
  }
}
```

**Comandos**:
- `!automodspamreload` — Recarrega a config.
- `!automodspamstatus` — Mostra limites atuais.

**Boas práticas**:
- Ajuste `flood_messages` e `flood_interval_seconds` conforme atividade normal do servidor.
- Use `ignore.channel_ids` para canais de spam liberado.
- Aumente `repeat_same_content` se usuários legítimos repetem comandos.
- Ratio de CAPS muito baixo gera falso positivo; 0.7 é razoável.

**Exemplo**: Usuário envia 6 mensagens em 3s → ação configurada aplicada.

---
## Proteção: Anti-Raid
Cog: `anti_raid`

**Objetivo**: Detectar ataques de raid (entrada massiva de contas) e ativar automaticamente um modo emergência com medidas de mitigação.

**Detecção** (config `anti_raid.json -> detection`):
- `join_threshold_count` + `join_threshold_interval_seconds`: Se X usuários entrarem em Y segundos.
- `min_account_age_hours_flag` + `flagged_join_threshold_count`: Variante considerando apenas contas jovens (<= idade).
- `sliding_window_seconds`: Janela maior para manter histórico (para status / debug).

**Ações em Modo Emergência** (`emergency`):
- Revogar convites (`revoke_invites`).
- Aplicar slowmode (`apply_slowmode`, `slowmode_seconds`) em todos os canais de texto ou apenas IDs específicos.
- Timeout automático em novos membros jovens (`timeout_newcomers`, `timeout_account_age_hours_max`, `timeout_duration_seconds`).
- Notificação embed para canal (`notify_channel_id`) + ping de cargos/usuários (`notify_ping_role_ids`, `notify_ping_user_ids`).
- Auto desativação após `auto_disable_seconds`.
- Reversão de slowmode para `revert_slowmode_seconds` (0 desativa) ao terminar.

**Comandos**:
- `!antiraidstatus` — Mostra se emergência está ativa e contagens recentes.
- `!antiraidactivate` — Ativa manualmente (requer permissão conforme `manual` + `manage_guild`).
- `!antiraiddeactivate` — Desativa manualmente.
- `!antiraidreload` — Recarrega config.

**Permissões Manuais** (`manual`):
- `command_whitelist_user_ids` / `command_whitelist_role_ids` especificam quem pode usar comandos de activate/deactivate.
- Se listas vazias, cai em checagem de `require_manage_guild`.

**Config Exemplo** (`anti_raid.json`):
```json
{
  "anti_raid": {
    "enabled": true,
    "detection": {
      "join_threshold_count": 10,
      "join_threshold_interval_seconds": 30,
      "min_account_age_hours_flag": 12,
      "flagged_join_threshold_count": 5,
      "sliding_window_seconds": 120
    },
    "emergency": {
      "auto_disable_seconds": 600,
      "apply_slowmode": true,
      "slowmode_seconds": 8,
      "apply_slowmode_channel_ids": [],
      "apply_slowmode_all_text": true,
      "revert_slowmode_seconds": 0,
      "timeout_newcomers": true,
      "timeout_duration_seconds": 900,
      "timeout_account_age_hours_max": 72,
      "revoke_invites": true,
      "recreate_invites_after": false,
      "notify_channel_id": 0,
      "notify_ping_role_ids": [],
      "notify_ping_user_ids": [],
      "notify_embed": {"enabled": true, "color": "FF0000", "title_activate": "⚠️ Modo Emergência Anti-Raid Ativado", "title_deactivate": "✅ Modo Emergência Desativado"}
    },
    "manual": {"command_whitelist_user_ids": [], "command_whitelist_role_ids": [], "require_manage_guild": true},
    "messages": {
      "status_header": "Anti-Raid Status",
      "status_values": "Emergência: {active} | Entradas janela: {joins} | Flagged: {flagged} | Threshold: {threshold}/{interval}s",
      "activated_reason": "Ativado automaticamente: {count} entradas em {interval}s",
      "manual_activate": "Modo emergência ativado manualmente por {user}.",
      "manual_deactivate": "Modo emergência desativado manualmente por {user}.",
      "timeout_reason": "Modo emergência anti-raid (conta jovem)",
      "invite_revoke_fail": "Falha ao revogar convites: {error}"
    },
    "log_channel_id": 0,
    "debug": false
  }
}
```

**Dicas**:
- Ajuste `join_threshold_count` conforme tamanho médio do servidor.
- Se muitos falsos positivos de contas novas, aumente `min_account_age_hours_flag` ou `flagged_join_threshold_count`.
- Use canal dedicado para notificações (`notify_channel_id`) com permissão restrita.
- Mantenha o bot com permissões: Manage Server (para convites), Manage Channels (slowmode) e Timeout Members.

**Exemplo**: 12 contas entram em 25s → emergência ativa → convites revogados, slowmode 8s aplicado, novos usuários jovens recebem timeout.

---
## Automod: NoMention
Cog: `automod_nomention`

**Função**: Bloqueia menções não autorizadas a @everyone, @here, cargos específicos, qualquer cargo (modo genérico) ou usuários sensíveis (IDs), com opção de punição (timeout).

**Config** (`automod_nomention.json`):
```json
{
  "automod_nomention": {
    "enabled": true,
    "debug": false,
    "log_channel_id": 0,
    "action": "delete_warn",
    "punishment": {"type": "timeout", "duration_seconds": 600, "reason": "Menção proibida"},
    "blocked": {
      "role_ids": [],
      "block_everyone": true,
      "block_here": true,
      "block_role_mentions": true,
      "block_user_ids": []
    },
    "exempt": {"roles": [], "users": [], "manage_messages_bypass": true},
    "warn": {"message": "{user} menção não permitida: {reason}", "delete_delay": 6, "dm_user": false},
    "messages": {"log_violation": "NoMention: {user} tipo={type} razão={reason}"}
  }
}
```

**Ações (`action`)**:
- `delete`: apaga a mensagem.
- `delete_warn`: apaga e envia aviso temporário.
- `delete_punish`: apaga, avisa e aplica punição (`timeout`).

**Comandos**:
- `!automodnomentionreload` — Recarrega config.
- `!automodnomentionstatus` — Mostra regras atuais.

**Boas práticas**:
- Se quiser permitir menção de alguns cargos, deixe `block_role_mentions=false` e use apenas `role_ids` específicos.
- Inclua cargos de moderação em `exempt.roles` para evitar punições indevidas.
- Ajuste `duration_seconds` do timeout conforme severidade.

**Exemplo**: Usuário sem permissão menciona @everyone → mensagem deletada, aviso enviado e timeout aplicado (se `delete_punish`).

---
## Configuração via JSON
Todos os arquivos vivem em `config/cogs/`.
- Edite IDs numéricos com valores reais do seu servidor.
- Cores são hex sem `#`.
- Mensagens podem conter placeholders `{user}`, `{target}`, `{channel}`, `{max}`, `{amount}`, `{deleted}` conforme a cog.

Após editar: reinicie o bot ou recarregue a cog específica (exemplo abaixo).

---
## Recarregando Cogs
No contexto do bot (por exemplo via console interativo ou comando próprio futuro):
```python
await bot.reload_extension('cogs.mod_ban')
await bot.reload_extension('cogs.mod_castigo')
await bot.reload_extension('cogs.tolls_buscarmembro')
await bot.reload_extension('cogs.tolls_clearchat')
await bot.reload_extension('cogs.mod_mutecall')
```

---
## Erros Comuns & Soluções
| Situação | Causa | Solução |
|----------|-------|---------|
| Slash command não aparece | Sync global demora | Defina `GUILD_ID` e `sync_commands_guild_only=true` em `global.json` |
| "Você não tem permissão" | Cargo não listado | Adicione o ID do cargo autorizado no JSON da cog |
| Embed não deleta | `delete_message_delay` muito baixo ou permissão ausente | Verifique permissão `manage_messages` do bot e valor em JSON |
| Cargo de mutado não aplica | `muted_role_id` incorreto ou acima do cargo do bot | Corrija o ID e reordene cargos no servidor |
| Rate limit | Servidor grande + loop rápido | Ajuste `batch_sleep_every` e `batch_sleep_seconds` |

---
## Boas Práticas
- Mantenha `muted_role_id` sempre abaixo do cargo do bot.
- Não use cores excessivamente escuras (melhor contraste).
- Faça backup dos JSON antes de grandes mudanças.
- Evite colocar IDs de cargos inexistentes.

---
## Próximas Extensões Possíveis
- Comando administrativo `/reloadconfig <cog>`.
- Sistema de warnings com escalonamento automático.
- Painel web para editar JSON.

---
Última atualização: 2025-11-22
