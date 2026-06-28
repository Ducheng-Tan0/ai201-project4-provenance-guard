# Provenance Guard — planning.md

## Table of Contents
1. [Architecture Narrative](#architecture-narrative)
2. [Architecture Diagram](#architecture-diagram)
3. [Detection Signals](#detection-signals)
4. [Uncertainty Representation & Confidence Scoring](#uncertainty-representation--confidence-scoring)
5. [Transparency Label Design](#transparency-label-design)
6. [Appeals Workflow](#appeals-workflow)
7. [Anticipated Edge Cases](#anticipated-edge-cases)
8. [API Surface](#api-surface)
9. [Data Storage Schema](#data-storage-schema)
10. [AI Tool Plan](#ai-tool-plan)

---

## Architecture Narrative

A piece of text enters the system through `POST /submit` along with a `creator_id`. The submission endpoint
assigns a unique `content_id` (UUID4), then passes the raw text through two independent detection signals
in sequence. Signal 1 — the LLM-based classifier which sends the text to Groq (llama-3.3-70b-versatile) with
a structured prompt and receives back a numeric probability that the content is AI-generated (for some 0 < p <1) .
Signal 2 — the stylometric heuristics engine — computes three statistical properties of the text in pure
Python (sentence-length variance, type-token ratio, and punctuation density) and combines them into a
single normalized score (0.0–1.0). A confidence scoring module takes both signal scores, applies a
weighted average, and produces a final `confidence` value. That value is then passed to the label
generator, which maps it to one of three plain-language transparency labels. The full result —
`content_id`, `attribution`, `confidence`, both individual signal scores, and the label text — is written
to the audit log and returned as the JSON response.

When a creator believes they have been misclassified, they call `POST /appeal` with their `content_id` and
a free-text `creator_reasoning`. The appeal endpoint looks up the original log entry, appends the appeal
reasoning, flips the entry's status from `classified` to `under_review`, writes a new audit log entry
capturing the change, and returns a confirmation. No automated re-classification occurs; a human reviewer
sees the updated status and the creator's reasoning when they query `GET /log`.

---

## Architecture Diagram

```
SUBMISSION FLOW
===============

  Client
    │
    │  POST /submit  { text, creator_id }
    ▼
┌─────────────────────────────────┐
│       /submit endpoint          │
│  - validate fields              │
│  - generate content_id (UUID4)  │
└────────────┬────────────────────┘
             │ raw text
             ▼
┌─────────────────────────────┐
│  Signal 1: LLM Classifier   │
│  (Groq llama-3.3-70b)       │
│  → llm_score  (0.0 – 1.0)   │
└────────────┬────────────────┘
             │ llm_score
             ▼
┌─────────────────────────────────┐
│  Signal 2: Stylometric Engine   │
│  - sentence length variance     │
│  - type-token ratio             │
│  - punctuation density          │
│  → stylo_score  (0.0 – 1.0)     │
└────────────┬────────────────────┘
             │ stylo_score
             ▼
┌─────────────────────────────────┐
│  Confidence Scoring Module      │
│  weighted avg (60% LLM,         │
│                40% stylometric) │
│  → confidence  (0.0 – 1.0)      │
└────────────┬────────────────────┘
             │ confidence
             ▼
┌────────────────────────────┐
│  Transparency Label        │
│  Generator                 │
│  → label_text  (string)    │
└────────────┬───────────────┘
             │ full result
             ▼
┌────────────────────────────┐
│  Audit Log (SQLite)        │
│  write structured entry    │
└────────────┬───────────────┘
             │ JSON response
             ▼
          Client
  { content_id, attribution,
    confidence, label_text,
    llm_score, stylo_score }


APPEAL FLOW
===========

  Client
    │
    │  POST /appeal  { content_id, creator_reasoning }
    ▼
┌───────────────────────────────────┐
│       /appeal endpoint            │
│  - look up content_id in log      │
│  - validate: entry exists and     │
│    status == "classified"         │
└────────────┬──────────────────────┘
             │ original entry + reasoning
             ▼
┌───────────────────────────────────┐
│  Status Update                    │
│  status: "classified"             │
│        → "under_review"           │
└────────────┬──────────────────────┘
             │ updated record
             ▼
┌───────────────────────────────────┐
│  Audit Log (SQLite)               │
│  append appeal_reasoning,         │
│  update status, log timestamp     │
└────────────┬──────────────────────┘
             │ confirmation JSON
             ▼
          Client
  { content_id, status: "under_review",
    message: "Appeal received" }
```

---

## Detection Signals

### Signal 1 — LLM-Based Classifier (Groq)

**What it measures:**
Holistic semantic and stylistic coherence. The LLM reads the text as a human reader would — attending to
vocabulary register, sentence rhythm, structural patterns, thematic consistency, and the kinds of
"tells" that experienced readers associate with generated text (e.g., overly balanced hedging, suspiciously
comprehensive lists, uniform clause length, generic transitions like "It is important to note that…").

**Why it differs between human and AI writing:**
AI language models are trained to produce high-probability, well-formed text. This produces characteristic
patterns: consistent formality even when informality would be natural, slightly-too-smooth transitions,
topic sentences that announce exactly what follows, and a tendency to cover all sides of an issue
symmetrically. Human writing is messier — it wanders, contradicts itself, has uneven energy, makes
unexpected associative leaps, and carries a recognizable individual voice. The LLM signal captures these
gestalt properties that are hard to quantify statistically.

**Output format:**
A float between 0.0 and 1.0.  
`0.0` = very likely human-written.  
`1.0` = very likely AI-generated.

The Groq prompt will ask the model to return *only* a JSON object: `{"ai_probability": <float>}`.
This structured output makes parsing deterministic and avoids freeform reasoning text bleeding into
the score.

**Blind spots:**
- Short texts (< ~80 words): too little signal for the LLM to identify meaningful patterns.  
- Highly polished human prose (published essays, formal academic writing): may resemble AI output
  in formality and structure, producing false positives.
- AI text that has been deliberately edited for naturalness ("humanized" outputs) can evade semantic
  detection.

---

### Signal 2 — Stylometric Heuristics Engine (Pure Python)

**What it measures:**
Three measurable statistical properties of the text's surface form, independent of meaning:

1. **Sentence-length variance** (`slv`): the standard deviation of word counts across all sentences,
   divided by mean sentence length (coefficient of variation). AI text tends to produce uniformly
   medium-length sentences; human writing has higher variance — some very short punchy sentences
   alongside longer sprawling ones.

2. **Type-token ratio** (`ttr`): the number of unique word types divided by total word tokens
   (after lowercasing, stripping punctuation). Higher values indicate more diverse vocabulary.
   AI models frequently repeat the same mid-frequency words ("important," "ensure," "various,"
   "stakeholders") within a passage, driving TTR down relative to informal or expressive human writing.

3. **Punctuation density** (`pd`): count of non-period punctuation marks (commas, semicolons,
   em-dashes, parentheses, exclamation/question marks) divided by total character count. Human writing
   — especially informal writing — uses a wider variety and higher density of non-period punctuation.
   AI text is often more conservative and period-heavy.

**Combining the three metrics into `stylo_score`:**

Each raw metric is normalized to [0, 1] against empirically set min/max bounds, then the three
normalized values are combined into a single `stylo_score` using this logic:

```
# Higher variance → more human → lower AI score
slv_norm  = clamp((slv_max - slv) / (slv_max - slv_min), 0, 1)

# Higher TTR → more human → lower AI score
ttr_norm  = clamp((ttr_max - ttr) / (ttr_max - ttr_min), 0, 1)

# Higher punct density → more human → lower AI score
pd_norm   = clamp((pd_max - pd) / (pd_max - pd_min), 0, 1)

# Weighted average — sentence variance is most diagnostic
stylo_score = 0.5 * slv_norm + 0.3 * ttr_norm + 0.2 * pd_norm
```

The default normalization bounds (tunable constants at the top of the module):

| Metric | `_min` | `_max` | Notes |
|--------|--------|--------|-------|
| `slv`  | 0.10   | 0.80   | CV of word-count per sentence |
| `ttr`  | 0.40   | 0.85   | unique/total word ratio |
| `pd`   | 0.01   | 0.08   | non-period punct / total chars |

**Output format:**
A float between 0.0 and 1.0.  
`0.0` = stylometric profile consistent with human writing.  
`1.0` = stylometric profile consistent with AI-generated text.

**Blind spots:**
- Non-native English speakers often write in more uniform, formal registers, producing higher
  `stylo_score` values even for genuine human work (a critical false-positive risk).
- Poetry and flash fiction: sentence segmentation fails on linebreaks, and intentionally constrained
  vocabulary (e.g., a villanelle) reduces TTR artificially.
- Very short texts (< 3 sentences): sentence-length variance is statistically meaningless.

---

### Why These Two Signals Together

LLM scoring is semantic and holistic; stylometric scoring is structural and statistical. They are
genuinely independent — the LLM signal does not use any of the three metrics, and the stylometric
engine has no access to the LLM's reasoning. When both signals agree (both high or both low), confidence
is high. When they disagree, the system should express genuine uncertainty rather than forcing a verdict.
This disagreement is a feature, not a bug.

---

## Uncertainty Representation & Confidence Scoring

### Formula

```
confidence = 0.60 * llm_score + 0.40 * stylo_score
```

The 60/40 weighting reflects that the LLM signal is more semantically rich and typically more
accurate on longer, well-formed text — but stylometric features provide a meaningful independent
check, especially when the LLM is uncertain or the text is short.

### What a Confidence Score Means

| Score range | Interpretation | Attribution label |
|-------------|----------------|-------------------|
| 0.00 – 0.35 | System is reasonably confident this is human-written | `likely_human` |
| 0.36 – 0.64 | System cannot make a confident determination | `uncertain` |
| 0.65 – 1.00 | System is reasonably confident this is AI-generated | `likely_ai` |

The "uncertain" band is intentionally wide (29 percentage points) because the cost of a false positive
— labeling a human creator's genuine work as AI-generated — is significantly worse than a false
negative on this platform. Uncertainty should be surfaced honestly rather than rounded to a verdict.

A score of 0.51 is squarely in the uncertain band and will produce an "uncertain" label. A score of
0.95 produces a "likely AI" label. These are meaningfully different outputs, not the same label with
different numbers attached.

### Calibration Approach

After initial implementation, test the scoring function against the four canonical inputs provided in
Milestone 4. Expected outcomes:

| Input type | Expected `confidence` range |
|------------|-----------------------------|
| Clearly AI-generated (formal/corporate) | 0.70 – 0.95 |
| Clearly human (informal, messy, personal) | 0.05 – 0.30 |
| Borderline: formal human prose | 0.40 – 0.65 |
| Borderline: lightly edited AI output | 0.50 – 0.75 |

If any score falls outside its expected range, inspect both signal scores separately before adjusting
weights. The most likely culprit is the stylometric normalization bounds, which may need to be
recalibrated against more sample text.

---

## Transparency Label Design

These are the three label variants, written as the exact text a user would see on the platform.
All three variants include: the verdict, a plain-language confidence statement, and a note about
the appeals process.

---

### ⚠️ Variant A — High-Confidence AI (confidence ≥ 0.65)

```
AI-Generated Content Detected

Our analysis indicates this content was likely created with AI assistance
(confidence: {confidence_pct}%). This label is based on two independent
signals: a language model assessment and a statistical writing-style analysis.

If you are the creator and believe this is incorrect, you can submit an
appeal below. Appeals are reviewed by our moderation team.
```

*(`{confidence_pct}` is replaced with `round(confidence * 100)`, e.g., "82%")*

---

### ✅ Variant B — High-Confidence Human (confidence ≤ 0.35)

```
Human-Written Content

Our analysis suggests this content was written by a person, not generated
by AI (confidence: {confidence_pct}%). This label reflects automated
analysis and is not a guarantee.

If you believe this assessment is wrong, you can submit an appeal below.
```

---

### 🔍 Variant C — Uncertain (0.36 ≤ confidence ≤ 0.64)

```
Attribution Unclear

Our automated analysis could not confidently determine whether this content
was written by a person or generated by AI. This does not imply wrongdoing —
some writing styles are difficult to assess automatically.

If you are the creator, you may submit an appeal to add context that can
help our moderation team make a more informed review.
```

---

### Design Notes

- Variant A uses a warning icon but avoids accusatory language ("likely created with AI assistance"
  rather than "this is AI-generated plagiarism").
- Variant C explicitly says "This does not imply wrongdoing" to reduce harm for creators whose genuine
  work falls in the uncertain band.
- All three variants mention the appeals path — this is intentional. Every creator should know they
  can contest the label, regardless of the verdict.
- The `{confidence_pct}` interpolation is only shown in Variants A and B (where the score is
  directional); Variant C omits it to avoid giving a borderline number that misleads users into
  thinking "48% AI" is a precise finding.

---

## Appeals Workflow

### Who Can Submit an Appeal?

Any submitter who provides a valid `content_id` (returned by `POST /submit`). The system does not
currently implement authentication beyond `creator_id` matching — appeals are open to the original
submitter. A production deployment would add auth here.

### What Information Must an Appeal Include?

Required fields in the request body:
- `content_id` (string, UUID4): the ID from the original submission.
- `creator_reasoning` (string, 10–2000 chars): the creator's explanation. This is free text; the
  prompt shown in the UI should encourage creators to explain *why* they wrote this themselves,
  not just assert it.

### What Does the System Do?

1. Look up the `content_id` in the audit log. Return 404 if not found.
2. Validate that `status == "classified"`. Return 409 if the content is already `under_review`
   or `reviewed` (to prevent duplicate appeals).
3. Update the record: set `status = "under_review"`, append `appeal_reasoning` and
   `appeal_timestamp` fields.
4. Write a new audit log entry of type `appeal_filed` capturing the change.
5. Return HTTP 200 with a confirmation object.

### What Does a Human Reviewer See?

When a moderator queries `GET /log?status=under_review`, they see all entries with:
- The original `attribution` and `confidence` score.
- Both individual signal scores (`llm_score`, `stylo_score`).
- The full `appeal_reasoning` text.
- The `appeal_timestamp`.

This gives them the full picture without having to cross-reference anything.

### Appeal Status Lifecycle

```
classified  →  under_review  →  reviewed
(on submit)    (on appeal)       (manual moderator action — out of scope for this project)
```

---

## Anticipated Edge Cases

### Edge Case 1: Non-native English Speaker Writing Formally

A creator who is a non-native English speaker may write in a careful, formal register — avoiding
colloquialisms, using complete sentences, keeping consistent grammar. This writing style closely
resembles the surface statistics of AI output: low sentence-length variance, conservative punctuation,
moderate-to-low type-token ratio. The stylometric signal will likely score this as AI-leaning even
though it is genuinely human. The LLM signal may partially compensate if the prose carries
idiosyncratic phrasing or personal content, but there is no guarantee.

**Mitigation in label design:** Variant C ("Attribution Unclear") explicitly says "this does not
imply wrongdoing" and offers the appeals path. Variant A encourages appeal. The goal is to catch
this case in the uncertain band rather than a false-positive verdict.

### Edge Case 2: Short Poems with Intentionally Simple Vocabulary

A short poem using simple, repeated words (e.g., a haiku, a villanelle, or a confessional lyric
poem) will have extremely low type-token ratio, very short or zero sentence-length variance
(poems don't use conventional sentences), and unusual punctuation patterns. The stylometric engine
will likely score this as AI-generated regardless of its true origin, because the normalization
bounds were calibrated on prose.

**Mitigation:** The system could detect line-break-heavy text and flag it as a potential poetry
submission, either skipping the stylometric signal or suppressing its weight. This is not
implemented in v1 — it is listed as a known limitation in the README.

### Edge Case 3: Very Short Submissions (< 50 words)

The LLM signal has too little content to assess meaningfully, and the stylometric metrics have
no statistical validity with fewer than 3 sentences. Both signals will produce near-0.5 scores
by default, collapsing everything into "Uncertain" — not because the system is genuinely unsure,
but because there is insufficient data.

**Mitigation:** Validate `len(text.split()) >= 50` at the submission endpoint and return a 400
error with a message like: "Submission too short for reliable analysis. Please submit at least
50 words."

### Edge Case 4: Mixed-Origin Content (Human Draft + AI Polish)

A creator writes a rough draft, then uses AI to polish or expand it. The final text may be
genuinely collaborative — neither fully human nor fully AI. Both signals will likely produce
mid-range scores, landing in "Uncertain." This is actually the *correct* behavior for the
intended design; the problem is that the transparency label doesn't communicate "this might be
collaborative" — it says "we couldn't determine." A future label variant for collaborative
content would be more honest, but is out of scope for this project.

---

## API Surface

| Method | Endpoint | Accepts | Returns |
|--------|----------|---------|---------|
| POST | `/submit` | `{ text, creator_id }` | `{ content_id, attribution, confidence, llm_score, stylo_score, label_text, status }` |
| POST | `/appeal` | `{ content_id, creator_reasoning }` | `{ content_id, status, message }` |
| GET | `/log` | optional query param: `?status=under_review` | `{ entries: [...] }` |

### `/submit` request/response contract

Request:
```json
{
  "text": "string, required, min 50 words",
  "creator_id": "string, required"
}
```

Response (200 OK):
```json
{
  "content_id": "uuid4-string",
  "creator_id": "string",
  "attribution": "likely_ai | uncertain | likely_human",
  "confidence": 0.82,
  "llm_score": 0.85,
  "stylo_score": 0.77,
  "label_text": "string — full label copy shown to user",
  "status": "classified",
  "timestamp": "ISO-8601"
}
```

Error responses:
- `400` — missing fields or text too short.
- `429` — rate limit exceeded.
- `500` — upstream API failure (Groq unavailable).

### `/appeal` request/response contract

Request:
```json
{
  "content_id": "uuid4-string",
  "creator_reasoning": "string, 10–2000 chars"
}
```

Response (200 OK):
```json
{
  "content_id": "uuid4-string",
  "status": "under_review",
  "message": "Your appeal has been received and will be reviewed by our moderation team."
}
```

Error responses:
- `400` — missing fields or `creator_reasoning` too short/long.
- `404` — `content_id` not found.
- `409` — content is already under review.

### `/log` request/response contract

Response (200 OK):
```json
{
  "entries": [
    {
      "content_id": "uuid4-string",
      "creator_id": "string",
      "timestamp": "ISO-8601",
      "attribution": "likely_ai",
      "confidence": 0.82,
      "llm_score": 0.85,
      "stylo_score": 0.77,
      "status": "classified",
      "appeal_reasoning": null,
      "appeal_timestamp": null
    }
  ]
}
```

---

## Data Storage Schema

Using SQLite (`provenance.db`) with a single `submissions` table.

```sql
CREATE TABLE submissions (
  content_id       TEXT PRIMARY KEY,
  creator_id       TEXT NOT NULL,
  text_snippet     TEXT NOT NULL,          -- first 200 chars, for reviewer context
  attribution      TEXT NOT NULL,          -- "likely_ai" | "uncertain" | "likely_human"
  confidence       REAL NOT NULL,
  llm_score        REAL NOT NULL,
  stylo_score      REAL NOT NULL,
  label_text       TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'classified',
  appeal_reasoning TEXT,
  appeal_timestamp TEXT,
  created_at       TEXT NOT NULL           -- ISO-8601
);
```

The `text_snippet` field stores only the first 200 characters. Full text is not stored — to avoid
the system becoming a content archive and to keep the database size manageable.

---

## AI Tool Plan

### Milestone 3 — Submission Endpoint + Signal 1

**Spec sections to provide:**
- This entire `planning.md` as context, with attention called to: the Detection Signals section
  (Signal 1 only), the API Surface section (`/submit` contract), the Architecture Diagram
  (submission flow), and the Data Storage Schema.

**Prompt / what to ask for:**
> "Using the planning document and architecture diagram below, generate: (1) a Flask app skeleton
> with a POST /submit route stub that validates the required fields, assigns a UUID4 content_id,
> and returns a hardcoded placeholder response; (2) a standalone `classify_with_llm(text)` function
> that calls the Groq API using llama-3.3-70b-versatile and returns a float between 0.0 and 1.0
> representing the AI probability. The function should send the text with a system prompt that
> instructs the model to return only `{"ai_probability": <float>}` and nothing else."

**Verification checklist:**
- [ ] Flask app runs with `python app.py` without errors.
- [ ] `POST /submit` with valid JSON returns 200 with `content_id`, `attribution`, `confidence`,
      `label_text`.
- [ ] `POST /submit` with missing `text` or `creator_id` returns 400.
- [ ] `classify_with_llm()` called independently on the 4 canonical test inputs produces scores
      that feel directionally correct before wiring into the endpoint.
- [ ] GROQ_API_KEY is loaded from `.env`, not hardcoded.

---

### Milestone 4 — Signal 2 + Confidence Scoring

**Spec sections to provide:**
- Detection Signals section (Signal 2 detail, including the formula and normalization table).
- Uncertainty Representation & Confidence Scoring section (the formula, the thresholds table,
  and the calibration expected-output table).
- Architecture Diagram (submission flow — specifically the signal 2 and confidence scoring boxes).

**Prompt / what to ask for:**
> "Using the spec sections provided, generate: (1) a standalone `compute_stylometric_score(text)`
> function in pure Python (no external libraries) that computes sentence-length variance,
> type-token ratio, and punctuation density, normalizes each using the bounds in the spec, and
> combines them into a single `stylo_score` float using the specified weights; (2) a
> `compute_confidence(llm_score, stylo_score)` function that applies the 60/40 weighted average
> and returns the combined float."

**Verification checklist:**
- [ ] `compute_stylometric_score()` called independently on the 4 canonical test inputs produces
      scores that feel directionally correct (AI-text → higher score, informal human → lower score).
- [ ] `compute_confidence()` with `(0.0, 0.0)` returns 0.0; with `(1.0, 1.0)` returns 1.0.
- [ ] End-to-end: `POST /submit` with the clearly-AI canonical input produces `confidence > 0.65`
      and `attribution == "likely_ai"`.
- [ ] End-to-end: `POST /submit` with the clearly-human canonical input produces
      `confidence < 0.35` and `attribution == "likely_human"`.
- [ ] Audit log entries now include `llm_score` and `stylo_score` as separate fields.

---

### Milestone 5 — Production Layer (Labels, Appeals, Rate Limiting, Full Audit Log)

**Spec sections to provide:**
- Transparency Label Design section (all three variant texts verbatim, the confidence thresholds,
  and the design notes).
- Appeals Workflow section (status lifecycle, required fields, what system does).
- API Surface section (`/appeal` and `/log` contracts).
- Architecture Diagram (appeal flow).

**Prompt / what to ask for:**
> "Using the spec sections provided, generate: (1) a `generate_label(attribution, confidence)`
> function that maps the attribution/confidence values to the correct label text as written in
> the spec, interpolating `{confidence_pct}` in Variants A and B; (2) a POST /appeal Flask
> endpoint that validates input, looks up the content_id in SQLite, returns 404 if not found
> and 409 if status != 'classified', updates the record to 'under_review', appends appeal_reasoning
> and appeal_timestamp, writes a log entry, and returns the confirmation JSON."

**Verification checklist:**
- [ ] Submitting the clearly-AI canonical input produces a response where `label_text` contains
      the exact Variant A copy (with `⚠️`).
- [ ] Submitting the clearly-human canonical input produces Variant B (with `✅`).
- [ ] A submission calibrated to land in the uncertain band produces Variant C (with `🔍`).
- [ ] `POST /appeal` with a valid `content_id` returns 200 and `status: "under_review"`.
- [ ] `POST /appeal` with the same `content_id` a second time returns 409.
- [ ] `POST /appeal` with an unknown `content_id` returns 404.
- [ ] `GET /log` returns at least 3 structured entries including at least one with
      `status: "under_review"` and a populated `appeal_reasoning` field.
- [ ] Running 12 rapid `POST /submit` requests produces 200 for the first 10 and 429 for the
      remaining 2.
- [ ] Rate limit values and reasoning are documented in README.