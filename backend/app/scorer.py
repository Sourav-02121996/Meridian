import re
from functools import lru_cache

import numpy as np

from .config import get_settings

SKILLS = {
    "python",
    "javascript",
    "typescript",
    "java",
    "kotlin",
    "swift",
    "golang",
    "go",
    "rust",
    "c++",
    "c#",
    "react",
    "angular",
    "vue",
    "next.js",
    "node.js",
    "django",
    "flask",
    "fastapi",
    "spring",
    "sql",
    "postgresql",
    "mysql",
    "mongodb",
    "redis",
    "graphql",
    "rest",
    "aws",
    "azure",
    "gcp",
    "docker",
    "kubernetes",
    "terraform",
    "linux",
    "git",
    "ci/cd",
    "pytorch",
    "tensorflow",
    "machine learning",
    "spark",
    "kafka",
    "snowflake",
    "databricks",
    "html",
    "css",
    "tailwind",
}
HEADER = re.compile(
    r"^(requirements?|qualifications?|what you(?:'|’)ll need|must have|skills|responsibilities)\b",
    re.I,
)


@lru_cache(maxsize=1)
def get_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(get_settings().model_name)


def _lines(text: str) -> list[str]:
    raw = [re.sub(r"^[\s•*\-–—\d.)]+", "", x).strip() for x in text.splitlines()]
    start = next((i for i, line in enumerate(raw) if HEADER.match(line)), 0)
    chosen = [line for line in raw[start:] if 3 <= len(line.split()) <= 40]
    if not chosen:
        chosen = [line for line in raw if 3 <= len(line.split()) <= 40]
    return chosen[:80]


def _skills(text: str) -> list[str]:
    lower = text.lower()
    found = {skill for skill in SKILLS if re.search(rf"(?<!\w){re.escape(skill)}(?!\w)", lower)}
    tokens = re.findall(r"\b(?:[A-Z][a-z]+[A-Z]\w*|[A-Za-z]+\.[A-Za-z0-9.]+|C\+\+|C#)\b", text)
    found.update(token.lower() for token in tokens if len(token) > 1)
    return sorted(found)


def _cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.matmul(a, b.T)


def score_job(description: str, resume: str) -> dict:
    if not description.strip() or not resume.strip():
        return {
            "score": 0.0,
            "requirement_coverage": 0.0,
            "skill_coverage": 0.0,
            "global_similarity": 0.0,
            "matched_skills": [],
            "missing_skills": _skills(description),
            "weak_requirements": _lines(description),
        }
    model = get_model()
    requirements, resume_lines = _lines(description), _lines(resume)
    weak: list[str] = []
    req_cov = 0.0
    if requirements and resume_lines:
        req_emb = model.encode(requirements, normalize_embeddings=True)
        res_emb = model.encode(resume_lines, normalize_embeddings=True)
        best = _cosine_matrix(np.asarray(req_emb), np.asarray(res_emb)).max(axis=1)
        req_cov = float(np.clip(best.mean(), 0, 1))
        weak = [
            requirement for requirement, similarity in zip(requirements, best) if similarity < 0.45
        ]
    jd_skills = _skills(description)
    resume_lower = resume.lower()
    matched = [s for s in jd_skills if re.search(rf"(?<!\w){re.escape(s)}(?!\w)", resume_lower)]
    missing = [s for s in jd_skills if s not in matched]
    skill_cov = len(matched) / len(jd_skills) if jd_skills else 1.0
    docs = model.encode([description[:5000], resume[:5000]], normalize_embeddings=True)
    global_sim = float(np.clip(np.dot(docs[0], docs[1]), 0, 1))
    score = round((0.55 * req_cov + 0.35 * skill_cov + 0.10 * global_sim) * 100, 1)
    return {
        "score": score,
        "requirement_coverage": req_cov,
        "skill_coverage": skill_cov,
        "global_similarity": global_sim,
        "matched_skills": matched,
        "missing_skills": missing,
        "weak_requirements": weak,
    }
