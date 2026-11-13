# GitHub fixtures

Synthetic JSON shaped like the official GitHub REST API (`/search/repositories`,
`/repos/{owner}/{repo}/releases`). Repository names, descriptions and releases are fictional.
Covers: a normal repo, a fork, an archived repo, HTML in a description, no licence, an invalid
name, a missing created_at, a foreign html_url (ignored: URLs are rebuilt), a release, a pre-release,
a release without a name or body, and an invalid tag.
