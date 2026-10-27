---
status: proposed
---

# Use one shared GitHub App for agent activity

Agents currently act with the human's GitHub credentials, making GitHub's actor records unable to distinguish human and automated activity. Route agent GitHub writes through one shared GitHub App, with harness, exact model ID, and run ID recorded separately. This creates a consistent human/agent identity boundary without maintaining an App per harness.

## Considered options

- **Human credentials plus trailers or footers:** cheap, but text metadata cannot change GitHub's attributed actor.
- **One App per harness:** separates harnesses in actor records, but increases installation and credential-management overhead and fragments identity when tools change.
- **Dedicated machine-user account:** provides a separate actor but does not offer the App installation permission model and short-lived installation tokens.

## Consequences

The shared actor cannot identify a particular harness or model; run-linked metadata supplies that detail and is not proof of origin by itself. App authentication does not set Git commit authorship or provide browser login. Human approval and human-performed merge remain distinct: approval does not turn a subsequent App merge into a human action.

Installing this App across repositories creates an identity dependency that becomes costly to replace once policies and reporting rely on it. Begin with a test repository. This proposal does not authorize installation, change commit attribution rules, or decide which repositories allow agent merges.
