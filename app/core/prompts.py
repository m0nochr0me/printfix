"""AI prompt templates for document diagnosis and fix orchestration."""

from typing import Literal

from jinja2 import Template

from app.util.base_dir import get_module_root


def get_prompt(
    prompt_name: Literal[
        "visual_inspection",
        "structural_review",
        "merge_diagnosis",
        "fix_planning",
        "verification",
    ],
) -> Template:
    """Load and return a Jinja2 template for the specified prompt."""
    base_dir = get_module_root("app")
    prompt_path = base_dir / "prompts" / f"{prompt_name}.yaml.j2"
    with prompt_path.open("r", encoding="utf-8") as f:
        prompt_content = f.read()
    return Template(prompt_content)
