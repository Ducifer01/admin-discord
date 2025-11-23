import discord
from discord.ext import commands
from config_loader import config_manager
from discord.utils import utcnow
import datetime

class BanCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Carrega config específica da cog ban
        self.config = config_manager.load_cog("ban")
        self.ban_cfg = self.config.get("ban", {})
        self.embed_cfg = self.config.get("embed_settings", {})
        # Rastreamento de bans disparados via comando para evitar log duplicado
        self._recent_command_bans: set[int] = set()

    def refresh_config(self):
        """Recarrega a configuração do JSON para refletir mudanças sem reiniciar o bot."""
        try:
            self.config = config_manager.reload_cog("ban")
        except Exception:
            # Se falhar reload, mantém anterior
            pass
        self.ban_cfg = self.config.get("ban", {})
        self.embed_cfg = self.config.get("embed_settings", {})

    def _color(self, key: str, fallback: str) -> int:
        hex_str = self.embed_cfg.get("colors", {}).get(key, fallback)
        try:
            return int(hex_str, 16)
        except ValueError:
            return int(fallback, 16)

    def build_embed(self, tipo: str, user: discord.abc.User, moderador: discord.abc.User, motivo: str, terceirizado: bool = False, executor_text: str | None = None):
        """Monta embed estilizado baseado em config.
        tipo: 'ban' ou 'unban'
        """
        title_key = "title_ban" if tipo == "ban" else "title_unban"
        title = self.embed_cfg.get(title_key, "Ban")
        if tipo == 'ban' and terceirizado:
            # Adiciona marcação se foi feito por outro meio (externo ao comando do bot)
            title = f"{title} (Terceirizado)"
        color = self._color(tipo, "FF0000" if tipo == "ban" else "00FF7F")
        use_ts = self.embed_cfg.get("use_timestamp", True)
        motivo_cb = self.embed_cfg.get("motivo_codeblock", True)
        show_ids = self.ban_cfg.get("show_ids", True)
        embed = discord.Embed(title=title, color=color)
        if use_ts:
            embed.timestamp = utcnow()

        # Nome formatado com mention + id
        user_field = f"{user.mention} | {user.id}" if show_ids else user.mention
        mod_field = f"{moderador.mention} | {moderador.id}" if show_ids else moderador.mention
        embed.add_field(name="Usuário:", value=user_field, inline=True)
        embed.add_field(name="Moderador:", value=mod_field, inline=True)
        if executor_text:
            embed.add_field(name="Origem:", value=executor_text, inline=False)
        motivo_fmt = f"```{motivo}```" if motivo_cb else motivo
        embed.add_field(name="Motivo:", value=motivo_fmt, inline=False)

        thumb = self.embed_cfg.get("thumbnail_url")
        if thumb:
            embed.set_thumbnail(url=thumb)
        banner = self.embed_cfg.get("banner_url")
        if banner:
            embed.set_image(url=banner)

        footer_text = self.embed_cfg.get("footer_text", "Sistema de Moderação")
        footer_icon = self.embed_cfg.get("footer_icon_url") or None
        embed.set_footer(text=footer_text, icon_url=footer_icon)
        return embed

    def has_higher_role(self, ctx: commands.Context, member: discord.Member):
        return ctx.author.top_role > member.top_role

    async def send_notifications(self, embed, ctx: commands.Context, is_castigo=False):
        notifications = self.config.get("notifications", {})
        if not notifications:
            return
        if (is_castigo and not notifications.get("enviar_dm_castigos")) or (not is_castigo and not notifications.get("enviar_dm_banidos")):
            return

        user_ids = set(notifications.get("user_notificados", []))
        role_ids = notifications.get("cargos_notificados", [])

        for member in ctx.guild.members:
            if any(role.id in role_ids for role in member.roles):
                user_ids.add(member.id)

        for user_id in user_ids:
            try:
                user = await self.bot.fetch_user(user_id)
                await user.send(embed=embed)
            except (discord.Forbidden, discord.NotFound):
                continue

    @commands.command(name="ban")
    async def ban(self, ctx: commands.Context, membro: discord.Member, *, motivo: str = "Nenhum motivo especificado"):
        # Recarrega config para garantir atualização via JSON
        self.refresh_config()
        authorized_roles = self.ban_cfg.get("authorized_roles", [])
        log_channel_id = self.ban_cfg.get("log_channel_id", 0)
        delete_delay = self.embed_cfg.get("delete_message_delay", 8)

        if not any(role.id in authorized_roles for role in ctx.author.roles):
            await self.bot.send_no_permission(ctx)
            return

        if not self.has_higher_role(ctx, membro):
            msg = await ctx.send("Você não pode banir esse membro, pois seu cargo é igual ou inferior")
            await msg.delete(delay=delete_delay)
            return

        try:
            await membro.ban(reason=motivo)
            # Registra para evitar log duplicado no listener on_member_ban
            self._recent_command_bans.add(membro.id)
            embed = self.build_embed("ban", membro, ctx.author, motivo, terceirizado=False)
            sent = await ctx.send(embed=embed)
            try:
                await sent.delete(delay=delete_delay)
            except Exception:
                pass
            log_channel = self.bot.get_channel(log_channel_id)
            if log_channel:
                await log_channel.send(embed=embed)
            await self.send_notifications(embed, ctx, is_castigo=False)

        except discord.Forbidden:
            msg = await ctx.send("Eu não tenho permissão para banir esse usuário")
            await msg.delete(delay=delete_delay)
        except Exception as e:
            msg = await ctx.send(f"Erro ao aplicar banimento: {str(e)}")
            await msg.delete(delay=delete_delay)

    @commands.command(name="unban")
    async def unban(self, ctx: commands.Context, id_membro: int, *, motivo: str = "Nenhum motivo especificado"):
        # Recarrega config para garantir atualização via JSON
        self.refresh_config()
        authorized_roles = self.ban_cfg.get("authorized_roles", [])
        log_channel_id = self.ban_cfg.get("log_channel_id", 0)
        delete_delay = self.embed_cfg.get("delete_message_delay", 8)

        if not any(role.id in authorized_roles for role in ctx.author.roles):
            await self.bot.send_no_permission(ctx)
            return

        try:
            async for ban_entry in ctx.guild.bans():
                if ban_entry.user.id == id_membro:
                    await ctx.guild.unban(ban_entry.user, reason=motivo)

                    embed = self.build_embed("unban", ban_entry.user, ctx.author, motivo)
                    sent = await ctx.send(embed=embed)
                    try:
                        await sent.delete(delay=delete_delay)
                    except Exception:
                        pass
                    log_channel = self.bot.get_channel(log_channel_id)
                    if log_channel:
                        await log_channel.send(embed=embed)
                    await self.send_notifications(embed, ctx, is_castigo=False)
                    return

            msg = await ctx.send("O usuário especificado não está banido")
            await msg.delete(delay=delete_delay)

        except discord.Forbidden:
            msg = await ctx.send("Eu não tenho permissão para desbanir esse usuário")
            await msg.delete(delay=delete_delay)
        except Exception as e:
            msg = await ctx.send(f"Erro ao remover banimento: {str(e)}")
            await msg.delete(delay=delete_delay)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        """Captura qualquer banimento, inclusive externo.
        - Ignora se foi ban via comando recente (para evitar duplicação)
        - Marca como terceirizado se executor não é o bot.
        """
        if not guild or not user:
            return
        # Se foi pelo comando (executor == bot) e está no set, removemos e ignoramos
        if user.id in self._recent_command_bans:
            self._recent_command_bans.discard(user.id)
            return
        log_channel_id = self.ban_cfg.get("log_channel_id", 0)
        if not log_channel_id:
            return
        log_channel = self.bot.get_channel(log_channel_id)
        if not isinstance(log_channel, discord.TextChannel):
            return
        executor = None
        if guild.me.guild_permissions.view_audit_log:
            # Procura executor no audit log recente
            now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
            async for entry in guild.audit_logs(limit=6, action=discord.AuditLogAction.ban):
                if entry.target and entry.target.id == user.id:
                    created = entry.created_at
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=datetime.timezone.utc)
                    if (now - created).total_seconds() <= 8:  # janela fixa
                        executor = entry.user
                        break
        terceirizado = executor is not None and executor.id != self.bot.user.id
        moderador = executor or self.bot.user
        origem_text = 'Ban externo (terceirizado)' if terceirizado else 'Ban origem desconhecida'
        embed = self.build_embed('ban', user, moderador, motivo='(motivo não disponível - ban externo)', terceirizado=terceirizado, executor_text=origem_text)
        try:
            await log_channel.send(embed=embed)
        except Exception:
            pass

async def setup(bot):
    await bot.add_cog(BanCog(bot))