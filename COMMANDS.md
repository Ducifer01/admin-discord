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
