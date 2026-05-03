"""List every Telegram channel/group you are subscribed to + its ID.

Usage on EC2:
    python list_channels.py

Pick the line that says "CRYPTO ADVANCE VIP" — copy the ID/username — paste
into .env as TG_CHANNEL.
"""
import asyncio
from telethon import TelegramClient
from telegram_bot import tg_config as config


async def main():
    client = TelegramClient(
        config.SESSION_NAME, config.TG_API_ID, config.TG_API_HASH
    )
    await client.start(phone=config.TG_PHONE)

    print(f"\n{'ID':>20}  {'Username':<30}  Title")
    print("-" * 90)
    async for dialog in client.iter_dialogs():
        if not dialog.is_channel and not dialog.is_group:
            continue
        entity = dialog.entity
        username = getattr(entity, "username", None) or "(no @username)"
        title = dialog.name or "(no title)"
        print(f"{entity.id:>20}  {username:<30}  {title}")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
