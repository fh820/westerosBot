# bot.py
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from app.services.pathfinder_bot_engine import (
    Pathfinder,
    DATA_FILE,
    COST_MAP_FILE,
    MAP_FILE,
)
from app.db.db_manager import init_db
from app.db.db_manager import init_db, get_session_factory

# 1. Load Environment Variables (for your token)
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# 2. Define Bot Intents
# These are the permissions your bot needs to function.
intents = discord.Intents.default()
intents.message_content = True  # Allows the bot to read message content

# 3. Create the Bot Instance
# We will attach our pathfinder engine to this 'bot' object.
bot = commands.Bot(command_prefix="!", intents=intents)


# 4. Define the Asynchronous Setup Hook
# This is the modern way to handle startup tasks in discord.py
@bot.event
async def setup_hook():
    print("--- ⚔️  WesterosBot Initializing ⚔️  ---")
    print(f"--- ⚔️  BOT CWD: {os.getcwd()} ⚔️  ---")  # ADD THIS LINE

    # 1. Connect to Database (Just connection, no world generation)
    print("Connecting to Database...")
    await init_db()

    # 2. Initialize Pathfinder Engine (The Map Logic)
    print("Initializing Pathfinder Engine...")
    bot.pathfinder = Pathfinder(
        data_file=DATA_FILE, cost_map_file=COST_MAP_FILE, map_file=MAP_FILE
    )
    print("✅ Pathfinder Engine Ready.")

    # 3. Load Cogs (Commands)
    print("Loading command cogs...")
    for filename in os.listdir(os.path.join(os.path.dirname(__file__), "cogs")):
        if filename.endswith(".py") and not filename.startswith("__"):
            try:
                await bot.load_extension(f"app.cogs.{filename[:-3]}")
                print(f"  - Loaded: {filename}")
            except Exception as e:
                print(f"  - ⚠️ FAILED to load {filename}: {e}")

    print("--- ✅ Setup Complete ---")


# 5. Define the on_ready Event
# This fires after the bot has successfully connected to Discord.
@bot.event
async def on_ready():
    """Confirms that the bot is online and connected."""
    print(f"\nSUCCESS! Logged in as {bot.user}")
    print(f"Ready to accept commands in servers.")
    print("-----------------------------------")


# 6. Run the Bot
# This is the final step that starts the bot's connection loop.
if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("ERROR: BOT_TOKEN not found in .env file. Bot cannot start.")
