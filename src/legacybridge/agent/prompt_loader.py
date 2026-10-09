"""Loads a prompt, optionally without the passages that depend on the semantic layer (ADR-013).

The ``no-semantic`` ablation removes the ontology. A prompt that still told the model to call
``explain_business_term`` or to read the rule engine's warnings would measure confusion about a
tool that does not exist, not the value of the layer. So those passages are marked in the prompt
files and dropped here, mechanically; nothing is rewritten or added except the glue that closes a
sentence.

    <!--semantic-->...<!--/semantic-->   kept in the normal prompt, removed in the ablation
    <!--plain-->...<!--/plain-->         the reverse: only in the ablation (punctuation glue)

With ``semantic=True`` the result is the prompt exactly as it was before the markers existed.
"""

from __future__ import annotations

import re
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent / "prompts"

# A block on its own lines takes its line breaks with it; an inline one only its own text.
_BLOCK_SEMANTIC = re.compile(
    r"^<!--semantic-->\n(.*?)^<!--/semantic-->\n?", re.DOTALL | re.MULTILINE
)
_INLINE_SEMANTIC = re.compile(r"<!--semantic-->(.*?)<!--/semantic-->", re.DOTALL)
_PLAIN = re.compile(r"<!--plain-->(.*?)<!--/plain-->", re.DOTALL)


def render_prompt(text: str, semantic: bool = True) -> str:
    if semantic:
        text = _BLOCK_SEMANTIC.sub(lambda m: m.group(1), text)
        text = _INLINE_SEMANTIC.sub(lambda m: m.group(1), text)
        return _PLAIN.sub("", text)
    text = _BLOCK_SEMANTIC.sub("", text)
    text = _INLINE_SEMANTIC.sub("", text)
    return _PLAIN.sub(lambda m: m.group(1), text).rstrip() + "\n"  # no dangling blank lines


def load_prompt(name: str, semantic: bool = True) -> str:
    return render_prompt((PROMPTS / name).read_text(encoding="utf-8"), semantic)
