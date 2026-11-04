"""Project and author data: one source for the report, the package and, later,
CITATION.cff and the Zenodo records.

Identifiers that do not exist yet stay None and are not shown anywhere: a
report must never link to a repository or a DOI that is not there.
"""
NAME = "tracetrim"
VERSION = "0.2.0"
DESCRIPTION = "Learn which fields of tool outputs an agent actually uses, from its traces, and trim the rest."

AUTHOR = "Alessio Pomaro"
AUTHOR_ROLE = "Independent Researcher"
ORCID = "0009-0008-6975-996X"
WEBSITE = "https://www.alessiopomaro.it/"

REPOSITORY = "https://github.com/alepom/tracetrim"
PAPER_DOI = None      # set when the Zenodo record is reserved
SOFTWARE_DOI = None
LICENSE = None        # set when the author confirms it


def as_dict() -> dict:
    return {k.lower(): v for k, v in globals().items() if k.isupper() and v is not None}
