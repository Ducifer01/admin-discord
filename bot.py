import asyncio
import logging
import os
from pathlib import Path
import discord
from discord.ext import commands

from config_loader import config_manager, TOKEN, PREFIX, GUILD_ID

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
intents.voice_states = True

# Logging básico
global_cfg = config_manager.load_global()
if global_cfg.get('logging', {}).get('enabled', True):
    level_name = global_cfg['logging'].get('level', 'INFO').upper()
    logging.basicConfig(level=getattr(logging, level_name, logging.INFO), format='[%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('bot')

class BotCore(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=PREFIX, intents=intents)
        self._error_cfg = global_cfg.get('errors', {})
        self._cleanup_cfg = global_cfg.get('message_cleanup', {})
        self._orig_ctx_send_patched = False
        self._perm_embed_cfg = global_cfg.get('permission_embed', {})

    async def setup_hook(self):
        # Carrega todas as cogs .py dentro de cogs/
        cogs_dir = Path(__file__).parent / 'cogs'
        for file in cogs_dir.glob('*.py'):
            if file.name.startswith('_'):
                continue
            ext = f'cogs.{file.stem}'
            try:
                await self.load_extension(ext)
                logger.info(f'Cog carregada: {ext}')
            except Exception as e:
                logger.error(f'Erro ao carregar {ext}: {e}')

        # Sync de slash commands
        try:
            if global_cfg['bot'].get('sync_commands_guild_only') and GUILD_ID:
                guild = discord.Object(id=int(GUILD_ID))
                await self.tree.sync(guild=guild)
                logger.info('Slash commands sincronizados apenas para a guild especificada.')
            else:
                await self.tree.sync()
                logger.info('Slash commands sincronizados globalmente.')
        except Exception as e:
            logger.error(f'Erro ao sincronizar comandos: {e}')

        # Monkey patch Context.send para auto delete de respostas se habilitado
        if self._cleanup_cfg.get('enabled', False) and not self._orig_ctx_send_patched:
            self._patch_context_send()
            # Registra hook before_invoke corretamente (o método interno não era automaticamente usado)
            async def _before_any_command(ctx: commands.Context):
                cfg = self._cleanup_cfg
                if not cfg.get('enabled'):
                    return
                if ctx.command and ctx.command.name in cfg.get('ignore_commands', []):
                    return
                prefixes_ignore = cfg.get('ignore_prefixes', [])
                if any(ctx.message.content.startswith(pfx) for pfx in prefixes_ignore):
                    return
                delay = cfg.get('user_command_delete_delay', 1)
                if delay <= 0:
                    return
                async def _del(msg):
                    await asyncio.sleep(delay)
                    try:
                        await msg.delete()
                    except (discord.Forbidden, discord.NotFound):
                        pass
                    except Exception as e:
                        if cfg.get('debug_log'):
                            logger.warning(f'Falha ao deletar invocação: {e}')
                asyncio.create_task(_del(ctx.message))
            # Usa API do bot para definir hook global
            self.before_invoke(_before_any_command)

    async def on_ready(self):
        activity_text = global_cfg['bot'].get('activity', 'Online')
        await self.change_presence(activity=discord.Game(name=activity_text), status=discord.Status.online)
        logger.info(f'Logado como {self.user} (ID: {self.user.id})')

    async def on_command_error(self, ctx: commands.Context, error: Exception):
        # Ignora se já tratado dentro do comando
        if hasattr(error, 'handled'):
            return
        msgs = self._error_cfg.get('messages', {})
        delay = self._error_cfg.get('delete_delay', 5)

        reply = None
        from discord.ext.commands import MissingPermissions, BotMissingPermissions, MissingRequiredArgument, CommandNotFound
        if isinstance(error, MissingPermissions):
            reply = msgs.get('missing_permissions')
        elif isinstance(error, BotMissingPermissions):
            reply = msgs.get('bot_missing_permissions')
        elif isinstance(error, MissingRequiredArgument):
            reply = msgs.get('missing_argument', 'Argumento faltando: {param}').format(param=error.param.name)
        elif isinstance(error, CommandNotFound):
            # Podemos silenciar ou responder
            reply = msgs.get('command_not_found')
        else:
            logger.error(f'Erro inesperado em comando {getattr(ctx.command, "name", "?")}: {error}', exc_info=error)
            reply = msgs.get('generic')

        if reply:
            try:
                msg = await ctx.send(reply)
                # Erros usam delay próprio de errors.delete_delay, independente do config global
                try:
                    await msg.delete(delay=delay)
                except Exception:
                    pass
            except Exception as send_err:
                logger.error(f'Falha ao enviar mensagem de erro: {send_err}')

    async def on_message(self, message: discord.Message):
        """Intercepta todas as mensagens para aplicar auto-delete em qualquer coisa que comece com o prefixo, mesmo se comando inválido."""
        if not message.guild or message.author.bot:
            return await super().on_message(message)
        cfg = self._cleanup_cfg
        prefix = PREFIX  # assumindo prefixo fixo
        if cfg.get('enabled') and message.content.startswith(prefix):
            # Ignora prefixos especiais (ex. //)
            prefixes_ignore = cfg.get('ignore_prefixes', [])
            if not any(message.content.startswith(pfx) for pfx in prefixes_ignore):
                delay = cfg.get('user_command_delete_delay', 1)
                if delay > 0:
                    async def _del(msg):
                        await asyncio.sleep(delay)
                        try:
                            await msg.delete()
                        except (discord.Forbidden, discord.NotFound):
                            pass
                        except Exception as e:
                            if cfg.get('debug_log'):
                                logger.warning(f'Falha ao deletar invocação (on_message): {e}')
                    asyncio.create_task(_del(message))
        await super().on_message(message)

    def _patch_context_send(self):
        """Envolve Context.send para agendar deleção das respostas."""
        original_send = commands.Context.send

        async def wrapped_send(ctx: commands.Context, *args, **kwargs):
            message = await original_send(ctx, *args, **kwargs)
            cfg = self._cleanup_cfg
            if cfg.get('enabled'):
                delay = cfg.get('bot_reply_delete_delay', 5)
                if delay > 0:
                    async def _del(m):
                        await asyncio.sleep(delay)
                        try:
                            await m.delete()
                        except (discord.Forbidden, discord.NotFound):
                            pass
                        except Exception as e:
                            if cfg.get('debug_log'):
                                logger.warning(f'Falha ao deletar resposta: {e}')
                    asyncio.create_task(_del(message))
            return message

        commands.Context.send = wrapped_send
        self._orig_ctx_send_patched = True

    async def send_no_permission(self, ctx: commands.Context):
        cfg = self._perm_embed_cfg
        if not cfg.get('enabled'):
            # Fallback texto
            msg = await ctx.send("Você não tem permissão para usar este comando")
            return msg
        import discord
        color_hex = cfg.get('color', 'D9534F')
        try:
            color_int = int(color_hex, 16)
        except ValueError:
            color_int = int('D9534F', 16)
        title = cfg.get('title', 'Acesso Negado')
        template = cfg.get('template', '{user}, você não tem permissão.')
        description = template.format(user=ctx.author.mention)
        embed = discord.Embed(title=title, description=description, color=color_int)
        sent = await ctx.send(embed=embed)
        delay = cfg.get('delete_delay_override') or self._cleanup_cfg.get('bot_reply_delete_delay')
        if delay:
            try:
                await sent.delete(delay=delay)
            except Exception:
                pass
        return sent


bot = BotCore()

async def main():
    token = TOKEN
    if not token:
        raise RuntimeError('DISCORD_TOKEN não definido no .env')
    await bot.start(token)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info('Bot encerrado manualmente.')
