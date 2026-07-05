"""Bounded aesthetic profile registry for local ViewSpec compilation."""

from __future__ import annotations

import re

from viewspec.types import DEFAULT_STYLE_TOKEN_VALUES


AESTHETIC_PROFILE_PREFIX = "aesthetic."
AESTHETIC_PROFILE_TOKENS = (
    "aesthetic.calm_ops",
    "aesthetic.premium_saas",
    "aesthetic.data_dense",
    "aesthetic.editorial_product",
    "aesthetic.executive_review",
)
MAX_AESTHETIC_PROFILE_DECLARATIONS = 32
MAX_AESTHETIC_PROFILE_CSS_BYTES = 2048
MIN_AESTHETIC_PROFILE_STYLE_CHANGES = 6
MIN_AESTHETIC_PROFILE_CATEGORIES = 3
MAX_AESTHETIC_PROFILE_LAYOUT_COLUMNS = 3
MAX_AESTHETIC_PROFILE_SPAN_COLUMNS = 3
LAYOUT_EMPHASIS_VALUES = frozenset({"featured"})

AESTHETIC_PROFILE_LAYOUT_ROLES = frozenset({"content_grid", "metric_grid", "metric_card"})
AESTHETIC_PROFILE_LAYOUT_PROPS = {
    "aesthetic.calm_ops": {
        "content_grid": {"columns": 2},
        "metric_grid": {"columns": 2},
    },
    "aesthetic.premium_saas": {
        "content_grid": {"columns": 2},
        "metric_grid": {"columns": 2},
        "metric_card": {"span_columns": 2, "layout_emphasis": "featured"},
    },
    "aesthetic.data_dense": {
        "content_grid": {"columns": 3},
        "metric_grid": {"columns": 3},
    },
    "aesthetic.editorial_product": {
        "content_grid": {"columns": 2},
        "metric_grid": {"columns": 1},
    },
    "aesthetic.executive_review": {
        "content_grid": {"columns": 2},
        "metric_grid": {"columns": 2},
        "metric_card": {"span_columns": 2, "layout_emphasis": "featured"},
    },
}

AESTHETIC_ALLOWED_CSS_PROPERTIES = frozenset(
    {
        "background",
        "background-color",
        "border",
        "border-color",
        "border-radius",
        "box-shadow",
        "color",
        "font-family",
        "font-size",
        "font-weight",
        "gap",
        "letter-spacing",
        "line-height",
        "max-width",
        "padding",
        "text-transform",
    }
)
UNSAFE_AESTHETIC_CSS_RE = re.compile(r"(?i)(@import|url\s*\(|expression\s*\(|javascript:|vbscript:|data:|var\s*\()")


class AestheticProfileError(ValueError):
    """Stable-code aesthetic profile registry failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


AESTHETIC_PROFILE_STYLE_VALUES: dict[str, dict[str, str]] = {
    # Five deliberately far-apart identities. The grounds alone must read as five
    # different products at a glance: sage / lavender / dark terminal / cream paper / steel.
    "aesthetic.calm_ops": {
        "palette.temperature": "background-color: #e9f3ee;",
        "tone.neutral": "color: #16241f; font-family: ui-sans-serif, system-ui, sans-serif;",
        "tone.muted": "color: #5d7268;",
        "tone.accent": "color: #0f766e; font-weight: 800;",
        "action.accent": "background-color: #0f766e; color: #ffffff; border-radius: 10px;",
        "surface.subtle": "background: #ffffff; border: 1px solid #cfe2da; border-radius: 14px;",
        "surface.strong": "background: #dcefe7; border: 1px solid #8ccdc2; border-radius: 16px;",
        "density.compact": "gap: 0.32rem; padding: 0.34rem 0.5rem;",
        "density.regular": "gap: 0.78rem; padding: 0.72rem 0.96rem;",
        "density.airy": "gap: 1.2rem; padding: 1rem 1.18rem;",
        "emphasis.high": "font-weight: 760; letter-spacing: -0.012em;",
        "rhythm.hierarchy": "font-size: 1.12rem; font-weight: 760; line-height: 1.36;",
        "narrative.flow": "max-width: 72ch; line-height: 1.74;",
    },
    "aesthetic.premium_saas": {
        "palette.temperature": "background-color: #ece9fe;",
        "tone.neutral": "color: #17123a; font-family: ui-sans-serif, system-ui, sans-serif;",
        "tone.muted": "color: #6b668d;",
        "tone.accent": "color: #4f46e5; font-weight: 860;",
        "action.accent": "background-color: #4f46e5; color: #ffffff; border-radius: 999px;",
        "surface.subtle": "background: #ffffff; border: 1px solid #d6ccff; border-radius: 20px; box-shadow: 0 24px 60px rgb(79 70 229 / 0.22);",
        "surface.strong": "background: #e5e0ff; border: 1px solid #b7a5fb; border-radius: 22px;",
        "density.compact": "gap: 0.48rem; padding: 0.52rem 0.76rem;",
        "density.regular": "gap: 1rem; padding: 0.94rem 1.22rem;",
        "density.airy": "gap: 1.55rem; padding: 1.35rem 1.65rem;",
        "emphasis.high": "font-weight: 860; letter-spacing: -0.03em;",
        "rhythm.hierarchy": "font-size: 1.44rem; font-weight: 860; line-height: 1.1;",
        "narrative.flow": "max-width: 64ch; line-height: 1.68;",
    },
    "aesthetic.data_dense": {
        # The only dark projection: a terminal identity, not a tint variation.
        "palette.temperature": "background-color: #0f172a;",
        "tone.neutral": "color: #e2e8f0; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;",
        "tone.muted": "color: #8fa3bd;",
        "tone.accent": "color: #60a5fa; font-weight: 780;",
        "action.accent": "background-color: #3b82f6; color: #ffffff; border-radius: 4px;",
        "surface.subtle": "background: #16213a; border: 1px solid #2c3e5f; border-radius: 5px;",
        "surface.strong": "background: #1d2b4a; border: 1px solid #3e5c8f; border-radius: 5px;",
        "density.compact": "gap: 0.16rem; padding: 0.18rem 0.3rem;",
        "density.regular": "gap: 0.34rem; padding: 0.3rem 0.44rem;",
        "density.airy": "gap: 0.7rem; padding: 0.58rem 0.76rem;",
        "emphasis.high": "font-weight: 720; letter-spacing: 0;",
        "rhythm.hierarchy": "font-size: 0.92rem; font-weight: 740; line-height: 1.18;",
        "narrative.flow": "max-width: 92ch; line-height: 1.42;",
    },
    "aesthetic.editorial_product": {
        "palette.temperature": "background-color: #f8f1e5;",
        "tone.neutral": "color: #2b2118; font-family: ui-serif, Georgia, Cambria, serif;",
        "tone.muted": "color: #85715f;",
        "tone.accent": "color: #be123c; font-weight: 780;",
        "action.accent": "background-color: #be123c; color: #ffffff; border-radius: 18px;",
        "surface.subtle": "background: #fffcf5; border: 1px solid #e7d9bf; border-radius: 24px; box-shadow: 0 3px 0 rgb(190 18 60 / 0.16);",
        "surface.strong": "background: #f3e8d2; border: 1px solid #d9c49a; border-radius: 26px;",
        "density.compact": "gap: 0.56rem; padding: 0.52rem 0.78rem;",
        "density.regular": "gap: 1.08rem; padding: 0.98rem 1.26rem;",
        "density.airy": "gap: 1.8rem; padding: 1.55rem 1.8rem;",
        "emphasis.high": "font-weight: 840; letter-spacing: 0;",
        "rhythm.hierarchy": "font-size: 1.66rem; font-weight: 840; line-height: 1.05;",
        "narrative.flow": "max-width: 56ch; line-height: 1.95;",
    },
    "aesthetic.executive_review": {
        "palette.temperature": "background-color: #e2e9f1;",
        "tone.neutral": "color: #0b1f33; font-family: ui-sans-serif, system-ui, sans-serif;",
        "tone.muted": "color: #51647a;",
        "tone.accent": "color: #0f5f72; font-weight: 820; text-transform: uppercase;",
        "action.accent": "background-color: #0b1f33; color: #ffffff; border-radius: 6px;",
        "surface.subtle": "background: #f7fafc; border: 1px solid #b9c8d8; border-radius: 4px; box-shadow: 0 10px 24px rgb(15 23 42 / 0.10);",
        "surface.strong": "background: #d3dfeb; border: 1px solid #8ea7b8; border-radius: 4px;",
        "density.compact": "gap: 0.28rem; padding: 0.3rem 0.46rem;",
        "density.regular": "gap: 0.58rem; padding: 0.56rem 0.78rem;",
        "density.airy": "gap: 0.95rem; padding: 0.86rem 1.05rem;",
        "emphasis.high": "font-weight: 800; letter-spacing: -0.006em;",
        "rhythm.hierarchy": "font-size: 1.06rem; font-weight: 800; line-height: 1.22;",
        "narrative.flow": "max-width: 78ch; line-height: 1.56;",
    },
}


def is_aesthetic_profile_token(token: str) -> bool:
    return isinstance(token, str) and token.startswith(AESTHETIC_PROFILE_PREFIX)


def profile_style_values(profile: str) -> dict[str, str]:
    validate_aesthetic_profile_registry()
    values = AESTHETIC_PROFILE_STYLE_VALUES.get(profile)
    if values is None:
        raise AestheticProfileError("AESTHETIC_PROFILE_UNKNOWN", f"Unknown aesthetic profile {profile}.")
    return dict(values)


def profile_layout_props(profile: str) -> dict[str, dict[str, int | str]]:
    validate_aesthetic_profile_registry()
    values = AESTHETIC_PROFILE_LAYOUT_PROPS.get(profile)
    if values is None:
        raise AestheticProfileError("AESTHETIC_PROFILE_UNKNOWN", f"Unknown aesthetic profile {profile}.")
    return {role: dict(props) for role, props in values.items()}


def profile_style_facts(profile: str) -> dict[str, object]:
    values = profile_style_values(profile)
    changed_tokens = sorted(
        token
        for token, css in values.items()
        if DEFAULT_STYLE_TOKEN_VALUES.get(token, "").strip() != css.strip()
    )
    categories = sorted({token.split(".", 1)[0] for token in changed_tokens if "." in token})
    changed_values = {token: values[token] for token in changed_tokens}
    return {
        "changed_token_count": len(changed_tokens),
        "changed_tokens": changed_tokens,
        "category_count": len(categories),
        "categories": categories,
        "declaration_count": len(_css_declarations(changed_values)),
    }


def validate_aesthetic_profile_registry() -> None:
    for profile in AESTHETIC_PROFILE_TOKENS:
        values = AESTHETIC_PROFILE_STYLE_VALUES.get(profile)
        if values is None:
            raise AestheticProfileError("AESTHETIC_DEFAULT_REGRESSION", f"Missing profile defaults for {profile}.")
        _validate_profile_style_values(profile, values)
        layout_props = AESTHETIC_PROFILE_LAYOUT_PROPS.get(profile)
        if layout_props is None:
            raise AestheticProfileError("AESTHETIC_DEFAULT_REGRESSION", f"Missing profile layout defaults for {profile}.")
        _validate_profile_layout_props(profile, layout_props)
        changed_tokens = {
            token
            for token, css in values.items()
            if DEFAULT_STYLE_TOKEN_VALUES.get(token, "").strip() != css.strip()
        }
        categories = {token.split(".", 1)[0] for token in changed_tokens if "." in token}
        if (
            len(changed_tokens) < MIN_AESTHETIC_PROFILE_STYLE_CHANGES
            or len(categories) < MIN_AESTHETIC_PROFILE_CATEGORIES
        ):
            raise AestheticProfileError(
                "AESTHETIC_DEFAULT_REGRESSION",
                f"{profile} must change at least {MIN_AESTHETIC_PROFILE_STYLE_CHANGES} style projections across {MIN_AESTHETIC_PROFILE_CATEGORIES} categories.",
            )


def _validate_profile_layout_props(profile: str, values: dict[str, dict[str, int]]) -> None:
    if profile not in AESTHETIC_PROFILE_TOKENS:
        raise AestheticProfileError("AESTHETIC_PROFILE_UNKNOWN", f"Unknown aesthetic profile {profile}.")
    if not isinstance(values, dict) or not values:
        raise AestheticProfileError("AESTHETIC_PROFILE_LAYOUT_UNSAFE", f"{profile} must declare closed layout props.")
    for role, props in values.items():
        if role not in AESTHETIC_PROFILE_LAYOUT_ROLES:
            raise AestheticProfileError("AESTHETIC_PROFILE_LAYOUT_UNSAFE", f"{profile} targets unsupported layout role {role}.")
        if role in {"content_grid", "metric_grid"}:
            if set(props) != {"columns"}:
                raise AestheticProfileError("AESTHETIC_PROFILE_LAYOUT_UNSAFE", f"{profile} may only set grid layout columns.")
            columns = props["columns"]
            if not isinstance(columns, int) or isinstance(columns, bool) or not 1 <= columns <= MAX_AESTHETIC_PROFILE_LAYOUT_COLUMNS:
                raise AestheticProfileError(
                    "AESTHETIC_PROFILE_LAYOUT_UNSAFE",
                    f"{profile} layout columns must be between 1 and {MAX_AESTHETIC_PROFILE_LAYOUT_COLUMNS}.",
                )
        elif role == "metric_card":
            if not set(props).issubset({"span_columns", "layout_emphasis"}) or not props:
                raise AestheticProfileError("AESTHETIC_PROFILE_LAYOUT_UNSAFE", f"{profile} may only set metric card span columns and layout emphasis.")
            span_columns = props.get("span_columns")
            if span_columns is not None and (
                not isinstance(span_columns, int)
                or isinstance(span_columns, bool)
                or not 1 <= span_columns <= MAX_AESTHETIC_PROFILE_SPAN_COLUMNS
            ):
                raise AestheticProfileError(
                    "AESTHETIC_PROFILE_LAYOUT_UNSAFE",
                    f"{profile} metric card span columns must be between 1 and {MAX_AESTHETIC_PROFILE_SPAN_COLUMNS}.",
                )
            layout_emphasis = props.get("layout_emphasis")
            if layout_emphasis is not None and layout_emphasis not in LAYOUT_EMPHASIS_VALUES:
                allowed = ", ".join(sorted(LAYOUT_EMPHASIS_VALUES))
                raise AestheticProfileError(
                    "AESTHETIC_PROFILE_LAYOUT_UNSAFE",
                    f"{profile} metric card layout emphasis must be one of: {allowed}.",
                )


def _validate_profile_style_values(profile: str, values: dict[str, str]) -> None:
    if profile not in AESTHETIC_PROFILE_TOKENS:
        raise AestheticProfileError("AESTHETIC_PROFILE_UNKNOWN", f"Unknown aesthetic profile {profile}.")
    total_css = " ".join(values.values())
    if len(total_css.encode("utf-8")) > MAX_AESTHETIC_PROFILE_CSS_BYTES:
        raise AestheticProfileError("AESTHETIC_PROFILE_UNSAFE_STYLE", f"{profile} exceeds {MAX_AESTHETIC_PROFILE_CSS_BYTES} CSS bytes.")
    declarations = _css_declarations(values)
    if len(declarations) > MAX_AESTHETIC_PROFILE_DECLARATIONS:
        raise AestheticProfileError(
            "AESTHETIC_PROFILE_UNSAFE_STYLE",
            f"{profile} exceeds {MAX_AESTHETIC_PROFILE_DECLARATIONS} CSS declarations.",
        )
    for prop, value in declarations:
        if prop not in AESTHETIC_ALLOWED_CSS_PROPERTIES:
            raise AestheticProfileError("AESTHETIC_PROFILE_UNSAFE_STYLE", f"{profile} uses unsupported CSS property {prop}.")
        if "<" in value or ">" in value or UNSAFE_AESTHETIC_CSS_RE.search(value):
            raise AestheticProfileError("AESTHETIC_PROFILE_UNSAFE_STYLE", f"{profile} uses unsafe CSS value for {prop}.")


def _css_declarations(values: dict[str, str]) -> list[tuple[str, str]]:
    declarations: list[tuple[str, str]] = []
    for token, css in values.items():
        if token not in DEFAULT_STYLE_TOKEN_VALUES:
            raise AestheticProfileError("AESTHETIC_PROFILE_UNSAFE_STYLE", f"Aesthetic profile token override {token} is not a local V1 style token.")
        if not isinstance(css, str):
            raise AestheticProfileError("AESTHETIC_PROFILE_UNSAFE_STYLE", f"Aesthetic profile value for {token} must be CSS text.")
        for raw in css.split(";"):
            declaration = raw.strip()
            if not declaration:
                continue
            if ":" not in declaration:
                raise AestheticProfileError("AESTHETIC_PROFILE_UNSAFE_STYLE", f"Aesthetic profile declaration {declaration} is malformed.")
            prop, value = declaration.split(":", 1)
            declarations.append((prop.strip().lower(), value.strip()))
    return declarations


__all__ = [
    "AESTHETIC_PROFILE_PREFIX",
    "AESTHETIC_PROFILE_TOKENS",
    "AESTHETIC_PROFILE_LAYOUT_PROPS",
    "AESTHETIC_PROFILE_LAYOUT_ROLES",
    "AESTHETIC_PROFILE_STYLE_VALUES",
    "AestheticProfileError",
    "is_aesthetic_profile_token",
    "profile_layout_props",
    "profile_style_facts",
    "profile_style_values",
    "validate_aesthetic_profile_registry",
]
