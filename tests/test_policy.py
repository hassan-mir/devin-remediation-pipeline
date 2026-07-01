from app.models import AutonomyTier
from app.policy import tier_from_score
from app.runtime import runtime


def test_tier_boundaries():
    am = runtime.hitl_auto_merge_below
    pr = runtime.hitl_auto_pr_below
    af = runtime.hitl_approve_first_below
    assert tier_from_score(am - 1) == AutonomyTier.auto_merge
    assert tier_from_score(am) == AutonomyTier.auto_pr
    assert tier_from_score(pr - 1) == AutonomyTier.auto_pr
    assert tier_from_score(pr) == AutonomyTier.approve_first
    assert tier_from_score(af - 1) == AutonomyTier.approve_first
    assert tier_from_score(af) == AutonomyTier.human_only
    assert tier_from_score(100) == AutonomyTier.human_only
