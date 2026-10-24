import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _int(name, default):
    value = os.getenv(name, "").strip()
    return int(value) if value else default


BOT_TOKEN = os.environ["BOT_TOKEN"]
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
ADMIN_ID = _int("ADMIN_ID", 0)

GIGACHAT_AUTH_KEY = os.environ["GIGACHAT_AUTH_KEY"]
# Физлицо: GIGACHAT_API_PERS, ИП/юрлицо: GIGACHAT_API_B2B или GIGACHAT_API_CORP
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat-2-Max")
# Сертификат НУЦ Минцифры (russian_trusted_root_ca.cer); без него TLS к Сберу не проверится
GIGACHAT_CA_BUNDLE = os.getenv("GIGACHAT_CA_BUNDLE", "")

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "")
# Автосписание включайте только после того, как ЮKassa одобрит автоплатежи для магазина
YOOKASSA_AUTOPAY = os.getenv("YOOKASSA_AUTOPAY", "0") == "1"

PRICE_TRIAL = _int("PRICE_TRIAL", 1)
PRICE_MONTH = _int("PRICE_MONTH", 299)
PRICE_SINGLE = _int("PRICE_SINGLE", 149)
TRIAL_DAYS = _int("TRIAL_DAYS", 7)
QUESTIONS_PER_DAY = _int("QUESTIONS_PER_DAY", 5)
DAILY_CARD_HOUR = _int("DAILY_CARD_HOUR", 8)

DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "bot.db"))
CARDS_DIR = Path(os.getenv("CARDS_DIR", str(BASE_DIR / "assets" / "cards")))
TZ = os.getenv("TZ_NAME", "Europe/Moscow")
