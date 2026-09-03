import requests
import time

url = "https://futures.kraken.com/api/charts/v1/PF_XBTUSD/5"

print("🔍 Testing Kraken connection...")
start = time.time()

try:
    r = requests.get(url, timeout=(3, 5))

    elapsed = time.time() - start

    print("STATUS:", r.status_code)
    print("TIME:", round(elapsed, 2), "sec")
    print("SIZE:", len(r.content))
    print("TEXT:", r.text[:300])

except Exception as e:
    elapsed = time.time() - start

    print("❌ ERROR:", repr(e))
    print("TIME:", round(elapsed, 2), "sec")
