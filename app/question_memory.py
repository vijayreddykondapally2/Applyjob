from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List


MEMORY_PATH = Path("data/question_memory.json")


def _normalize(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokens(text: str) -> set[str]:
    return {t for t in _normalize(text).split() if len(t) > 2}


class QuestionMemory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or MEMORY_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> List[Dict[str, str]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return [x for x in payload if isinstance(x, dict)]
            return []
        except Exception:
            return []

    def save(self, records: List[Dict[str, str]]) -> None:
        self.path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    def lookup(self, question: str) -> str:
        q_tokens = _tokens(question)
        if not q_tokens:
            return ""
        best_score = 0.0
        best_answer = ""
        for row in self.load():
            q_old = row.get("question", "")
            ans = row.get("answer", "")
            if not q_old or not ans:
                continue
            old_tokens = _tokens(q_old)
            if not old_tokens:
                continue
            overlap = len(q_tokens & old_tokens)
            score = overlap / max(len(q_tokens), len(old_tokens))
            if score > best_score:
                best_score = score
                best_answer = ans
        return best_answer if best_score >= 0.45 else ""

    def remember(self, question: str, answer: str) -> None:
        question = _normalize(question)
        answer = (answer or "").strip()
        if not question or not answer:
            return
        rows = self.load()
        for row in rows:
            if _normalize(row.get("question", "")) == question:
                row["answer"] = answer
                self.save(rows)
                return
        rows.append({"question": question, "answer": answer})
        self.save(rows)
