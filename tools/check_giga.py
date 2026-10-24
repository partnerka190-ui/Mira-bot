"""Проверка ключа GigaChat: python -m tools.check_giga"""
import asyncio

from bot.llm import giga


async def main():
    print("GigaChat:", await giga.complete("Ответь одним словом: работает?", max_tokens=10))


asyncio.run(main())
