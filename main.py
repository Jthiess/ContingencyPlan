# Imports
import discord
import dotenv
import os

# Load environment variables
dotenv.load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Intents setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = discord.Bot(intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.slash_command(name="ping", description="Simple test command")
async def ping(ctx):
    await ctx.respond("Pong")

bot.run(TOKEN)
