"""The hidden-grammar world: an oracle for a small constructed language.

The seed draws one language from a family: its words, a plural suffix on
nouns, an agreement suffix on verbs, and three choices of order (subject,
verb and object as SVO or SOV; an adjective before or after its noun; the
negation particle before the verb or at the end of the sentence). Five rules
then decide whether a sentence is acceptable: the word order, the adjective's
place, the negation's place, the verb agreeing with a plural subject, and a
transitive verb taking exactly one object while an intransitive one takes
none. The agent sees the words and a few acceptable sentences, and asks the
oracle about sentences of its own; the oracle answers acceptable or not, as a
speaker would, and never says why.

    python3 grammar.py setup --workdir W --seed S       the lexicon, the examples, an empty record
    python3 grammar.py observe --workdir W              the counts and the last answer
    python3 grammar.py act ASK --workdir W --seed S      {"sentence": "..."} on stdin
    python3 grammar.py truth --seed S                   the language, for operators

Everything hidden is derived from the seed inside each command, never kept in
the working copy, and every command is deterministic, so a replay rebuilds
the world from the seed and the journal. checks/score.py scores a classifier
on held-out sentences drawn from the same functions.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ASK_CAP = 40
MAX_WORDS = 12
LEXICON = "lexicon.txt"
EXAMPLES = "examples.txt"
ASKED = "asked.tsv"
SENTENCE = re.compile(r"^[a-z]+( [a-z]+)*$")
CONSONANTS = "ptkmnslvdgb"
VOWELS = "aeiou"
SUFFIXES = ("ri", "ka", "lo", "mi", "se", "tu")
# The five rules, as the kinds of sentence that break them.
KINDS = ("order", "adjective", "negation", "agreement", "transitivity")


@dataclass(frozen=True)
class Language:
    nouns: tuple[str, ...]
    intransitive: tuple[str, ...]
    transitive: tuple[str, ...]
    adjectives: tuple[str, ...]
    negation: str
    plural: str
    agreement: str
    order: str
    adjective: str
    negation_at: str

    @property
    def words(self) -> list[str]:
        return sorted({*self.nouns, *self.intransitive, *self.transitive, *self.adjectives, self.negation})


# A noun phrase: the noun, whether it is plural, and its adjective or None.
Phrase = tuple[str, bool, "str | None"]


@dataclass(frozen=True)
class Clause:
    subject: Phrase
    verb: str
    object: Phrase | None
    negated: bool


def language(seed: int) -> Language:
    rng = random.Random(f"hidden-grammar:{seed}")
    plural, agreement = rng.sample(SUFFIXES, 2)
    taken: set[str] = set()

    def word(syllables: int) -> str:
        while True:
            candidate = "".join(rng.choice(CONSONANTS) + rng.choice(VOWELS) for _ in range(syllables))
            forms = {candidate, candidate + plural, candidate + agreement}
            if not forms & taken and not candidate.endswith((plural, agreement)):
                taken.update(forms)
                return candidate

    return Language(
        nouns=tuple(word(2) for _ in range(6)),
        intransitive=tuple(word(2) for _ in range(2)),
        transitive=tuple(word(2) for _ in range(3)),
        adjectives=tuple(word(3) for _ in range(3)),
        negation=word(1),
        plural=plural,
        agreement=agreement,
        order=rng.choice(("SVO", "SOV")),
        adjective=rng.choice(("before", "after")),
        negation_at=rng.choice(("verb", "end")),
    )


def _phrase(lang: Language, phrase: Phrase, adjective: str) -> list[str]:
    noun, plural, adj = phrase
    form = noun + (lang.plural if plural else "")
    if adj is None:
        return [form]
    return [adj, form] if adjective == "before" else [form, adj]


def realize(
    lang: Language, clause: Clause, *, order: str | None = None, adjective: str | None = None,
    negation_at: str | None = None, agree: bool = True,
) -> str:
    """The clause as a sentence, under the language's rules or with one of
    them overridden (the way an ungrammatical sentence is made)."""
    order = order or lang.order
    adjective = adjective or lang.adjective
    negation_at = negation_at or lang.negation_at
    plural_subject = clause.subject[1]
    suffixed = plural_subject if agree else not plural_subject
    verb = [clause.verb + (lang.agreement if suffixed else "")]
    if clause.negated and negation_at == "verb":
        verb = [lang.negation, *verb]
    obj = _phrase(lang, clause.object, adjective) if clause.object is not None else []
    tokens = _phrase(lang, clause.subject, adjective) + (verb + obj if order == "SVO" else obj + verb)
    if clause.negated and negation_at == "end":
        tokens.append(lang.negation)
    return " ".join(tokens)


def phrases(lang: Language) -> list[Phrase]:
    return [(noun, plural, adj) for noun in lang.nouns for plural in (False, True) for adj in (None, *lang.adjectives)]


def clauses(lang: Language) -> list[Clause]:
    """Every well-formed clause: an intransitive verb with no object, a
    transitive one with exactly one."""
    found = []
    for subject in phrases(lang):
        for negated in (False, True):
            found += [Clause(subject, verb, None, negated) for verb in lang.intransitive]
            for verb in lang.transitive:
                found += [Clause(subject, verb, obj, negated) for obj in phrases(lang)]
    return found


_ACCEPTED: dict[int, frozenset[str]] = {}


def acceptable(seed: int) -> frozenset[str]:
    """Every acceptable sentence of the seed's language."""
    if seed not in _ACCEPTED:
        lang = language(seed)
        _ACCEPTED[seed] = frozenset(realize(lang, clause) for clause in clauses(lang))
    return _ACCEPTED[seed]


def normalize(sentence: str) -> str:
    return " ".join(sentence.lower().split())


def accepts(seed: int, sentence: str) -> bool:
    return normalize(sentence) in acceptable(seed)


def broken(seed: int, rng: random.Random, kind: str) -> str:
    """A sentence that breaks exactly the rule `kind`, drawn at random."""
    pool = sorted(variants(seed)[kind])
    while True:
        text = rng.choice(pool)
        if text not in acceptable(seed):
            return text


_VARIANTS: dict[int, dict[str, frozenset[str]]] = {}


def variants(seed: int) -> dict[str, frozenset[str]]:
    """Per rule, every sentence a well-formed clause becomes when that one
    rule is broken: the order, the adjective's place and the negation's place
    flipped, the agreement inverted, an object missing or one too many. A
    variant can still be acceptable by chance; the callers filter those."""
    if seed not in _VARIANTS:
        lang = language(seed)
        every = clauses(lang)
        flip_order = "SOV" if lang.order == "SVO" else "SVO"
        flip_adjective = "after" if lang.adjective == "before" else "before"
        flip_negation = "end" if lang.negation_at == "verb" else "verb"
        wrong = [Clause(subject, verb, None, negated) for subject in phrases(lang)
                 for negated in (False, True) for verb in lang.transitive]
        wrong += [Clause(subject, verb, obj, negated) for subject in phrases(lang) for negated in (False, True)
                  for verb in lang.intransitive for obj in phrases(lang)]
        _VARIANTS[seed] = {
            "order": frozenset(realize(lang, c, order=flip_order) for c in every if c.object is not None),
            "adjective": frozenset(
                realize(lang, c, adjective=flip_adjective) for c in every
                if c.subject[2] is not None or (c.object is not None and c.object[2] is not None)
            ),
            "negation": frozenset(realize(lang, c, negation_at=flip_negation) for c in every if c.negated),
            "agreement": frozenset(realize(lang, c, agree=False) for c in every),
            "transitivity": frozenset(realize(lang, c) for c in wrong),
        }
    return _VARIANTS[seed]


def examples(seed: int) -> list[str]:
    """Five acceptable sentences the agent starts from: one of each kind of
    clause, no contrasts."""
    lang = language(seed)
    rng = random.Random(f"hidden-grammar-examples:{seed}")
    noun = lambda: rng.choice(lang.nouns)  # noqa: E731
    shown = [
        Clause((noun(), False, None), rng.choice(lang.intransitive), None, False),
        Clause((noun(), False, rng.choice(lang.adjectives)), rng.choice(lang.transitive), (noun(), False, None), False),
        Clause((noun(), True, None), rng.choice(lang.intransitive), None, False),
        Clause((noun(), False, None), rng.choice(lang.transitive), (noun(), True, rng.choice(lang.adjectives)), False),
        Clause((noun(), False, None), rng.choice(lang.transitive), (noun(), False, None), True),
    ]
    return [realize(lang, clause) for clause in shown]


def held_out(seed: int, size: int = 400) -> list[tuple[str, bool, str]]:
    """The checker's sentences: half acceptable, half breaking one rule each,
    the five rules equally, as (sentence, acceptable, kind)."""
    lang = language(seed)
    rng = random.Random(f"hidden-grammar-held-out:{seed}")
    pool = clauses(lang)
    found: list[tuple[str, bool, str]] = []
    seen: set[str] = set()
    while len(found) < size // 2:
        text = realize(lang, rng.choice(pool))
        if text not in seen:
            seen.add(text)
            found.append((text, True, "acceptable"))
    per_kind = (size - len(found)) // len(KINDS)
    for kind in KINDS:
        made = 0
        while made < per_kind:
            text = broken(seed, rng, kind)
            if text not in seen:
                seen.add(text)
                found.append((text, False, kind))
                made += 1
    rng.shuffle(found)
    return found


def violated(seed: int, sentence: str) -> list[str]:
    """The rules an unacceptable sentence breaks on its own: the kinds whose
    one-rule variants of a well-formed clause produce it. Empty for an
    acceptable sentence, and for one that breaks several rules at once or is
    not made of the language's words."""
    text = normalize(sentence)
    if text in acceptable(seed):
        return []
    return [kind for kind, made in variants(seed).items() if text in made]


# --- the world's commands ------------------------------------------------------


def _asked(workdir: Path) -> list[tuple[str, bool]]:
    path = workdir / ASKED
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if "\t" in line:
            sentence, answer = line.rsplit("\t", 1)
            rows.append((sentence, answer == "acceptable"))
    return rows


def setup(workdir: Path, seed: int) -> None:
    lang = language(seed)
    (workdir / LEXICON).write_text(
        "# The words of the language, base forms only, in alphabetical order.\n"
        + "\n".join(lang.words) + "\n"
    )
    (workdir / EXAMPLES).write_text(
        "# Five sentences a speaker accepts. Nothing here says what they do not accept.\n"
        + "\n".join(examples(seed)) + "\n"
    )
    (workdir / ASKED).write_text("")


def observe(workdir: Path) -> dict[str, Any]:
    rows = _asked(workdir)
    last = rows[-1] if rows else None
    return {
        "asked": len(rows),
        "asks_left": max(0, ASK_CAP - len(rows)),
        "accepted": sum(1 for _, answer in rows if answer),
        "rejected": sum(1 for _, answer in rows if not answer),
        "last_sentence": last[0] if last else None,
        "last_ok": last[1] if last else None,
    }


def ask(workdir: Path, seed: int, params: dict[str, Any]) -> dict[str, Any]:
    raw = params.get("sentence")
    if not isinstance(raw, str):
        return {"status": "refused", "detail": "ASK takes one parameter, sentence"}
    sentence = normalize(raw)
    if not SENTENCE.match(sentence):
        return {"status": "refused", "detail": "a sentence is lowercase words of the letters a to z, separated by spaces"}
    if len(sentence.split()) > MAX_WORDS:
        return {"status": "refused", "detail": f"a sentence has at most {MAX_WORDS} words"}
    rows = _asked(workdir)
    if len(rows) >= ASK_CAP:
        return {"status": "refused", "detail": f"the speaker has answered {ASK_CAP} questions and will answer no more"}
    ok = accepts(seed, sentence)
    with (workdir / ASKED).open("a") as handle:
        handle.write(f"{sentence}\t{'acceptable' if ok else 'not acceptable'}\n")
    return {"status": "ok", "detail": "acceptable" if ok else "not acceptable", "sentence": sentence, "acceptable": ok}


def truth(seed: int) -> dict[str, Any]:
    lang = language(seed)
    return {
        "seed": seed, "nouns": lang.nouns, "intransitive": lang.intransitive, "transitive": lang.transitive,
        "adjectives": lang.adjectives, "negation": lang.negation, "plural": lang.plural, "agreement": lang.agreement,
        "order": lang.order, "adjective": lang.adjective, "negation_at": lang.negation_at,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="the hidden-grammar world")
    parser.add_argument("command", choices=("setup", "observe", "act", "truth"))
    parser.add_argument("action", nargs="?")
    parser.add_argument("--workdir", type=Path, default=Path("."))
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    if args.command == "setup":
        setup(args.workdir, args.seed)
        return 0
    if args.command == "observe":
        print(json.dumps(observe(args.workdir)))
        return 0
    if args.command == "truth":
        print(json.dumps(truth(args.seed), indent=2))
        return 0
    if args.action != "ASK":
        print(json.dumps({"status": "refused", "detail": f"unknown action {args.action}"}))
        return 0
    params = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(ask(args.workdir, args.seed, params if isinstance(params, dict) else {})))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
