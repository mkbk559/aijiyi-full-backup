"""探活本机反代网关：/v1/models 与一次最小 chat。"""
import json
import urllib.request

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(url, payload, key="test-key", timeout=90):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with OP.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


for model in ("glm-5.3-flash", "deepseek-v4.1-flash"):
    status, data = post("http://127.0.0.1:8788/v1/chat/completions", {
        "model": model,
        "messages": [{"role": "user", "content": "回一个字：好"}],
        "max_tokens": 20,
    })
    text = ""
    if isinstance(data, dict):
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    print(json.dumps({"model": model, "status": status,
                      "reply": text[:40],
                      "error": None if status == 200 else str(data)[:200]},
                     ensure_ascii=False))
