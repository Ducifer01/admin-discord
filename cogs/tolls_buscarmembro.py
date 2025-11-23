import discord
from discord.ext import commands
from config_loader import config_manager

class BuscarMembro(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = config_manager.load_cog("buscarmembro")

    @commands.command(name="buscarmembro")
    async def buscarmembro(self, ctx: commands.Context, membro: discord.Member):
        # Verifica se o comando foi usado em um servidor
        if not ctx.guild:
            msg = await ctx.send("Este comando só pode ser usado em um servidor!")
            await msg.delete(delay=5)
            return

        # Carrega a lista de cargos autorizados e o tempo de deleção
        section = self.config.get("buscarmembro", {})
        buscar_cargo = section.get("authorized_roles", [])
        delete_delay = section.get("delete_delay", 5)
        mensagens = section.get("mensagens", {})

        # Verifica se o usuário é o bot
        if membro == self.bot.user:
            desc = mensagens.get("bot", "{user}, não faz sentido procurar o bot em call.")
            embed = discord.Embed(
                title="🚫 Sem entrosar!",
                description=desc.format(user=ctx.author.mention),
                color=discord.Color.red()
            )
            msg = await ctx.send(embed=embed)
            await msg.delete(delay=delete_delay)
            return

        # Verifica se o usuário tem um cargo autorizado
        if not any(role.id in buscar_cargo for role in ctx.author.roles):
            # Usa helper global padronizado se existir
            if hasattr(self.bot, 'send_no_permission'):
                await self.bot.send_no_permission(ctx)
            else:
                desc = mensagens.get("sem_permissao", "{user}, você não tem permissão.")
                embed = discord.Embed(
                    title="🔒 Acesso Negado",
                    description=desc.format(user=ctx.author.mention),
                    color=discord.Color.red()
                )
                msg = await ctx.send(embed=embed)
                await msg.delete(delay=delete_delay)
            return

        # Verifica se o membro está em um canal de voz
        if membro.voice and membro.voice.channel:
            voice_channel = membro.voice.channel
            desc = mensagens.get("encontrado", "{user}, {target} está na call {channel}.")
            embed = discord.Embed(
                title="🎧 Membro Encontrado",
                description=desc.format(user=ctx.author.mention, target=membro.mention, channel=voice_channel.mention),
                color=discord.Color.green()
            )
        else:
            desc = mensagens.get("nao_encontrado", "{user}, {target} não está em call.")
            embed = discord.Embed(
                title="🔍 Nenhuma Call Encontrada",
                description=desc.format(user=ctx.author.mention, target=membro.mention),
                color=discord.Color.blue()
            )

        # Envia a mensagem e a deleta após o tempo configurado
        msg = await ctx.send(embed=embed)
        await msg.delete(delay=delete_delay)

async def setup(bot):
    await bot.add_cog(BuscarMembro(bot))