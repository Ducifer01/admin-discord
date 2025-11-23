import discord
from discord.ext import commands
import datetime
import re
from config_loader import config_manager

from discord.utils import utcnow

class CastigoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Carrega config específica da cog castigo
        self.config = config_manager.load_cog("castigo")
        self.castigo_cfg = self.config.get("castigo", {})
        self.embed_cfg = self.config.get("embed_settings", {})

    def refresh_config(self):
        try:
            self.config = config_manager.reload_cog("castigo")
        except Exception:
            pass
        self.castigo_cfg = self.config.get("castigo", {})
        self.embed_cfg = self.config.get("embed_settings", {})

    def _color(self, key: str, fallback: str) -> int:
        hex_str = self.embed_cfg.get("colors", {}).get(key, fallback)
        try:
            return int(hex_str, 16)
        except ValueError:
            return int(fallback, 16)

    def build_embed(self, tipo: str, membro: discord.abc.User, moderador: discord.abc.User, motivo: str, extra_tempo: str = None):
        """tipo: 'castigo' ou 'remove_castigo'"""
        title = self.embed_cfg.get("title_castigo" if tipo == "castigo" else "title_remove_castigo", "Castigo")
        color = self._color(tipo, "FFA500" if tipo == "castigo" else "1E90FF")
        use_ts = self.embed_cfg.get("use_timestamp", True)
        motivo_cb = self.embed_cfg.get("motivo_codeblock", True)
        show_ids = self.castigo_cfg.get("show_ids", True)
        embed = discord.Embed(title=title, color=color)
        if use_ts:
            embed.timestamp = utcnow()
        user_field = f"{membro.mention} | {membro.id}" if show_ids else membro.mention
        mod_field = f"{moderador.mention} | {moderador.id}" if show_ids else moderador.mention
        embed.add_field(name="Membro:", value=user_field, inline=True)
        embed.add_field(name="Moderador:", value=mod_field, inline=True)
        if extra_tempo:
            embed.add_field(name="Tempo:", value=extra_tempo, inline=True)
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

    def has_higher_role_bot(self, ctx: commands.Context, member: discord.Member):
        bot_member = ctx.guild.me
        return bot_member.top_role > member.top_role

    def parse_duration(self, duration_str):
        match = re.match(r"(\d+)([smd])", duration_str.lower())
        if not match:
            return None
        value, unit = int(match.group(1)), match.group(2)
        if unit == "s":
            return value
        elif unit == "m":
            return value * 60
        elif unit == "d":
            return value * 86400
        return None

    def format_duration(self, seconds):
        if seconds < 60:
            return f"{seconds} segundos"
        elif seconds < 3600:
            minutes = seconds // 60
            return f"{minutes} minuto{'s' if minutes != 1 else ''}"
        elif seconds < 86400:
            hours = seconds // 3600
            return f"{hours} hora{'s' if hours != 1 else ''}"
        else:
            days = seconds // 86400
            return f"{days} dia{'s' if days != 1 else ''}"

    async def send_notifications(self, embed, ctx: commands.Context, is_castigo=True):
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

    @commands.command(name="castigo")
    async def castigo(self, ctx: commands.Context, membro: discord.Member, duração: str, *, motivo: str = "Nenhum motivo especificado"):
        self.refresh_config()
        authorized_roles = self.castigo_cfg.get("authorized_roles", [])
        log_channel_id = self.castigo_cfg.get("log_channel_id", 0)
        delete_delay = self.embed_cfg.get("delete_message_delay", 8)

        if not any(role.id in authorized_roles for role in ctx.author.roles):
            await self.bot.send_no_permission(ctx)
            return

        if not self.has_higher_role(ctx, membro):
            msg = await ctx.send("Você não pode castigar alguém com cargo igual ou superior ao seu")
            await msg.delete(delay=delete_delay)
            return

        if not self.has_higher_role_bot(ctx, membro):
            msg = await ctx.send("Eu não posso castigar esse usuário devido ao meu cargo")
            await msg.delete(delay=delete_delay)
            return

        duration_seconds = self.parse_duration(duração)
        if duration_seconds is None:
            msg = await ctx.send("Formato de tempo inválido! Use 's', 'm' ou 'd' (ex.: 5m para 5 minutos)")
            await msg.delete(delay=delete_delay)
            return

        try:
            timeout_until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)
            await membro.timeout(timeout_until, reason=motivo)
            embed = self.build_embed("castigo", membro, ctx.author, motivo, extra_tempo=self.format_duration(duration_seconds))
            sent = await ctx.send(embed=embed)
            try:
                await sent.delete(delay=delete_delay)
            except Exception:
                pass
            log_channel = self.bot.get_channel(log_channel_id)
            if log_channel:
                await log_channel.send(embed=embed)
            await self.send_notifications(embed, ctx, is_castigo=True)

        except discord.Forbidden:
            msg = await ctx.send("Eu não tenho permissão para castigar esse usuário")
            await msg.delete(delay=delete_delay)
        except Exception as e:
            msg = await ctx.send(f"Erro ao aplicar castigo: {str(e)}")
            await msg.delete(delay=delete_delay)

    @commands.command(name="removercastigo")
    async def remover_castigo(self, ctx: commands.Context, membro: discord.Member, *, motivo: str = "Nenhum motivo especificado"):
        self.refresh_config()
        authorized_roles = self.castigo_cfg.get("authorized_roles", [])
        log_channel_id = self.castigo_cfg.get("log_channel_id", 0)
        delete_delay = self.embed_cfg.get("delete_message_delay", 8)

        if not any(role.id in authorized_roles for role in ctx.author.roles):
            await self.bot.send_no_permission(ctx)
            return

        try:
            if not membro.is_timed_out():
                msg = await ctx.send(f"{membro.mention} não está com um castigo ativo")
                await msg.delete(delay=delete_delay)
                return

            await membro.timeout(None, reason=motivo)
            embed = self.build_embed("remove_castigo", membro, ctx.author, motivo)
            sent = await ctx.send(embed=embed)
            try:
                await sent.delete(delay=delete_delay)
            except Exception:
                pass
            log_channel = self.bot.get_channel(log_channel_id)
            if log_channel:
                await log_channel.send(embed=embed)
            await self.send_notifications(embed, ctx, is_castigo=True)

        except discord.Forbidden:
            msg = await ctx.send("Eu não tenho permissão para remover o castigo desse usuário")
            await msg.delete(delay=delete_delay)
        except Exception as e:
            msg = await ctx.send(f"Erro ao remover castigo: {str(e)}")
            await msg.delete(delay=delete_delay)

async def setup(bot):
    await bot.add_cog(CastigoCog(bot))