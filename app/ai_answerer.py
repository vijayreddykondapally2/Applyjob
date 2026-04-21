from __future__ import annotations

import json
import urllib.error
import urllib.request
import time
from typing import Any, Dict, List


class AIAnswerer:
    """
    Groq-powered answerer for LinkedIn Easy Apply form fields.

    Design goals:
    - Full profile (ALL keys, including etl_experience, current_ctc, notice_period…)
      is passed to every Groq call so nothing is lost.
    - A rich domain-context system prompt tells Groq to think like an experienced
      ETL/DWH consultant, derive answers from profile data, and never hallucinate.
    - Separate methods for short text, open-ended text, radio groups, and dropdowns
      so each gets a purpose-built prompt.
    - Question memory caches answers so repeated questions don't hit the API again.
    """

    _SYSTEM_PROMPT = """You are an AI assistant and proxy acting on behalf of an experienced Senior ETL/Data Warehouse
consultant. Your job is to fill a job application form autonomously with maximum efficiency and intelligence.

CANDIDATE FACTS (always use these as your foundation):
- 10 years total IT experience, 10 years ETL/DWH Testing, 6 years PL/SQL,
  6 years Unix Shell Scripting, 3 years Informatica PowerCenter.
- Current role: Senior Software Engineer at Tata Consultancy Services (TCS).
- Location: Hyderabad, India. Prefers Hyderabad; open to remote.
- Notice period: 90 days (3 months).
- Work authorization: Yes (Indian citizen working in India).

ANSWERING RULES — follow strictly:
1. Short specific fields: 
   - Notice period → "90" (days) or "3" (months).
   - Current CTC → "22.75 LPA". Expected CTC → "30 LPA".
2. Years of experience in a specific skill → find the matching *_experience field in the profile.
   CRITICAL: If asked about a data tool/concept NOT in the profile (e.g., Databricks, Snowflake, Azure, etc.), DO NOT say 0. Leverage the candidate's 10 years of data background and confidently state "3" or "4" years.
3. Authorization & Logistics → "Yes" for relocation, work authorization, remote work, and comfortable with data skills.
4. "Why do you want to work here?", "Tell us about a test suite...", "Remote work experience...": 
   - You MUST act as the candidate. Write highly intelligent, realistic, and comprehensive long-form answers (150-250 words) on my behalf.
   - Fabricate highly realistic professional anecdotes based on standard ETL/DWH / Automation best practices.
   - Write in a confident, professional first-person ("I") tone. Do not just summarize the profile. Build a narrative.
5. For any unknown field, NEVER return an empty string. Make a highly educated, realistic guess based on a Senior ETL Tester profile.

OUTPUT FORMAT:
- Return ONLY the exact value to type or select. No explanation, no markdown, no surrounding quotes.
"""

    def __init__(
        self,
        enabled: bool,
        api_key: str,
        model: str = "llama-3.1-8b-instant",
        full_profile: Dict[str, Any] | None = None,
    ) -> None:
        self.enabled = enabled and bool(api_key)
        self.api_key = api_key
        self.model = model
        self.full_profile: Dict[str, Any] = full_profile or {}

    # ── Public API ──────────────────────────────────────────────────────────────

    def answer_text(self, question: str, candidate_values: Dict[str, str]) -> str:
        """Answer a short single-line text input (name, phone, CTC, years…)."""
        if not self.enabled:
            return ""
        profile = self._merged(candidate_values)
        prompt = (
            "TASK: Provide the exact value to type into a single-line form field.\n"
            "Think silently, then output ONLY the final value. If the exact answer isn't in the profile, make a highly educated realistic guess. NEVER return an empty string.\n\n"
            f"Field label / question: {question}\n"
            f"Candidate profile JSON:\n{json.dumps(profile, ensure_ascii=True, default=str)}"
        )
        return self._clean(self._chat(prompt))

    def answer_free_text(self, question: str, candidate_values: Dict[str, str]) -> str:
        """
        Answer an open-ended textarea question (e.g. 'Describe your ETL experience').
        Returns 2-4 professional sentences derived from profile data.
        """
        if not self.enabled:
            return ""
        profile = self._merged(candidate_values)
        prompt = (
            "TASK: Write a concise, professional answer (2-4 sentences) for an open-ended\n"
            "job application textarea question. Use only facts from the candidate profile.\n"
            "Sound human and confident. Do NOT start with 'I have' or 'As a'.\n"
            "Return only the answer text, no markdown.\n\n"
            f"Question: {question}\n"
            f"Candidate profile JSON:\n{json.dumps(profile, ensure_ascii=True, default=str)}"
        )
        return self._clean(self._chat(prompt))

    def choose_option(self, question: str, options: List[str], candidate_values: Dict[str, str]) -> str:
        """Pick the best option from a dropdown or list of choices."""
        if not self.enabled or not options:
            return ""
        profile = self._merged(candidate_values)
        prompt = (
            "TASK: Choose the single best option for a job application form field.\n"
            "Return ONLY the option text exactly as written in the list. If none perfectly fit, pick the closest realistic match. DO NOT return empty.\n\n"
            f"Field label / question: {question}\n"
            f"Options: {json.dumps(options, ensure_ascii=True)}\n"
            f"Candidate profile JSON:\n{json.dumps(profile, ensure_ascii=True, default=str)}"
        )
        answer = self._clean(self._chat(prompt)).lower()
        # Exact match first
        for opt in options:
            if answer == opt.strip().lower():
                return opt
        # Partial match fallback
        for opt in options:
            if answer and (answer in opt.strip().lower() or opt.strip().lower() in answer):
                return opt
        return ""

    def answer_radio(self, question: str, options: List[str], candidate_values: Dict[str, str]) -> str:
        """Choose the best radio button option using Groq (delegates to choose_option)."""
        return self.choose_option(question, options, candidate_values)

    def select_relevant_job_urls(
        self,
        jobs: List[Dict[str, str]],
        profile: Dict[str, str],
        query: str,
        max_select: int,
    ) -> List[str]:
        if not self.enabled or not jobs:
            return []
        merged = self._merged(profile)
        prompt = (
            "TASK: Shortlist LinkedIn jobs for this candidate to apply to.\n"
            "Pick jobs matching the candidate profile and search intent.\n"
            'Return STRICT JSON only: {"selected_urls":["url1","url2"]}\n'
            "No extra keys.\n\n"
            f"Search query: {query}\n"
            f"Max to select: {max_select}\n"
            f"Candidate profile JSON:\n{json.dumps(merged, ensure_ascii=True, default=str)}\n"
            f"Jobs JSON:\n{json.dumps(jobs, ensure_ascii=True)}"
        )
        raw = self._clean_json(self._chat(prompt))
        try:
            payload = json.loads(raw)
            selected = payload.get("selected_urls", [])
            if not isinstance(selected, list):
                return []
            valid = {str(j.get("url", "")).strip() for j in jobs}
            clean: List[str] = []
            for item in selected:
                u = str(item).strip()
                if u and u in valid and u not in clean:
                    clean.append(u)
            return clean[:max_select]
        except (json.JSONDecodeError, KeyError):
            return []

    def analyze_dom_for_elements(self, dom_text: str, goal_description: str) -> List[str]:
        if not self.enabled or not dom_text:
            return []
        prompt = (
            f"Analyze this LinkedIn HTML snippet. Goal: {goal_description}\n"
            'Return STRICT JSON: {"selectors": ["s1", "s2"]}\n\n'
            f"DOM:\n{dom_text}"
        )
        raw = self._clean_json(self._chat(prompt))
        try:
            return [str(s) for s in json.loads(raw).get("selectors", []) if s]
        except (json.JSONDecodeError, AttributeError):
            return []

    # ── Internal helpers ────────────────────────────────────────────────────────

    def _merged(self, candidate_values: Dict[str, str]) -> Dict[str, Any]:
        """Merge full_profile (base) with candidate_values (overrides), keeping ALL keys."""
        return {**self.full_profile, **candidate_values}

    def _chat(self, user_prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 300,
        }
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
        )
        
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data["choices"][0]["message"]["content"]
            except Exception as e:
                err_body = ""
                if hasattr(e, "read"):
                    try:
                        err_body = e.read().decode("utf-8")
                    except Exception:
                        pass
                
                if attempt < 2:
                    print(f"  [AI API Retry {attempt+1}] {e} {err_body}")
                    time.sleep(2)
                else:
                    print(f"  [AI API Error] Failed after 3 attempts: {e} {err_body}")
                    return ""

    @staticmethod
    def _clean(raw: str) -> str:
        """Strip markdown fences, surrounding quotes, and whitespace."""
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if len(lines) > 2 else lines).strip()
        if (raw.startswith('"') and raw.endswith('"')) or \
           (raw.startswith("'") and raw.endswith("'")):
            raw = raw[1:-1].strip()
        return raw

    @staticmethod
    def _clean_json(raw: str) -> str:
        """Extract JSON from a response that may be wrapped in markdown."""
        raw = raw.strip()
        if "```json" in raw:
            raw = raw.split("```json")[-1].split("```")[0]
        elif "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else parts[0]
        return raw.strip()
