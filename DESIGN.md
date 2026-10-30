# Design decisions and failure modes

1. **Write provenance is distinct from read provenance.** Imported/tool text must not automatically become remembered user preferences. This lab only accepts a caller-provided `source='user'`, which must be enforced by authenticated application wiring outside this demo.
2. **Durability requires consent.** Session-scoped notes are backed by SQLite for repeatability but are restricted to an individual session; durable notes can be shared across sessions only for the same tenant/user.
3. **Retrieval is least-context.** Lexical scoring retrieves matching notes, constrained by a hard word-count budget; that is not an LLM token count, so production must use the target model's tokenizer and measure prompt length.
4. **Supersession isn't erasure.** Superseded facts are excluded from recall but retained in owner export until TTL or explicit forgetting.
5. **Deletion has limitations.** SQLite database pages, WAL copies, filesystem snapshots and backups can retain historic bytes; secure deletion requires deployment-level design and retention policy.
6. **No memory is a privileged instruction.** `trust='untrusted_context'` is returned with each item. Applications must keep it as data, not merge it into trusted system/developer policy.
7. **No blanket secrecy claims.** Basic credential pattern rejection is demonstrative; names, account identifiers and other sensitive data are not comprehensively detected or redacted.

Production extensions: authenticated identity/consent, encryption and secret management, backup retention, row-level policies, GDPR/CCPA request workflow, encrypted indexes, supervised memory extraction, model-aware budgets, and concurrency tests.
