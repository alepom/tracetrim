"""The stricter billing audit (experiments/c4_audit.py): the false positives an
external review found must stay rejected, the real answers' formats accepted."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from experiments.c4_audit import FORBID, RULES, passes  # noqa: E402


def ok(ticket, text):
    return passes(text, RULES[ticket], FORBID.get(ticket, ()))


def test_the_reviewers_false_positives_are_rejected():
    assert not ok("T1", "Due addebiti da 129 euro, entrambi falliti")
    assert not ok("T1", "Due addebiti da 29,00 €, entrambi falliti")
    assert not ok("T3", "Il periodo termina il 20 novembre 2027")
    assert not ok("T4", "Un rimborso di 149 euro è in corso")
    assert not ok("T5", "Fattura di €38,50 con €9,00 di storage")


def test_the_formats_agents_write_are_accepted():
    assert ok("T1", "Risultano due addebiti riusciti di 29,00 € il 21 ottobre 2026, a 47 secondi")
    assert ok("T3", "Il periodo corrente termina il 20 novembre 2026.")
    assert ok("T4", "Il rimborso di 49,00 € è ancora in elaborazione.")
    assert ok("T5", "L'ultima fattura è di €38,00, contro €29,00: l'aumento di €9,00 è per Extra storage 50 GB.")


def test_negated_statements_are_rejected():
    assert not ok("T3", "Non risulta che il periodo termini il 20 novembre 2026")
