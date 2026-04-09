from dataclasses import dataclass


@dataclass
class JobCard:
    title: str
    company: str
    location: str
    url: str
    is_easy_apply: bool


@dataclass
class ApplyResult:
    title: str
    company: str
    url: str
    channel: str
    status: str
    note: str
