import os
import discord
import feedparser
import asyncio

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1419941517103075454
RSS_URL = "https://rss.app/feeds/Rja0GFPWPIz5ZQNG.xml"
CHECK_INTERVAL = 60

intents = discord.Intents.default()
client = discord.Client(intents=intents)
last_post_id = None

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    await client.wait_until_ready()
    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        print("Channel not found! Check CHANNEL_ID or bot permissions.")
        return

    global last_post_id
    while True:
        feed = feedparser.parse(RSS_URL)
        if feed.entries:
            newest = feed.entries[0]
            post_id = newest.id if 'id' in newest else newest.link
            if post_id != last_post_id:
                title = newest.title
                link = newest.link
                description = newest.summary if 'summary' in newest else ""

                embed = discord.Embed(
                    title=title,
                    url=link,
                    description=(description[:200] + "...") if len(description) > 200 else description
                )

                # optional image
                if 'media_content' in newest and len(newest.media_content) > 0:
                    embed.set_image(url=newest.media_content[0]['url'])

                await channel.send(embed=embed)
                last_post_id = post_id

        await asyncio.sleep(CHECK_INTERVAL)

client.run(TOKEN)
