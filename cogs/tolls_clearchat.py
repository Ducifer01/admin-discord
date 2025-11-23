import discord
from discord.ext import commands
from config_loader import config_manager

class ClearChatCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = config_manager.load_cog("clearchat").get("clearchat", {})

    @commands.command(name="clear")
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx, amount: int = None):
        """Apaga mensagens do canal atual. Uso: !clear <quantidade>."""
        max_messages = self.config.get("max_messages", 999)
        delete_delay = self.config.get("delete_delay", 5)
        msgs = self.config.get("messages", {})
        # Verifica se o comando foi executado em um servidor
        if ctx.guild is None:
            msg = await ctx.send("Este comando só pode ser usado em um servidor!")
            await msg.delete(delay=5)
            return

        text_channel = ctx.channel

        # Verifica se o bot tem permissão para gerenciar mensagens no canal de texto
        if not text_channel.permissions_for(ctx.guild.me).manage_messages:
            msg = await ctx.send(f"❌ **Não tenho permissão para apagar mensagens no canal {text_channel.mention}!**")
            await msg.delete(delay=5)
            return

        # Verifica se a quantidade foi especificada
        if amount is None:
            text = msgs.get("need_amount", "{user}, informe a quantidade.").format(user=ctx.author.mention)
            msg = await ctx.send(text)
            await msg.delete(delay=delete_delay)
            return

        # Verifica se a quantidade está dentro do limite permitido (1 a 999)
        if not (1 <= amount <= max_messages):
            text = msgs.get("range_error", "{user}, quantidade inválida.").format(user=ctx.author.mention, max=max_messages)
            msg = await ctx.send(text)
            await msg.delete(delay=delete_delay)
            return

        # Confirmação antes de apagar
        confirm_text = msgs.get("confirm", "Apagando {amount} mensagens...").format(amount=amount, channel=f"#{text_channel.name}")
        confirm_msg = await ctx.send(confirm_text)
        await confirm_msg.delete(delay=delete_delay)

        # Limpeza eficiente com bulk_delete()
        try:
            deleted = await text_channel.purge(limit=amount + 1)  # +1 para incluir a mensagem do comando
        except discord.Forbidden:
            text = msgs.get("no_perm_bot", "Não tenho permissão em {channel}.").format(channel=text_channel.mention)
            msg = await ctx.send(text)
            await msg.delete(delay=delete_delay)
            return
        except discord.HTTPException as e:
            msg = await ctx.send(f"❌ **Ocorreu um erro ao apagar mensagens: {str(e)}**")
            await msg.delete(delay=delete_delay)
            return

        # Informando sucesso e quantidade de mensagens apagadas
        deleted_count = len(deleted)
        if deleted_count > 0:
            text = msgs.get("success", "{deleted} mensagens apagadas em {channel}.").format(deleted=deleted_count, channel=f"#{text_channel.name}")
            msg = await ctx.send(text)
            await msg.delete(delay=delete_delay)
        else:
            text = msgs.get("none", "Nenhuma mensagem recente em {channel}.").format(channel=f"#{text_channel.name}")
            msg = await ctx.send(text)
            await msg.delete(delay=delete_delay)

        print(f"↪ {deleted_count} mensagens apagadas no canal #{text_channel.name} por {ctx.author}.")

async def setup(bot):
    await bot.add_cog(ClearChatCog(bot))