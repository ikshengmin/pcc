"""Source-only workflow routing contract shared by existing CI policy tests."""
import re


def has_only_validation_plan_dependency(body):
    """Original producer jobs remain independent after read-only path planning."""
    return re.findall(r"^    needs: (.+)$", body, re.MULTILINE) == ["validation-plan"]
