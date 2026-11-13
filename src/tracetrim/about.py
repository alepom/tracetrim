"""Project and author data: one source for the report, the package and, later,
CITATION.cff and the Zenodo records.

Identifiers stay None until they exist, and None is never shown. The DOIs
below are published on Zenodo and resolve.
"""
NAME = "tracetrim"
VERSION = "1.0.0"
DESCRIPTION = "Learn which fields of tool outputs an agent visibly reuses, from its traces, and trim the rest."

AUTHOR = "Alessio Pomaro"
AUTHOR_ROLE = "Independent Researcher"
ORCID = "0009-0008-6975-996X"
WEBSITE = "https://www.alessiopomaro.it/"

REPOSITORY = "https://github.com/alepom/tracetrim"
PAPER_DOI = "10.5281/zenodo.24189959"            # version 2; version 1: 10.5281/zenodo.23953934
SOFTWARE_DOI = "10.5281/zenodo.24190053"         # 1.0.0; 0.3.4: 10.5281/zenodo.23954125
LICENSE = "MIT"


def as_dict() -> dict:
    return {k.lower(): v for k, v in globals().items() if k.isupper() and v is not None}
