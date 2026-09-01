import os
import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

message = "🟢 تست جدید\n\nscanner.py با موفقیت اجرا شد."

response = requests.post(
    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
    data={
        "chat_id": CHAT_ID,
        "text": message
    },
    timeout=10
)

print("Telegram response:")
print(response.text)

response.raise_for_status()
