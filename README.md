# The $3 Hook Writing Class

Local-first landing page and LLM-led curiosity-hook exercise.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open http://127.0.0.1:8873/?src=x.

`OPENROUTER_API_KEY` is server-only. No key is stored in this repository or sent to browsers.

Referral variants:
- `?src=x` — social proof / curiosity
- `?src=essay` — essay framing
- `?src=builder` — shipper framing
- anything else — direct framing
