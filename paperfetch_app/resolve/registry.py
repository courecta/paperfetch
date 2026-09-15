from __future__ import annotations

from ..fetch.http import HttpClient
from ..models import PaperIdentity
from .base import Resolution


def resolve_extended(identity: PaperIdentity, client: HttpClient) -> Resolution | None:
    """Resolver registry for DOI, PMC, OpenReview, bioRxiv, and proceedings.

    Implemented in Phase 2. Returns ``None`` when no specialized resolver
    handles the identity so the generic resolver can take over.
    """
    from . import doi as doi_resolver
    from . import openreview as openreview_resolver
    from . import pmc as pmc_resolver
    from . import proceedings as proceedings_resolver

    if identity.kind in {"doi", "biorxiv", "medrxiv"}:
        return doi_resolver.resolve_doi_like(identity, client)
    if identity.kind == "pmcid":
        return pmc_resolver.resolve_pmc(identity, client)
    if identity.kind == "openreview":
        return openreview_resolver.resolve_openreview(identity, client)
    if identity.kind in {"acl", "pmlr", "cvf", "jmlr", "neurips"}:
        return proceedings_resolver.resolve_proceedings(identity, client)
    return None
