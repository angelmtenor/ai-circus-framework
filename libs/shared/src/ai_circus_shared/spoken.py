"""A spoken chat turn: the reply is heard, not read.

agui-voice runs the same `/agui/{scenario_slug}` agent a typed turn does (docs/adr/0033), and marks its runs with
`forwardedProps: {"spoken": true}`. The agents (assistant, rag-agent, form-agent) then add `SPOKEN_REPLY_INSTRUCTIONS`
to their system prompt: without it a voice answer is as long as a typed one, lists and tables included, and took about
two minutes to say. The tools, the data and every check stay exactly as for a typed turn.
"""

from __future__ import annotations

from collections.abc import Mapping

SPOKEN_PROP = "spoken"

SPOKEN_REPLY_INSTRUCTIONS = (
    "\n\nThis turn is spoken: the user is talking to you by voice and hears your reply read aloud. Answer in two to "
    "four short sentences, about 60 words at most, as a person would say them: no lists, tables, headings, bold, "
    "links, code or emoji, and round numbers the way you would say them aloud. Lead with the answer itself. Use your "
    "tools exactly as you would for a typed question. If there is more worth seeing (a ranking, a table, the figures "
    "behind the answer), say in one sentence that you can show it on screen, and stop there."
)


def is_spoken(forwarded_props: object) -> bool:
    """True when a run's `forwardedProps` mark it as a spoken turn (exactly `true`, nothing truthy)."""
    return isinstance(forwarded_props, Mapping) and forwarded_props.get(SPOKEN_PROP) is True


def spoken_props() -> dict[str, bool]:
    """The `forwardedProps` agui-voice sends with each spoken turn."""
    return {SPOKEN_PROP: True}
