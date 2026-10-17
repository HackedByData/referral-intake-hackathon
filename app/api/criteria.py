"""Read-only view of IHH's admission criteria, so the UI can show every criterion (even ones with no result)."""
from fastapi import APIRouter

from app.rules.criteria import load_criteria
from app.schemas import CriteriaView

router = APIRouter(prefix="/api", tags=["criteria"])


@router.get("/criteria", response_model=CriteriaView)
def get_criteria():
    c = load_criteria()
    return CriteriaView(precedence=c.precedence, kinds=c.kinds, rules=c.rules)
