import io
import pkgutil

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands


class SaveCog(commands.Cog, name="Save"):
    @app_commands.command(name="save", description="Request a save for the Steam version of BNLC")
    @app_commands.guild_only()
    @app_commands.choices(game=[
        Choice(name="Battle Network 6 Falzar", value="exe6f"),
        Choice(name="Battle Network 6 Gregar", value="exe6g")
    ])
    async def request_save(
        self,
        interaction: discord.Interaction,
        game: Choice[str],
        steamid_64_or_32: int
    ) -> None:
        steamid_32 = steamid_64_or_32 & 0xffffffff
        steam_id_bytes = steamid_32.to_bytes(4, "little")

        encrypted = pkgutil.get_data("mrprog/bot/saves", f"{game}_save_0.bin")
        xor_byte = encrypted[1]
        decrypted = self.array_xor(encrypted, xor_byte)

        for i, b in enumerate(steam_id_bytes):
            decrypted[6496 + i] = b

        encrypted_updated = self.array_xor(decrypted, xor_byte)

        with io.BytesIO() as save_file:
            save_file.write(encrypted_updated)
            save_file.seek(0)
            save_upload = discord.File(save_file, filename=f"{game}_save_0.bin")

        await interaction.response.send_message(
            content=fr"Copy this file to `C:\Program Files (x86)\Steam\userdata\{steamid_32}\1798020\remote\{game}_save_0.bin`.\n"
                    fr"**MAKE SURE TO MAKE A BACKUP FIRST!**", file=save_upload)

    @staticmethod
    def array_xor(b1, xor):
        result = bytearray(b1)
        for i in range(len(b1)):
            result[i] ^= xor
        return result


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SaveCog(bot))
