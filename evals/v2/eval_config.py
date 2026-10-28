"""Evaluation configuration — scoring rubrics and pass/fail gates."""

from __future__ import annotations

# Gate thresholds (fraction of questions that must pass per category)
GATES = {
    "overall": 0.75,
    "factual_lookup": 0.70,
    "procedure_steps": 0.65,
    "troubleshooting": 0.60,
    "entity_centric": 0.65,
    "temporal": 0.60,
    "cross_domain_synthesis": 0.60,
    "comparison": 0.60,
    "taxonomy_navigation": 0.65,
    "graph_traversal": 0.60,
    "adversarial_no_answer": 0.85,
}

# Citation verification gate
CITATION_VERIFICATION_GATE = 0.90

# Scoring rubric
SCORING = {
    "must_include_terms": {
        "weight": 0.3,
        "description": "Answer must contain required terms",
    },
    "must_not_include_terms": {
        "weight": 0.1,
        "description": "Answer must not contain forbidden terms",
    },
    "must_cite_paths": {
        "weight": 0.3,
        "description": "Answer must cite the expected source paths",
    },
    "min_citations": {
        "weight": 0.2,
        "description": "Answer must have at least N verified citations",
    },
    "expected_intent": {
        "weight": 0.1,
        "description": "Agent must classify the correct intent",
    },
}
