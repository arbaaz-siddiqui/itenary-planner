# prompts/

Prompt files versioned separately so they can be rolled back without touching code.
Both are loaded by `agent.py` at startup.

| File | What it does |
|---|---|
| `system_prompt_v1.md` | The main system prompt. Establishes the sales-focused tone, the tool-use phases (floor check → detail search → selection → payment summary), pricing discipline (one inclusive INR total, no itemization), and the Streamlit card-display signal phrases. |
| `whatsapp_addendum.md` | Mobile-channel overrides appended after the system prompt when the user is on WhatsApp. Disables markdown tables/long lists, enforces 600-char budget, suppresses card phrases. |
