from __future__ import annotations

from importlib import resources

DEFAULT_GLOSSARY_TERMS = (
    "AE2",
    "ME",
    "GuideME",
    "Powah",
    "Advanced AE",
    "ExtendedAE",
    "Occultism",
    "Theurgy",
    "JDT",
    "Patchouli",
    "Modonomicon",
    "FTB Quests",
)


def build_glossary_prompt(extra_terms: list[str] | None = None) -> str:
    terms = load_default_glossary_terms()
    if extra_terms:
        terms.extend(term for term in extra_terms if term and term not in terms)
    return "Keep these names in English unless a widely used Chinese community translation is obvious: " + ", ".join(terms)


def build_style_prompt() -> str:
    try:
        return resources.files("mc_han.data").joinpath("style_guide.md").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        return (
            "Translate into natural Simplified Chinese used by Minecraft players. "
            "Keep resource IDs, file paths, JSON keys, tags, placeholders, and code unchanged."
        )


def load_default_glossary_terms() -> list[str]:
    try:
        text = resources.files("mc_han.data").joinpath("default_glossary.yaml").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        return list(DEFAULT_GLOSSARY_TERMS)
    terms: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            term = stripped[2:].strip()
            if term:
                terms.append(term)
    return terms or list(DEFAULT_GLOSSARY_TERMS)
