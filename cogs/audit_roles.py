import datetime
import discord
from discord.ext import commands
from typing import Dict, Any, List, Tuple
from config_loader import config_manager

DEFAULTS = {
    "audit_roles": {
        "enabled": True,
        "log_channel_id": 0,
        "audit_window_seconds": 5,
        "log_embed": {
            "enabled": True,
            "color": "FFAA33",
            "title_roles": "Mudança de cargos",
            "title_role_perms": "Permissões de cargo"
        },
        "options": {
            "log_member_roles_change": True,
            "log_role_permissions_change": True
        },
        "messages": {
            "roles_added": "{executor} adicionou cargos {roles} ao {target}",
            "roles_removed": "{executor} removeu cargos {roles} de {target}",
            "roles_changed_unknown": "Cargos mudaram em {target} (executor desconhecido) adicionados: {added} removidos: {removed}",
            "role_perms_changed": "{executor} modificou permissões do cargo {role}",
            "role_perms_changed_unknown": "Permissões modificadas em {role} (executor desconhecido)",
            "status_header": "Auditoria cargos",
            "status_main": "Habilitado: {enabled} | Janela: {window}s",
            "status_channel": "Canal log: {channel}",
            "status_opts": "Log member roles: {member_roles} | Log role perms: {role_perms}"
        },
        "debug": False
    }
}

class AuditRolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.raw_cfg = config_manager.load_cog('audit_roles', defaults=DEFAULTS)
        self.cfg = self.raw_cfg.get('audit_roles', {})
        self.enabled: bool = self.cfg.get('enabled', True)
        self.log_channel_id: int = self.cfg.get('log_channel_id', 0)
        self.window: int = self.cfg.get('audit_window_seconds', 5)
        self.embed_cfg: Dict[str, Any] = self.cfg.get('log_embed', {})
        self.msgs: Dict[str, str] = self.cfg.get('messages', {})
        self.opts: Dict[str, Any] = self.cfg.get('options', {})
        self.debug: bool = self.cfg.get('debug', False)

    def refresh_config(self):
        self.raw_cfg = config_manager.reload_cog('audit_roles')
        self.__init__(self.bot)

    async def _log(self, guild: discord.Guild, title: str, lines: List[Tuple[str,str,bool]]):
        if not self.log_channel_id:
            return
        ch = guild.get_channel(self.log_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return
        if self.embed_cfg.get('enabled', True):
            color_hex = self.embed_cfg.get('color', 'FFAA33')
            try:
                color_val = int(str(color_hex), 16)
            except Exception:
                color_val = int('FFAA33', 16)
            embed = discord.Embed(title=title, color=color_val)
            for n,v,i in lines:
                embed.add_field(name=n, value=v, inline=i)
            try:
                await ch.send(embed=embed)
            except Exception:
                pass
        else:
            try:
                txt = title + '\n' + '\n'.join(f"{n}: {v}" for n,v,_ in lines)
                await ch.send(txt)
            except Exception:
                pass

    async def _find_executor(self, guild: discord.Guild, target: discord.Member | discord.Role, action: discord.AuditLogAction) -> discord.User | None:
        if not guild.me.guild_permissions.view_audit_log:
            return None
        now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
        async for entry in guild.audit_logs(limit=10, action=action):
            if entry.target and getattr(entry.target, 'id', None) == target.id:
                created = entry.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=datetime.timezone.utc)
                if (now - created).total_seconds() <= self.window:
                    return entry.user
        return None

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not self.enabled or not self.opts.get('log_member_roles_change', True):
            return
        before_roles = set(r.id for r in before.roles)
        after_roles = set(r.id for r in after.roles)
        if before_roles == after_roles:
            return
        added_ids = after_roles - before_roles
        removed_ids = before_roles - after_roles
        if not added_ids and not removed_ids:
            return
        added_mentions = [after.guild.get_role(rid).mention for rid in added_ids if after.guild.get_role(rid)]
        removed_mentions = [after.guild.get_role(rid).mention for rid in removed_ids if after.guild.get_role(rid)]
        executor = await self._find_executor(after.guild, after, discord.AuditLogAction.member_role_update)
        title = self.embed_cfg.get('title_roles', 'Mudança de cargos')
        if executor:
            if added_mentions:
                msg_add = self.msgs.get('roles_added', '{executor} adicionou cargos {roles} ao {target}').format(executor=executor.mention, roles=', '.join(added_mentions), target=after.mention)
                await self._log(after.guild, title, [('Evento', msg_add, False)])
            if removed_mentions:
                msg_rem = self.msgs.get('roles_removed', '{executor} removeu cargos {roles} de {target}').format(executor=executor.mention, roles=', '.join(removed_mentions), target=after.mention)
                await self._log(after.guild, title, [('Evento', msg_rem, False)])
        else:
            msg = self.msgs.get('roles_changed_unknown', 'Cargos mudaram em {target} (executor desconhecido) adicionados: {added} removidos: {removed}').format(target=after.mention, added=', '.join(added_mentions) or '(nenhum)', removed=', '.join(removed_mentions) or '(nenhum)')
            await self._log(after.guild, title, [('Evento', msg, False)])

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        if not self.enabled or not self.opts.get('log_role_permissions_change', True):
            return
        if before.permissions != after.permissions or before.name != after.name or before.color != after.color or before.hoist != after.hoist or before.mentionable != after.mentionable:
            executor = await self._find_executor(after.guild, after, discord.AuditLogAction.role_update)
            title = self.embed_cfg.get('title_role_perms', 'Permissões de cargo')
            if executor:
                msg = self.msgs.get('role_perms_changed', '{executor} modificou permissões do cargo {role}').format(executor=executor.mention, role=after.mention)
            else:
                msg = self.msgs.get('role_perms_changed_unknown', 'Permissões modificadas em {role} (executor desconhecido)').format(role=after.mention)
            await self._log(after.guild, title, [('Evento', msg, False)])

    @commands.command(name='rolesauditreload')
    async def roles_reload(self, ctx: commands.Context):
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.reply('Sem permissão.')
        self.refresh_config()
        await ctx.reply('Config audit_roles recarregada.')

    @commands.command(name='rolesauditstatus')
    async def roles_status(self, ctx: commands.Context):
        m = self.msgs
        ch = ctx.guild.get_channel(self.log_channel_id)
        lines = [m.get('status_header', 'Auditoria cargos')]
        lines.append(m.get('status_main', '').format(enabled=self.enabled, window=self.window))
        lines.append(m.get('status_channel', '').format(channel=ch.mention if ch else '(não definido)'))
        lines.append(m.get('status_opts', '').format(member_roles=self.opts.get('log_member_roles_change', True), role_perms=self.opts.get('log_role_permissions_change', True)))
        await ctx.reply('\n'.join(lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(AuditRolesCog(bot))
