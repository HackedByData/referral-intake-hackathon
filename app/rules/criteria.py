"""Flatten IHH's admission-criteria.json into Rule objects grouped for parallel evaluation."""
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.schemas import Rule

GROUP_NAMES = ("function", "behavior", "infection_docs", "disease",
               "systems_a", "systems_b", "systems_c", "systems_d")

SECTION_GROUP = {
    "ADLs": "function", "Mobility": "function", "Cognition": "function",
    "Mental Health": "behavior", "Other Factors": "behavior",
    "Infection Control": "infection_docs", "Infection": "infection_docs", "End of life care": "infection_docs",
    "Diabetes": "disease", "Cardiac": "disease", "Respiratory Illness": "disease",
}
SYSTEM_GROUP = {
    "Integumentary": "systems_a", "Ophthalmology": "systems_a", "Respiratory": "systems_a",
    "Musculoskeletal": "systems_b", "GI": "systems_b",
    "Endocrine": "systems_c", "GU": "systems_c",
    "Circulatory System": "systems_d", "Neurology": "systems_d",
}
FORM_GROUP = {
    "FORM-M1": "function", "FORM-M2": "function", "FORM-M3": "function", "FORM-M4": "function", "FORM-M5": "function",
    "FORM-X1": "behavior", "FORM-X2": "behavior", "FORM-X5": "behavior", "FORM-X7": "behavior",
    "FORM-X4": "infection_docs",
}


@dataclass(frozen=True)
class Criteria:
    rules: list[Rule]
    precedence: str
    kinds: dict[str, str]

    def by_id(self) -> dict[str, Rule]:
        return {r.id: r for r in self.rules}

    def groups(self) -> dict[str, list[Rule]]:
        out: dict[str, list[Rule]] = {g: [] for g in GROUP_NAMES}
        for r in self.rules:
            if r.group != "superseded":
                out[r.group].append(r)
        return out

    def superseded(self) -> list[Rule]:
        return [r for r in self.rules if r.group == "superseded"]

    def checklist(self) -> list[Rule]:
        return [r for r in self.rules if r.source == "checklist"]


@lru_cache(maxsize=4)
def _load(path_str: str) -> Criteria:
    data = json.loads(Path(path_str).read_text("utf-8"))
    rules: list[Rule] = []
    for item in data["general"]["any_of"]:
        rules.append(Rule(id=item["id"], source="outline", section="General admission criteria (meets one or more)",
                          kind="must", text=item["text"], group="function"))
    for item in data["rules"]:
        rules.append(Rule(id=item["id"], source="outline", section=item["section"], kind=item["kind"],
                          text=item["text"], group=SECTION_GROUP[item["section"]],
                          human_review=item.get("human_review", False)))
    for system in data["by_system"]:
        name = system["system"]
        for item in system.get("eligible", []):
            rules.append(Rule(id=item["id"], source="by_system", section=f"{name} (eligible)",
                              kind=item.get("kind", "eligible"), text=item["text"], group=SYSTEM_GROUP[name]))
        for item in system.get("ineligible", []):
            rules.append(Rule(id=item["id"], source="by_system", section=f"{name} (ineligible)",
                              kind="exclude", text=item["text"], group=SYSTEM_GROUP[name]))
    for item in data["referral_checklist"]["items"]:
        rules.append(Rule(id=item["id"], source="checklist", section="Referral checklist", kind="document",
                          text=item["text"], group="infection_docs"))
    form = data["referral_form_screen"]
    for key, kind, section in (("must_meet_all", "must", "Referral form: must meet all"),
                               ("not_eligible_if", "exclude", "Referral form: not eligible if")):
        for item in form[key]:
            sup = item.get("superseded_by")
            rules.append(Rule(id=item["id"], source="form", section=section, kind=kind, text=item["text"],
                              group="superseded" if sup else FORM_GROUP[item["id"]], superseded_by=sup))
    return Criteria(rules=rules, precedence=data["precedence"], kinds=data["kinds"])


def load_criteria(path: Path | None = None) -> Criteria:
    return _load(str(path or get_settings().criteria_path))
