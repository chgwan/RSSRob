import re
from dataclasses import dataclass, field
from typing import List, Optional


def parse_terms(value) -> List[str]:
    """Normalize an include/exclude value into a clean list of terms.

    Accepts a list (from YAML) or a comma/newline-separated string (from a web
    form). Trims whitespace and drops empties."""
    if value is None:
        return []
    parts = re.split(r"[,\n]", value) if isinstance(value, str) else list(value)
    return [str(t).strip() for t in parts if str(t).strip()]


def _matches_any(value: str, terms: List[str], regex: bool) -> bool:
    if regex:
        for p in terms:
            try:
                if re.search(p, value, re.I):
                    return True
            except re.error:
                continue
        return False
    low = value.lower()
    return any(t.lower() in low for t in terms)


@dataclass
class FeedFilter:
    include: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    field_name: str = "title"
    regex: bool = False

    def keeps(self, item) -> bool:
        """True if item passes include (or include empty) AND matches no exclude."""
        value = getattr(item, self.field_name, None) or ""
        if self.include and not _matches_any(value, self.include, self.regex):
            return False
        if self.exclude and _matches_any(value, self.exclude, self.regex):
            return False
        return True


def build_filter(raw: Optional[dict]) -> Optional[FeedFilter]:
    """Build a FeedFilter from a config dict, or None when nothing to filter."""
    if not raw:
        return None
    include = parse_terms(raw.get("include"))
    exclude = parse_terms(raw.get("exclude"))
    if not include and not exclude:
        return None
    return FeedFilter(
        include=include,
        exclude=exclude,
        field_name=raw.get("field") or "title",
        regex=bool(raw.get("regex")),
    )


def apply_filter(items, include, exclude, field, regex):
    """Tag each item kept/dropped for preview (used by the web playground).

    Keep rule: passes include (or none given) AND matches no exclude term."""
    inc, exc = parse_terms(include), parse_terms(exclude)
    field_name = field or "title"
    results = []
    for it in items:
        value = getattr(it, field_name, None) or ""
        kept, reason = True, "kept"
        if inc and not _matches_any(value, inc, regex):
            kept, reason = False, "no include match"
        elif exc and _matches_any(value, exc, regex):
            kept, reason = False, "excluded"
        results.append({"item": it, "kept": kept, "reason": reason})
    return results
