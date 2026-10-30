# Agent Memory & Context Governance Lab

A **local, deterministic Python/SQLite engineering lab** exploring how a tool-using assistant might *govern* remembered context. This is not an LLM memory service, semantic embedding database, production identity provider, or Google Cloud integration.

## Why this project?

Long-running agents need more than a vector store: trusted write boundaries, tenant/user isolation, consent for durable records, expiry, conflict handling, transparent provenance, deletion, and strict context budgets. This lab makes those choices executable and testable.

## Capabilities

- **Trusted-write gate:** only a caller-labeled explicit `source="user"` is eligible for memory. Retrieved documents and tool outputs are denied; the demo does **not authenticate** that label.
- **Consent & scope:** session-bound notes expire by default after 1 hour; durable notes require explicit consent and expire after 90 days by default. Both have retention ceilings.
- **Isolation:** tenant + user scoping; session notes remain restricted to one session, while durable notes can be recalled in another session for the same owner.
- **Conflict supersession:** newer facts in the same slot supersede older ones, while preserving the older record for owner export until forget/expiration.
- **Context governance:** lexical retrieval with an explicit **word-based approximation** of a context budget (not model tokenization); results are labeled `untrusted_context` even when sourced from a user.
- **Deletion:** `forget` physically removes the selected owner's memory content, including superseded versions for a slot. Non-content audit metadata remains, and SQLite/WAL file remnants are not securely erased.
- **Fail-closed examples:** known credential shapes are rejected (not a comprehensive DLP filter), out-of-range TTLs fail, and all queries are parameterized.

## Quickstart (Python 3.10+, standard library)

```sh
python -m memory_lab demo
python -m memory_lab evaluate
python -m unittest discover -s tests -v
```

Expected evaluation: 8/8 deterministic policy scenarios; tests exercise isolation, consent, injection provenance, conflict, expiry, deletion, persistence, and budgets.

## API sketch

```python
from memory_lab import Actor, MemoryStore

actor = Actor("tenant-a", "alice", "session-01")
with MemoryStore("demo.db") as memory:
    memory.remember(actor, "reply_style", "Use concise technical explanations.",
                    scope="durable", consent=True)
    retrieved = memory.recall(actor, "technical", word_budget=30)
    memory.forget(actor, slot="reply_style")
```

## Threat boundaries / limitations

This SDK is a **policy simulator**. `Actor`, `source`, and `consent` are supplied by the caller and must be derived from authenticated sessions and an explicit consent UI in a real deployment. There is no retrieval-augmented generation, embedding similarity, encrypted storage, full PII detection, authorization service, or production-grade erasure. Content may be malicious even if originally said by a user; it must never be injected as higher-priority instructions. SQLite audit metadata survives forget operations. No real customer records or credentials are included.

## Architecture

`Actor` -> `remember(policy & consent)` -> `SQLite memories` -> `lexical recall + budget` -> `untrusted_context`. Separate `export`, `forget`, `prune_expired`, `audit` paths.

See [DESIGN.md](DESIGN.md) for design choices and known gaps. All fixtures are synthetic. MIT licensed.
