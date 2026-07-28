import json
import os
import secrets
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
DATA_PATH = Path(os.environ.get("HOOK_DATA_PATH", Path(__file__).parent / "data" / "signups.jsonl"))
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
STRIPE_PAYMENT_URL = os.environ.get("STRIPE_PAYMENT_URL", "")
VARIANTS = {
    "x": {
        "eyebrow": "THE $3 HOOK WRITING CLASS",
        "headline": "Write the first sentence people can’t ignore.",
        "subhead": "A small, practical class for indie hackers who have built something useful and still have to explain it before anyone cares.",
        "proof": "From $3 curiosity hooks to $3,000 services.",
    },
    "essay": {
        "eyebrow": "THE HELLO, WORLD! OF THE INTERNET",
        "headline": "The hook is where your product starts making money.",
        "subhead": "Learn the curiosity hook that turns a sentence into a reader, a reader into a subscriber, and a subscriber into a customer.",
        "proof": "One good hook makes everything downstream less difficult.",
    },
    "builder": {
        "eyebrow": "FOR PEOPLE WHO SHIP",
        "headline": "Stop building in silence.",
        "subhead": "Bring one thing you made. Leave with a repeatable way to make strangers curious enough to look.",
        "proof": "Three hook patterns. Reps until one earns its $3.",
    },
    "default": {
        "eyebrow": "THE $3 HOOK WRITING CLASS",
        "headline": "If you can hook attention, you can hook money.",
        "subhead": "A live, LLM-led writing room for indie hackers learning the internet’s most useful small craft: making people curious.",
        "proof": "Join for $3. Make hooks. Publish the misses.",
    },
}

SYSTEM_PROMPT = """You are the coach for a $3 hook-writing class for indie hackers. Teach curiosity hooks, not hype. Be concise and specific. Return JSON with keys: hook, why_it_works, risk, next_exercise. The hook must be honest, avoid invented numbers, and make a concrete promise or tension. The risk should name what would falsify it."""
PRIMER_PROMPT = """You are the top-of-funnel coach for a $3 hook-writing class. Analyze only the supplied public page text. Be useful, specific, and honest. Do not invent business facts. Return JSON with exactly these keys: what_it_is, likely_audience, hook_gap, first_hook. Keep each value under 280 characters. The first_hook should be a curiosity hook worth testing, not hype."""


def variant_for(source: str):
    source = (source or "").lower().strip()
    if "twitter" in source or "x.com" in source or source in {"x", "tweet"}:
        return "x"
    if "essay" in source or "substack" in source or source in {"article", "newsletter"}:
        return "essay"
    if "indie" in source or "builder" in source or "product" in source:
        return "builder"
    return "default"


def append_record(record):
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DATA_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def public_url(value):
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use a public http:// or https:// URL.")
    addresses = socket.getaddrinfo(parsed.hostname, None)
    import ipaddress
    if not addresses or any(ipaddress.ip_address(address[4][0]).is_private or ipaddress.ip_address(address[4][0]).is_loopback or ipaddress.ip_address(address[4][0]).is_link_local for address in addresses):
        raise ValueError("That URL points to a private network.")
    return parsed.geturl()


def stripe_checkout_url(email, lead_id):
    params = urlencode({"prefilled_email": email, "client_reference_id": lead_id})
    return f"{STRIPE_PAYMENT_URL}&{params}" if "?" in STRIPE_PAYMENT_URL else f"{STRIPE_PAYMENT_URL}?{params}"


@app.get("/")
def index():
    source = request.args.get("src", request.args.get("utm_source", ""))
    key = variant_for(source)
    return render_template("index.html", variant=VARIANTS[key], variant_key=key, source=source, stripe_url=STRIPE_PAYMENT_URL)


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "hook-writing-class", "llm_configured": bool(os.environ.get("OPENROUTER_API_KEY"))})


@app.post("/api/join")
def join():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip().lower()
    if "@" not in email or len(email) > 254:
        return jsonify({"error": "A real email address is required."}), 400
    record = {
        "id": secrets.token_urlsafe(8),
        "email": email,
        "source": str(payload.get("source", "direct"))[:120],
        "variant": variant_for(str(payload.get("source", ""))),
        "idea": str(payload.get("idea", ""))[:1000],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    append_record(record)
    return jsonify({"ok": True, "message": "You’re in. Bring one thing you made."})


@app.post("/api/prime")
def prime():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip().lower()
    url = str(payload.get("url", "")).strip()
    twitter = str(payload.get("twitter", "")).strip()[:120]
    if "@" not in email or len(email) > 254:
        return jsonify({"error": "A real email address is required."}), 400
    try:
        url = public_url(url)
    except (ValueError, socket.gaierror, socket.timeout):
        return jsonify({"error": "Use a public website or product URL."}), 400
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return jsonify({"error": "The primer is not configured yet."}), 503
    lead_id = secrets.token_urlsafe(8)
    record = {"id": lead_id, "email": email, "url": url, "twitter": twitter, "source": str(payload.get("source", "direct"))[:120], "variant": variant_for(str(payload.get("source", ""))), "created_at": datetime.now(timezone.utc).isoformat(), "kind": "primer"}
    try:
        page = requests.get(url, headers={"User-Agent": "HookClassPrimer/1.0"}, timeout=10, stream=True)
        page.raise_for_status()
        chunks = []
        total = 0
        for chunk in page.iter_content(16384):
            total += len(chunk)
            chunks.append(chunk)
            if total >= 120000:
                break
        content = b"".join(chunks)[:120000].decode("utf-8", errors="ignore")
        prompt = f"URL: {url}\nPUBLIC PAGE TEXT:\n{content[:24000]}"
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "HTTP-Referer": os.environ.get("PUBLIC_URL", "https://hooks.metaspn.network"), "X-Title": "The $3 Hook Writing Class"}, json={"model": OPENROUTER_MODEL, "messages": [{"role": "system", "content": PRIMER_PROMPT}, {"role": "user", "content": prompt}], "temperature": 0.4, "response_format": {"type": "json_object"}}, timeout=35)
        response.raise_for_status()
        primer = json.loads(response.json()["choices"][0]["message"]["content"])
        record["status"] = "primed"
        append_record(record)
        return jsonify({"ok": True, "lead_id": lead_id, "primer": primer, "checkout_url": stripe_checkout_url(email, lead_id)})
    except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError):
        record["status"] = "primer_failed"
        append_record(record)
        return jsonify({"error": "I saved your place, but the primer missed the beat. Continue to checkout and we’ll handle it in class."}), 502


@app.post("/api/exercise")
def exercise():
    payload = request.get_json(silent=True) or {}
    idea = str(payload.get("idea", "")).strip()
    audience = str(payload.get("audience", "")).strip()
    if len(idea) < 8 or len(audience) < 3:
        return jsonify({"error": "Give the coach a product and a person."}), 400
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return jsonify({"error": "The coach is not configured yet. The landing page is live; the exercise needs its server key."}), 503
    prompt = f"Product or service: {idea}\nAudience: {audience}\nWrite one curiosity hook for it."
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "HTTP-Referer": os.environ.get("PUBLIC_URL", "https://hooks.metaspn.network"), "X-Title": "The $3 Hook Writing Class"},
            json={"model": OPENROUTER_MODEL, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}], "temperature": 0.7, "response_format": {"type": "json_object"}},
            timeout=35,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        result = json.loads(content)
        return jsonify({"ok": True, "result": result, "model": OPENROUTER_MODEL})
    except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError) as exc:
        app.logger.warning("exercise failed: %s", type(exc).__name__)
        return jsonify({"error": "The coach missed the beat. Try again."}), 502


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8873")))
