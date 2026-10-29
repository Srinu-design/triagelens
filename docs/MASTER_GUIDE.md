# TriageLens — Master Guide

Oct 28, 2026 · @Srinu

## What it is

TriageLens is an intake triage service for bug reports, feature requests and client enquiries. For each new ticket it scrubs personal data, suggests a category, finds likely duplicates with hybrid search inside PostgreSQL, and acts: link the bug, add a vote to the feature, or answer the enquiry from its FAQ.

**30-second pitch (say it like this):**

> "Support teams drown in duplicates written in different words. Keyword search misses paraphrases, and vector search misses opaque error codes.
>
> TriageLens runs both in one PostgreSQL query — full-text and pgvector HNSW, fused with Reciprocal Rank Fusion — scoped to the right category. The duplicate decision uses calibrated similarity thresholds, so it auto-merges only when it's sure and sends the grey zone to a human. Emails, phone numbers, UPI IDs, Aadhaar and API keys are scrubbed before any text reaches an LLM."

**One-line header:** Hybrid-search intake triage with calibrated duplicate detection and PII scrubbing.

**What it is not:** a RAG chatbot. The LLM only suggests labels; search, decisions and actions are deterministic and testable.

## The problems it solves

Seven failure modes, each reproduced on the 30-ticket demo seed with the real embedding model and PostgreSQL 16.

| Problem | Why the naive approach fails | TriageLens's fix (measured) |
| --- | --- | --- |
| Paraphrased duplicates | "Mobile avatar upload screen hangs" shares no words with "Android image cropper freezes on profile photo" | Semantic branch ranks it #1 (cosine 0.663) |
| Opaque identifiers | Embeddings blur codes: the ticket with `E_0X7F3A` sits #16 of 30 semantically | Exact error-code match pins it #1 |
| Cross-category collisions | Unscoped, the dark-mode crash bug and the dark-mode feature request tie for #1 | Category-scoped search returns each one #1 in its own category |
| Lexical noise | OR-matching ranks tickets sharing one word ("screen", "upload") above the real match | Minimum-should-match: a ticket must match half the query terms (capped at 4) |
| Deciding "duplicate" | RRF always has a #1, and its score isn't a probability | Cosine thresholds: auto-merge ≥ 0.80, human review 0.70–0.80, new < 0.70 |
| ANN post-filtering | HNSW with a category filter returned 1 of 5 requested rows in our test | pgvector ≥ 0.8 iterative scans keep searching until 5 match |
| PII leakage | Raw ticket text would go to an external LLM | Ordered regex + checksum validators scrub before the LLM, the embedding, storage and logs |

**Say it precisely.** PostgreSQL's `ts_rank_cd` is not BM25 (it has no inverse document frequency). And no latency or memory number goes on the resume until the benchmark in this guide produces it.

## Architecture at a glance

&#91;embedded content: TriageLens pipeline · scrub, classify, embed, search, decide, act\]

Everything left of the decision is deterministic and testable; the LLM only suggests the category, which the user confirms. One SQL statement runs both retrievers and the fusion, so search is a single round trip.

## Design decisions you must defend

Thirteen decisions; the first five are where interviews go deep.

1. **One query, two retrievers.** A lexical CTE (GIN index on a generated `tsvector`: title and error code weighted A, description B) and a semantic CTE (HNSW on `halfvec(384)`, cosine) are fused with RRF in a `FULL OUTER JOIN`. One round trip, one transaction, no second database to keep in sync.
2. **OR plus minimum-should-match.** `websearch_to_tsquery` ANDs every word, so natural sentences match nothing; plain OR lets one shared word outrank the real match. TriageLens requires half the distinct query terms, capped at 4. On the paraphrase demo, OR-only pushed the true duplicate to #5; with the rule, it is #1. The term count runs only on rows the GIN index returns: written as one condition, it forced a scan of all 100,000 template tickets in a test (about 500–840 ms a query); with a prefilter, 26–166 ms and the same results.
3. **Exact identifiers are pinned, not ranked.** Error codes in the query (`WORD_WORD_…` pattern) are matched exactly against an indexed `error_code` column and pinned above fused results. An opaque code like `E_0X7F3A` is invisible to embeddings (rank 16 of 30).
4. **RRF (k = 60) ranks; it doesn't decide.** Fusing ranks avoids the scale mismatch between `ts_rank_cd` and cosine. But an RRF score has no absolute meaning, so it can't say "this is a duplicate".
5. **Calibrated thresholds decide.** Cosine to the best same-category candidate: ≥ 0.80 auto-merge, 0.70–0.80 human review, < 0.70 new; an exact error-code match lifts a candidate to at least review but never lowers the 0.80 bar for auto-merge. On the 31-ticket labeled set (in-sample): 21 of 23 duplicates auto-merged, 0 wrong merges, 4 sent to review (2 real, 2 lookalikes), 0 missed. The margin is thin: the closest lookalike scores 0.754, the weakest real duplicate 0.755.
6. **Category is part of the query.** The classifier suggests a category, the user confirms it, and search runs inside it. The best cross-category hit is shown as "related" but never acted on.
7. **Iterative scans for filtered ANN.** HNSW collects about `ef_search` candidates and filters afterwards, so a filter can starve the result. pgvector 0.8's `hnsw.iterative_scan = strict_order` keeps walking until enough rows match, in exact distance order. Per-category partial indexes are the alternative; the benchmark compares both.
8. **Honest about scale.** At 30 rows Postgres correctly picks a sequential scan (checked with `EXPLAIN`), and the UI shows the real plan. An "ANN lab" forces the index to demonstrate the filter trap; the benchmark measures HNSW at 100k rows.
9. **`halfvec` storage.** 2 bytes per dimension instead of 4. Only the vector part of each index entry halves — the graph's neighbour links don't — so quote the measured index size, not "50%".
10. **Local embeddings, no silent fallback.** fastembed with `BAAI/bge-small-en-v1.5` (384 dims, MIT license, about 65 MB) is baked into the image: no API key, no network at runtime. If the model can't load, `/readyz` fails instead of faking semantics.
11. **PII is scrubbed first.** Ordered patterns (secrets before phone numbers), Luhn for cards, Verhoeff for Aadhaar, PAN's holder-type letter, UPI handles. Only scrubbed text is stored, embedded, logged or sent to the LLM.
12. **The LLM is optional and boxed in.** Any OpenAI-compatible endpoint, JSON validated by Pydantic, one retry, deterministic fallback rules, a daily call budget. Ticket text is data, so a prompt injection can at worst produce a wrong label, which the user confirms anyway.
13. **Per-session sandbox.** Each visitor sees the 30 seed tickets plus only their own. Their votes and links count only for them, and everything they create expires after 24 hours.

## Numbers: measured vs still to measure

Seven numbers are already measured; seven more come from your benchmark run and the live host. Rule: a number goes on the resume only if `calibrate.py` or `bench/RESULTS.md` printed it.

**Already measured** (fastembed's bge-small, PostgreSQL 16, the seed and labeled set in the Build Spec):

| Metric | Result |
| --- | --- |
| Duplicate decision, 31-ticket labeled set (23 duplicates, 8 new), in-sample | Auto ≥ 0.80: 21 auto-merged, 0 wrong; 4 to review; 0 missed |
| Margin on that set | Closest lookalike 0.754, weakest real duplicate 0.755 |
| Top-1 candidate accuracy on the 23 duplicates | 23 / 23 |
| Code cases: 6 tickets with error codes (3 same bug, 3 different bug) | 3 auto-merged correctly, 3 to review, 0 wrong merges (the old code-plus-0.70 rule merged 2) |
| HNSW with a category filter, forced, `ef_search` 5, no iterative scan | 1 of 5 requested rows returned |
| Lexical branch, 100k template tickets, dev container | About 500–840 ms as one condition (sequential scan); 26–166 ms with the `hits` prefilter (GIN index), same results |
| Query embedding, warm, 2 threads, dev container | about 5.9 ms (re-measure on EC2) |

**To measure with the benchmark harness** (100k synthetic tickets, on your laptop, not the 1 GB server):

| Metric | Method | Resume placeholder |
| --- | --- | --- |
| Index size, `vector(384)` vs `halfvec(384)` | `pg_relation_size` of both HNSW indexes | \[X MB → Y MB, Z% smaller\] |
| Recall@10 | ANN top-10 vs exact top-10, 200 queries | \[0.9x\] |
| Vector search p50 / p95 | 200 warm queries, `ef_search` 40, DB time and embed time separately | \[a ms / b ms\] |
| Filtered search, 10%-selective category | rows returned with iterative scan off / strict / relaxed | \[counts\] |
| Partial per-category indexes vs iterative scan | recall@10 and p95 on the same queries | \[winner + numbers\] |
| Full hybrid query p50 / p95 | exact §7 SQL on the real schema, 200 warm queries, All and Enquiries; the plan must read `intake_tsv_gin` | \[a ms / b ms\] |
| Memory on the shared 1 GB host | `docker stats` at idle and under 3 × 10 previews, LLM off; swap | \[MB per service\] |

The labeled set is small and you wrote it, so say so: it validates the pipeline and sets starting thresholds; production thresholds need real tickets. Don't split it into calibration and test yet: about 10 held-out tickets can't measure a 0.001 margin. Grow it with tickets someone else writes first.

## Resume bullets

Bullets 1–3 match your resume and are usable once the build passes its tests; add bullet 4 only from `bench/RESULTS.md`.

**TriageLens — Hybrid-Search Ticket Triage** | Python, FastAPI, PostgreSQL + pgvector, LLM APIs

- Built a triage service that links duplicate tickets and answers repeat enquiries, fusing Postgres full-text and pgvector HNSW search in one SQL query via reciprocal-rank fusion.
- Calibrated duplicate thresholds on 31 tickets (in-sample): 21 of 23 auto-merged, 0 wrong merges.
- Scrubbed PII (UPI IDs, Aadhaar, PAN, cards, emails, phones, API keys) with Luhn and Verhoeff checks before any LLM call; LLM output is Pydantic-validated, with a rules fallback.
- After the benchmark: Benchmarked \[N\] synthetic tickets: `halfvec` HNSW index \[Z\]% smaller than float32 at recall@10 \[R\]; full hybrid search p95 \[L\] ms.

Internship tie-in for interviews: it extends the enquiry tracker you built at Envizon Designs and the LLM work from DataBeat — mention that in the story, not in the bullet.

**Never write:** "57% MAP" or "5 ms vs 250 ms" (unsourced), "BM25" (Postgres FTS isn't), "50% less RAM" (unmeasured), "<10 ms latency" without saying whether it includes the embedding step, or "0 wrong merges" without "in-sample".

## Live demo script (4 minutes)

Eight steps; every expected result below was reproduced on the seed.

1. **Open the site.** No login. Point at the three columns (Lexical · Semantic · Fused) and the telemetry row: DB ms, embed ms, the real query plan, model name, pgvector version.
2. **Preset "Opaque code: E\_0X7F3A".** Lexical pins BUG-109 #1 with an "exact code" badge; Semantic has it at #16; Fused puts it #1. Say: "Embeddings can't see opaque IDs. Exact matching can."
3. **Preset "Mobile avatar upload screen hangs".** Lexical is empty; Semantic and Fused rank BUG-102 #1 (cosine 0.66). Say: "Zero shared words. Minimum-should-match keeps one-word matches like 'screen' from outranking it."
4. **Preset "Android checkout dark mode".** On All, FEAT-201 and BUG-105 tie at the top. Switch to Bugs: BUG-105 #1. Features: FEAT-201 #1. Say: "A crash bug must never be merged into a feature request."
5. **Triage "Night mode for checkout on Android"** (category Feature). Decision: Duplicate of FEAT-201 (cosine 0.88) → click **Add vote**: 14 → 15.
6. **Triage "Dark mode for the web dashboard".** Decision: Review — closest is FEAT-201 at 0.75, but it's a different feature. Click **Not a duplicate** → a new feature is created. Say: "That grey zone is why there are two thresholds."
7. **Preset "PII + triage".** The diff card shows 8 redactions (email, phone, IP, UPI ID, PAN, card, two secrets). The Aadhaar-shaped order ref `2345 6789 0125` stays, because its Verhoeff checksum fails. Say: "Checksums keep random 12-digit numbers from being redacted."
8. **ANN lab.** Force the index, `ef_search` 5, filter Features, query "checkout crashes in dark mode". Iterative scan off → fewer than 5 rows (we saw 1); `strict_order` → 5. Say: "HNSW filters after collecting candidates; iterative scans keep walking until the filter is satisfied."

Close on the calibration card: "21 of 23 duplicates auto-merged on my 31-ticket set, zero wrong merges, the rest go to a human. It's in-sample, so the next step is more tickets."

## Interview Q&A

Twenty-one questions, most likely first. Keep answers short and back them with the demo.

**1. Why hybrid search instead of just embeddings?** They fail differently. Embeddings found the paraphrase with zero shared words, but ranked the ticket with an opaque code 16th of 30; exact lexical matching put it first.

**2. Why Reciprocal Rank Fusion?** It fuses ranks, not scores, so the unbounded `ts_rank_cd` and a bounded cosine never have to share a scale. Score = Σ 1 / (k + rank) over the lists an item appears in.

**3. Why k = 60?** It's the constant from Cormack, Clarke and Büttcher's 2009 RRF paper. A large k flattens the curve, so being near the top of both lists beats being #1 in just one.

**4. So why not decide duplicates with the RRF score?** RRF always has a #1, and its score has no absolute meaning. The decision uses cosine to the best same-category candidate with two calibrated thresholds, plus exact error-code matches.

**5. How did you choose 0.80 and 0.70?** On a labeled set: 23 duplicate tickets and 8 genuinely new ones. At 0.80, 21 duplicates auto-merge with zero wrong merges; 0.70–0.80 holds 2 real duplicates and 2 lookalikes, which go to a human. It's small, synthetic and in-sample, and the margin is thin (lookalike 0.754, weakest duplicate 0.755), so it sets starting values. `calibrate.py` re-runs on real data, and a held-out split waits until the set is big enough.

**6. Why not AND the query words, the default?** AND returns nothing for natural sentences. Plain OR lets one shared word win: on the paraphrase demo, OR-only pushed the true duplicate to #5. Requiring half the terms (max 4) fixed it.

**7. Why pin error codes instead of letting RRF rank them?** An exact identifier match is strong evidence of relevance, so it's pinned first instead of ranked. It isn't proof of the same bug, though: a shared code still needs cosine ≥ 0.80 to auto-merge. In the code-case test, the earlier rule (code plus 0.70) merged two different bugs that shared a generic code (413, 504). The code is matched with `=` against an indexed column, not `ILIKE`, where `_` and `%` act as wildcards.

**8. What is the HNSW post-filtering problem?** The index collects about `ef_search` nearest candidates and the `WHERE` clause filters them afterwards, so a selective filter can leave fewer rows than `LIMIT`. Forced HNSW with `ef_search` 5 returned 1 of 5 rows in our test.

**9. How do iterative scans fix it? strict or relaxed?** pgvector 0.8 keeps scanning the graph until enough rows pass the filter, up to `hnsw.max_scan_tuples` (20,000 by default). `strict_order` keeps exact distance order; `relaxed_order` can return slightly out-of-order results with better recall.

**10. Why not per-category partial indexes?** With 3 fixed categories they are a strong option: exact isolation, no extra traversal. The cost is one index per category plus one for "All". The benchmark compares both; pick by measured recall and latency.

**11. Your demo has 30 rows. Is HNSW even used?** No — at 30 rows Postgres correctly picks a sequential scan, and the UI shows that plan. The ANN lab forces the index to demonstrate behaviour, and the benchmark measures it at 100k rows.

**12. What does `halfvec` cost you?** Half-precision floats keep about 3 significant digits. Vector storage halves, but HNSW also stores neighbour links, so the index shrinks by less than half. Measure index size and recall@10 before claiming anything.

**13. HNSW or IVFFlat? What are m, ef\_construction and ef\_search?** HNSW needs no training step and has a better speed–recall trade-off. `m` = links per node (16), `ef_construction` = build-time candidate list (64), `ef_search` = query-time candidate list (40, the default).

**14. Why pgvector, not a vector database?** One store means one transaction for the ticket, its text index and its vector, with no dual-write sync. At this scale Postgres is enough; I'd revisit with benchmark evidence.

**15. Is Postgres full-text search BM25?** No. `ts_rank_cd` uses term frequency, proximity and weights, but no inverse document frequency. For true BM25 you'd add an extension or an external engine.

**16. Which embedding model, and why?** `BAAI/bge-small-en-v1.5` through fastembed: 384 dimensions, MIT license, about 65 MB, runs on CPU in a few milliseconds per query. Ticket-to-ticket matching is symmetric, so no query instruction prefix is used.

**17. Why regex for PII, not an NER model?** The targets are structured (emails, phones, IDs, keys), so ordered patterns plus checksums (Luhn, Verhoeff) are precise, fast and testable. Names and addresses would need NER; I'd say that's a known gap.

**18. Couldn't a ticket prompt-inject your LLM?** The LLM only returns a label inside a strict Pydantic schema, never runs tools, and the user confirms the category. The worst case is a wrong suggestion; bad output falls back to deterministic rules.

**19. What if the category is wrong?** Duplicate search runs in the confirmed category, and the best cross-category hit is shown as "related". So a misfiled bug is still visible next to its feature-request twin.

**20. How would it scale?** Move embedding to a worker queue, partition by tenant, add read replicas for search, and keep the same SQL. Re-run the benchmark at each step.

**21. Did you test the hybrid query at scale?** The keyword half first, and it caught a real problem. The first version wrote minimum-should-match as one condition, `(search_tsv @@ tsq AND matched >= need) OR error_code = ANY(codes)`, which forced a scan of every row: about 500–840 ms a query on 100,000 template tickets. A prefilter the GIN index can answer, with terms counted only on those rows, gave the same results in 26–166 ms. The benchmark now times the full query and records its plan.

## Know-your-code checklist

Tick each once you can explain it without notes.

- [ ] Read the hybrid SQL top to bottom: say what each CTE returns, why `lexical` ends with `ORDER BY … LIMIT 20`, and why `hits` filters before terms are counted.
- [ ] Explain how the OR query is built from `to_tsvector` lexemes, and compute "need" for a 5-term query (3).
- [ ] Compute one RRF score by hand, e.g. lexical rank 1 and semantic rank 2.
- [ ] Explain the decision function: thresholds, the review band, and why a matching error code goes to review instead of auto-merging.
- [ ] Run `calibrate.py` and read its output: the grid, the 0.754 / 0.755 margin and the code cases.
- [ ] Explain why `SET LOCAL` (not `SET`) is used for `hnsw.iterative_scan` and `hnsw.ef_search`.
- [ ] Run `EXPLAIN` on the semantic CTE at 30 rows and at 100k rows and explain the difference.
- [ ] Run `EXPLAIN ANALYZE` on the lexical branch at 100k rows, with the one-condition filter from Q21 and with `hits`, and explain why only one uses the GIN index.
- [ ] Walk the PII rule order and say why secrets run before phone numbers.
- [ ] Validate an Aadhaar-format number with Verhoeff and a card number with Luhn on paper.
- [ ] Explain where embeddings are computed, why in a thread pool, and why the model is baked into the image.
- [ ] Show what happens when the LLM returns invalid JSON (retry, then rules).
- [ ] Read `app.js` once: debounced search, the three columns, the triage card, the ANN lab.
- [ ] Explain why search waits 250 ms after typing and cancels the previous request: an old, slow response must not overwrite a newer one. The cancel only stops the browser waiting; the server may still run the query, one reason search is rate-limited.

## What changed from the old spec

Eighteen fixes; the first six were bugs the agent would have built as written, and the last four came from a review on 28 Oct.

| Old spec | Problem | Now |
| --- | --- | --- |
| Lexical CTE: `LIMIT 20` with no `ORDER BY` | Returns an arbitrary 20 rows, not the top 20 | `ORDER BY fts_rank LIMIT 20` |
| `:query ILIKE '%' \|\| error_code \|\| '%'` | `_` and `%` are wildcards; an empty or `%` code matched every query | Codes extracted from the query, exact `=` on an indexed column, `CHECK` forbids empty codes |
| `websearch_to_tsquery` on raw input | ANDs every word, and treats `-`, quotes and `or` in user text as syntax | OR of lexemes from `to_tsvector`, plus minimum-should-match |
| Top RRF hit = "duplicate" | Every search has a #1; RRF has no threshold | Calibrated cosine thresholds with a review band |
| "Semantic-cluster" fallback embeddings | Tests proved hand-made clusters, not the model; a silent fallback fakes the demo | Real model baked into the image; no silent fallback |
| Phone regex could run before secret detection | Could split `sk-live-998877665544` and mislabel it | Ordered rules, word boundaries, exact-output tests |
| "57% MAP", "5 ms → 250 ms", "50% less RAM", "<10 ms" | Unsourced or unmeasured | Measured table plus a benchmark harness |
| "BM25" | `ts_rank_cd` isn't BM25 | Called Postgres full-text search |
| A 30-row demo implying HNSW | The planner uses a sequential scan at 30 rows | Real plan shown, ANN lab, 100k-row benchmark |
| SAVEPOINT fallback for old pgvector | Hides a misconfiguration | Pin pgvector 0.8.7; refuse to start below 0.8 |
| PII: emails, phones, IPv4, `sk-`/Bearer only | Misses UPI IDs, Aadhaar, PAN, cards, AWS and GitHub keys | 14 ordered rules with Luhn and Verhoeff validators |
| Shared, writable demo dataset | One visitor's tickets and votes show up for the next interviewer | Per-session sandbox, 24 h expiry |
| Upvote = counter + 1 per click | A rage-click adds 10 votes | One vote per session (`UNIQUE`) plus `Idempotency-Key` |
| Public LLM endpoint on your key, no deployment plan | Unbounded cost; nowhere to run it | Rate limits, daily budget, rules fallback; same EC2 as SplitSync |
| A code match auto-merged at cosine ≥ 0.70 | Never checked against data; it merged 2 of the 3 different-bug code cases | A code match goes to review unless cosine ≥ 0.80; 6 code cases with expected verdicts |
| Lexical filter written as one condition | Couldn't use the GIN index: a full scan, about 500–840 ms a query at 100k rows | A hits prefilter, with terms counted only on its rows: GIN used, 26–166 ms, same results |
| Calibration numbers without caveats | In-sample, with a 0.001 margin between the closest lookalike and the weakest duplicate | Labeled in-sample with the margin; grow the set before any held-out split |
| Benchmark timed only vector search; no memory check | The query users run, and the 1 GB host shared with SplitSync, were never measured | Full hybrid query at 100k with its plan; a done-checklist box for docker stats under load, LLM off |

## Sources

- [pgvector iterative index scans (0.8.x docs)](https://docs.pgedge.com/pgvector/v0-8-5/iterative-index-scans/) — what iterative scans do and their defaults.
- [Permanent account number — Wikipedia](https://en.wikipedia.org/wiki/PAN_card) — PAN structure used by the scrubber.
- Every other number in this guide was measured by running the Build Spec's code on the demo seed, except the 100k lexical-branch timings, measured on template tickets in a 2-vCPU dev container.
