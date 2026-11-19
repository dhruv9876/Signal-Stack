"""Adaptive 10-question founder interview: one question at a time, with memory."""
import json

import llm

TOTAL = 10
AREAS = [
    "primary business lines and revenue streams",
    "highest-value customer segments",
    "current customer problems and unmet needs",
    "why customers choose this company over alternatives",
    "important competitors or substitutes",
    "major industry and market changes",
    "developments that could materially affect the business",
    "growth priorities for the next 6-12 months",
    "internal business information that should influence decisions",
    "decisions the intelligence system should help the founder make",
]


def _history(qa):
    return "\n".join(
        f"Q{r['idx']} ({'skipped' if r['skipped'] else 'answered'}): {r['question']}\n"
        f"A{r['idx']}: {'[founder skipped this]' if r['skipped'] else r['answer']}"
        for r in qa) or "(no questions asked yet)"


def _profile_text(profile):
    keep = {k: v for k, v in profile.items() if k not in ("pages_read", "fetch_errors")}
    return json.dumps(keep, ensure_ascii=False)[:6000]


def next_question(profile, qa):
    n = len(qa) + 1
    prompt = f"""You are interviewing a founder so an intelligence system can monitor their business.
COMPANY PROFILE (from their website): {_profile_text(profile)}

INTERVIEW SO FAR:
{_history(qa)}

Ask question {n} of {TOTAL}. Rules:
- One question only, specific to THIS company, its industry and the answers so far.
- If a previous answer opened an important thread, ask a follow-up about it.
- Never repeat or rephrase an earlier question. If something was skipped, do not ask for it again.
- Over the whole interview, these areas must be covered: {'; '.join(AREAS)}.
  Prefer an area not yet covered. Question {TOTAL} should ask which decisions the system should help with, if not yet covered.
- Plain, conversational language.
Return JSON: {{"question": str, "area": str}}"""
    d = llm.generate_json(prompt)
    if not isinstance(d, dict) or not d.get("question"):
        raise llm.LLMError("Model did not return a question.")
    return {"question": d["question"].strip(), "area": d.get("area", "")}


def build_interview_profile(profile, qa):
    prompt = f"""Turn this founder interview into a structured company intelligence profile.
Use ONLY what the founder said or the website profile says. If unknown, use null or [].
WEBSITE PROFILE: {_profile_text(profile)}
INTERVIEW:
{_history(qa)}

Return JSON with keys: business_lines, top_customer_segments, customer_problems, differentiators,
named_competitors, market_changes_to_watch, growth_priorities, internal_data_available,
decisions_to_support, confidential_topics_skipped. Each value is a short list of strings."""
    d = llm.generate_json(prompt)
    if not isinstance(d, dict):
        raise llm.LLMError("Unexpected profile format from the model.")
    return d
