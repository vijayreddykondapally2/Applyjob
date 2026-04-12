from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Dict, List


class AIAnswerer:
    def __init__(self, enabled: bool, api_key: str, model: str = "llama-3.1-8b-instant") -> None:
        self.enabled = enabled and bool(api_key)
        self.api_key = api_key
        self.model = model

    def answer_text(self, question: str, profile: Dict[str, str]) -> str:
        if not self.enabled:
            return ""
        prompt = (
            "You are filling a job application form field.\n"
            "Return only the exact value to type in the field, no explanation.\n"
            "If unknown, return empty string.\n\n"
            f"Question: {question}\n"
            f"Candidate profile JSON: {json.dumps(profile, ensure_ascii=True)}"
        )
        return self._chat(prompt).strip()

    def choose_option(self, question: str, options: List[str], profile: Dict[str, str]) -> str:
        if not self.enabled or not options:
            return ""
        prompt = (
            "Choose one best option for a job application field.\n"
            "Return only one option text exactly as listed, no explanation.\n"
            "If none fit, return empty string.\n\n"
            f"Question: {question}\n"
            f"Options: {json.dumps(options, ensure_ascii=True)}\n"
            f"Candidate profile JSON: {json.dumps(profile, ensure_ascii=True)}"
        )
        answer = self._chat(prompt).strip()
        normalized = answer.lower()
        for opt in options:
            if normalized == opt.strip().lower():
                return opt
        return ""

    def select_relevant_job_urls(
        self,
        jobs: List[Dict[str, str]],
        profile: Dict[str, str],
        query: str,
        max_select: int,
    ) -> List[str]:
        if not self.enabled or not jobs:
            return []
        prompt = (
            "You are helping shortlist LinkedIn jobs to apply.\n"
            "Pick jobs that best match the candidate profile and search intent.\n"
            "Return STRICT JSON only in this shape: "
            '{"selected_urls":["url1","url2"]}\n'
            "Do not include any extra keys.\n\n"
            f"Search query: {query}\n"
            f"Max select: {max_select}\n"
            f"Candidate profile JSON: {json.dumps(profile, ensure_ascii=True)}\n"
            f"Jobs JSON: {json.dumps(jobs, ensure_ascii=True)}"
        )
        raw = self._chat(prompt).strip()
        try:
            payload = json.loads(raw)
            selected = payload.get("selected_urls", [])
            if not isinstance(selected, list):
                return []
            normalized = {str(j.get('url', '')).strip() for j in jobs}
            clean = []
            for item in selected:
                u = str(item).strip()
                if u and u in normalized and u not in clean:
                    clean.append(u)
            return clean[:max_select]
        except json.JSONDecodeError:
            return []

    def analyze_dom_for_elements(self, dom_text: str, goal_description: str) -> List[str]:
        if not self.enabled or not dom_text:
            return []
        prompt = (
            "Analyze the provided HTML snippet from LinkedIn.\n"
            f"Goal: {goal_description}\n"
            "Identify the most likely CSS selectors (IDs, classes, or attributes) for the target elements.\n"
            "Return STRICT JSON only: {'selectors': ['s1', 's2', ...]}\n"
            "Prioritize buttons, links, or containers that match the goal.\n\n"
            f"DOM Snippet:\n{dom_text}"
        )
        raw = self._chat(prompt).strip()
        try:
            # Basic cleaning if AI includes markdown backticks
            if "```json" in raw:
                raw = raw.split("```json")[-1].split("```")[0]
            elif "```" in raw:
                raw = raw.split("```")[-1].split("```")[0]
            payload = json.loads(raw)
            selectors = payload.get("selectors", [])
            return [str(s) for s in selectors if s]
        except (json.JSONDecodeError, AttributeError):
            return []

    def _chat(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
        except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError):
            return ""
