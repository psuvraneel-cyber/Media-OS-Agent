# arXiv fixtures

Synthetic Atom documents shaped like the official arXiv API responses
(<https://info.arxiv.org/help/api/user-manual.html>). Paper ids, titles, abstracts and author
names are fictional. Includes: a sample page (multi-line title, entity-encoded markup in the
abstract, PDF links that must never be followed, a cross-listed paper), malformed entries, the
arXiv API error feed shape, an empty result, and XML attack payloads (billion laughs, external
entity / XXE, external DTD) that `defusedxml` must reject.
