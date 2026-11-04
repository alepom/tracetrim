"""Project and author data: one source for the report, the package and, later,
CITATION.cff and the Zenodo records.

Identifiers that do not exist yet stay None and are not shown anywhere: a
report must never link to a repository or a DOI that is not there.
"""
NAME = "tracetrim"
VERSION = "0.3.3"
DESCRIPTION = "Learn which fields of tool outputs an agent actually uses, from its traces, and trim the rest."

AUTHOR = "Alessio Pomaro"
AUTHOR_ROLE = "Independent Researcher"
ORCID = "0009-0008-6975-996X"
WEBSITE = "https://www.alessiopomaro.it/"

REPOSITORY = "https://github.com/alepom/tracetrim"
PAPER_DOI = "10.5281/zenodo.23953934"
SOFTWARE_DOI = "10.5281/zenodo.23954125"
LICENSE = "MIT"


def as_dict() -> dict:
    return {k.lower(): v for k, v in globals().items() if k.isupper() and v is not None}
