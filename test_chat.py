import urllib.request
import json

req = urllib.request.Request(
    'http://localhost:8000/api/v1/chat/stream',
    data=json.dumps({"message": "what are the all applicstions between june n julty", "chat_history": []}).encode('utf-8'),
    headers={'Content-Type': 'application/json'}
)

try:
    with urllib.request.urlopen(req) as response:
        print(response.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print("HTTP Error:", e.code)
    print(e.read().decode('utf-8'))
