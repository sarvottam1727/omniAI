import json, urllib.request
BASE = "http://127.0.0.1:5173"

# what's in SECRET_STORE before restart?
def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read().decode())

print("Before restart:")
print("  /api/crypto-status:", get("/api/crypto-status"))
state = get("/api/state")
senders = [s for s in state["senders"] if s["provider"] == "gmail"]
for s in senders:
    print(f"  sender {s['id'][:8]} password_configured={s['password_configured']}")
