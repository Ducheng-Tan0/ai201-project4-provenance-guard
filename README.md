# ai201-project4-provenance-guard
A backend system that any creative sharing platform could plug into to classify submitted content, score confidence in that classification, surface a transparency label to users, and handle appeals from creators who believe they've been misclassified.



# Provenance Guard

A backend attribution system that classifies submitted text as human-written or AI-generated, scores confidence in that classification, surfaces a transparency label to users, and handles creator appeals. Built as a pluggable API that any creative sharing platform could integrate.

---

## Table of Contents

1. [Setup & Installation](#setup--installation)
2. [Running the Server](#running-the-server)
3. [API Endpoints](#api-endpoints)
4. [Architecture](#architecture)
5. [Detection Signals](#detection-signals)
6. [Confidence Scoring](#confidence-scoring)
7. [Transparency Labels](#transparency-labels)
8. [Appeals Workflow](#appeals-workflow)
9. [Rate Limiting](#rate-limiting)
10. [Audit Log](#audit-log)
11. [Known Limitations](#known-limitations)
12. [Spec Reflection](#spec-reflection)
13. [AI Usage](#ai-usage)

---

## Setup & Installation

**Prerequisites:** Python 3.10+, a Groq API key (free at https://console.groq.com)

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/ai201-project4-provenance-guard.git
cd ai201-project4-provenance-guard

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # Mac/Linux
source .venv/Scripts/activate      # Windows Git Bash

# Install dependencies
pip install -r requirements.txt
```

Create a `.env` file in the project root — **never commit this file**:

```
GROQ_API_KEY=your_key_here
```

It is already listed in `.gitignore`.

---

## Running the Server

```bash
python app.py
```

The server starts on `http://127.0.0.1:5000` by default. If port 5000 is taken, Flask will use 5001 — check the terminal output for the actual port.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/submit` | Submit text for attribution analysis |
| `POST` | `/appeal` | Contest a classification |
| `GET`  | `/log`    | Retrieve audit log entries |

### POST `/submit`

Accepts a piece of text and returns an attribution result, confidence score, and transparency label.

**Request:**
```json
{
  "text": "string — minimum 30 words",
  "creator_id": "string"
}
```

**Response (200):**
```json
{
  "content_id": "3f7a2b1e-9c4d-4a1b-8e2f-1a2b3c4d5e6f",
  "creator_id": "test-user-1",
  "attribution": "likely_ai",
  "confidence": 0.82,
  "llm_score": 0.85,
  "stylo_score": 0.77,
  "label_text": "⚠️ AI-Generated Content Detected\n\nOur analysis indicates...",
  "status": "classified",
  "timestamp": "2025-04-01T14:32:10.123Z"
}
```

**Error responses:** `400` missing/short fields · `429` rate limit exceeded · `500` Groq unavailable

---

### POST `/appeal`

Allows a creator to contest a classification by providing their reasoning.

**Request:**
```json
{
  "content_id": "uuid from /submit response",
  "creator_reasoning": "string — 10 to 2000 characters"
}
```

**Response (200):**
```json
{
  "content_id": "3f7a2b1e-...",
  "status": "under_review",
  "appeal_timestamp": "2025-04-01T14:45:00.000Z",
  "message": "Your appeal has been received and will be reviewed by our moderation team."
}
```

**Error responses:** `400` missing/invalid fields · `404` content_id not found · `409` already under review

---

### GET `/log`

Returns recent audit log entries. Accepts an optional `?status=` query parameter to filter.

```bash
# All entries
curl http://localhost:5000/log

# Only entries under review
curl "http://localhost:5000/log?status=under_review"
```

**Response (200):**
```json
{
  "count": 3,
  "entries": [ { ...entry fields... } ]
}
```

---

## Architecture

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
│  → llm_score  (0.0 – 1.0)  │
└────────────┬────────────────┘
             │ llm_score
             ▼
┌─────────────────────────────────┐
│  Signal 2: Stylometric Engine   │
│  - sentence length variance     │
│  - type-token ratio             │
│  - punctuation density          │
│  → stylo_score  (0.0 – 1.0)   │
└────────────┬────────────────────┘
             │ stylo_score
             ▼
┌─────────────────────────────────┐
│  Confidence Scoring Module      │
│  weighted avg (60% LLM,         │
│                40% stylometric) │
│  → confidence  (0.0 – 1.0)    │
└────────────┬────────────────────┘
             │ confidence
             ▼
┌────────────────────────────┐
│  Transparency Label        │
│  Generator                 │
│  → label_text  (string)   │
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


APPEAL FLOW
===========

  Client
    │
    │  POST /appeal  { content_id, creator_reasoning }
    ▼
┌───────────────────────────────────┐
│       /appeal endpoint            │
│  - look up content_id in DB       │
│  - validate status == classified  │
└────────────┬──────────────────────┘
             │
             ▼
┌───────────────────────────────────┐
│  Status Update                    │
│  "classified" → "under_review"    │
└────────────┬──────────────────────┘
             │
             ▼
┌───────────────────────────────────┐
│  Audit Log (SQLite)               │
│  append appeal_reasoning,         │
│  update status + timestamp        │
└────────────┬──────────────────────┘
             │ confirmation JSON
             ▼
          Client
```

Text enters through `POST /submit`, is classified by two independent signals, combined into a confidence score, mapped to a transparency label, written to the SQLite audit log, and returned. Appeals flow through `POST /appeal`, which looks up the original entry, flips its status to `under_review`, and persists the creator's reasoning alongside the original classification data for human review.

---

## Detection Signals

The system uses two genuinely independent signals. "Independent" means they measure different properties of the text — one semantic, one structural — so their agreement or disagreement is itself informative.

### Signal 1 — LLM Classifier (Groq `llama-3.3-70b-versatile`)

**What it measures:** Holistic semantic and stylistic coherence. The model reads the text as an experienced reader would, attending to vocabulary register, sentence rhythm, structural patterns, and characteristic AI writing tells — overly balanced hedging, generic transitional phrases ("It is important to note that…"), topics covered symmetrically as if from a checklist, absence of personal voice or unexpected associations.

**Why it separates AI from human writing:** AI language models are trained to produce high-probability, well-formed text. This produces a recognizable aesthetic: consistent formality even when informality would be natural, smooth transitions that announce what follows, and a tendency to cover all angles of an issue evenly. Human writing is messier — it wanders, has uneven energy, and carries an individual voice.

**Output:** A float `0.0–1.0`. The model is prompted to return only `{"ai_probability": <float>}` — structured JSON output that makes parsing deterministic. `temperature=0` ensures consistency across repeated calls.

**Blind spots:** Short texts (under ~80 words) give the model too little signal. Highly polished human prose — published essays, academic writing — can resemble AI output in formality and structure, risking false positives. Deliberately "humanized" AI output can evade semantic detection.

---

### Signal 2 — Stylometric Heuristics Engine (pure Python)

**What it measures:** Three statistical surface properties of the text, computed without any external API:

**Sentence-length variance (SLV):** The coefficient of variation (std / mean) of word counts across sentences. AI text produces uniformly medium-length sentences. Human writing varies more — short punchy sentences alongside longer ones. Weight in final score: **50%**.

**Type-token ratio (TTR):** Unique word types divided by total tokens. AI models frequently repeat mid-frequency words ("important," "ensure," "various," "stakeholders"), driving TTR down relative to more expressive human writing. Weight: **30%**.

**Punctuation density (PD):** Count of non-period punctuation (commas, dashes, parentheses, question marks, etc.) divided by total characters. Human writing — especially informal writing — uses a wider variety and higher density of non-period punctuation. Weight: **20%**.

Each raw metric is normalized to `[0, 1]` against calibrated bounds and **inverted** (higher human-like value → lower AI score), then combined:

```
stylo_score = 0.50 × slv_norm + 0.30 × ttr_norm + 0.20 × pd_norm
```

**Calibrated bounds (from measured sample texts):**

| Metric | Min | Max | Typical AI | Typical human |
|--------|-----|-----|------------|---------------|
| SLV | 0.15 | 0.65 | ~0.29 | ~0.56 |
| TTR | 0.60 | 0.90 | ~0.78 | ~0.79 |
| PD  | 0.003 | 0.012 | ~0.006 | ~0.009 |

**Output:** A float `0.0–1.0`. Pure Python — no external libraries, cannot fail due to API outage.

**Blind spots:** Non-native English speakers writing carefully in a formal register produce surface statistics similar to AI output — low SLV, conservative punctuation — and are the primary false-positive risk for this signal. Poetry and flash fiction break sentence segmentation entirely. Texts under ~80 words produce insufficient SLV variance for meaningful discrimination; in these cases SLV falls back to the midpoint of its range and the LLM signal carries more weight.

---

### Why These Two Signals Together

The LLM signal is semantic and holistic; the stylometric signal is structural and statistical. Neither has access to the other's inputs or computation. When both agree (both score high, or both score low), the combined confidence reflects genuine certainty. When they disagree, the wide uncertain band absorbs the conflict and the label honestly says so. That disagreement behavior is by design, not a failure mode.

---

## Confidence Scoring

### Formula

```
confidence = 0.60 × llm_score + 0.40 × stylo_score
```

The 60/40 weighting reflects that the LLM signal is semantically richer and typically more accurate on well-formed prose. The stylometric signal provides an independent structural check that is especially useful when the LLM is uncertain or the text is short.

### Score Bands

| Confidence | Attribution | Label variant |
|------------|-------------|---------------|
| 0.00 – 0.35 | `likely_human` | ✅ Variant B |
| 0.36 – 0.64 | `uncertain` | 🔍 Variant C |
| 0.65 – 1.00 | `likely_ai` | ⚠️ Variant A |

The uncertain band is intentionally wide at 29 percentage points. A false positive — labeling a human creator's genuine work as AI-generated — is significantly more harmful on a creative platform than a false negative. The system leans toward uncertainty rather than forcing a verdict when signals are mixed.

A score of 0.51 produces the uncertain label. A score of 0.95 produces the AI label. These are meaningfully different outputs, not the same label with a different number.

### Example Submissions with Contrasting Scores

**High-confidence AI example:**

Input: *"Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, stakeholders across various sectors must collaborate to ensure responsible deployment. The integration of AI systems into everyday workflows presents both opportunities and challenges..."*

```json
{
  "attribution": "likely_ai",
  "confidence": 0.8240,
  "llm_score": 0.9500,
  "stylo_score": 0.6100
}
```

**Lower-confidence (uncertain) example:**

Input: *"I've been thinking a lot about remote work lately. There are genuine tradeoffs — flexibility and no commute on one side, isolation and blurred work-life boundaries on the other. Studies show productivity varies widely by individual and role type."*

```json
{
  "attribution": "uncertain",
  "confidence": 0.4808,
  "llm_score": 0.6200,
  "stylo_score": 0.2705
}
```

The first example produces a directional verdict; the second honestly reports disagreement between signals rather than forcing a call.

---

## Transparency Labels

Three label variants are returned in the `label_text` field of every `/submit` response. The variant shown depends on the confidence band. All three mention the appeals path.

---

### Variant A — High-Confidence AI (`confidence ≥ 0.65`)

> ⚠️ AI-Generated Content Detected
>
> Our analysis indicates this content was likely created with AI assistance (confidence: {confidence_pct}%). This label is based on two independent signals: a language model assessment and a statistical writing-style analysis.
>
> If you are the creator and believe this is incorrect, you can submit an appeal below. Appeals are reviewed by our moderation team.

---

### Variant B — High-Confidence Human (`confidence ≤ 0.35`)

> ✅ Human-Written Content
>
> Our analysis suggests this content was written by a person, not generated by AI (confidence: {confidence_pct}%). This label reflects automated analysis and is not a guarantee.
>
> If you believe this assessment is wrong, you can submit an appeal below.

---

### Variant C — Uncertain (`0.36 ≤ confidence ≤ 0.64`)

> 🔍 Attribution Unclear
>
> Our automated analysis could not confidently determine whether this content was written by a person or generated by AI. This does not imply wrongdoing — some writing styles are difficult to assess automatically.
>
> If you are the creator, you may submit an appeal to add context that can help our moderation team make a more informed review.

---

### Label Design Notes

Variant A uses a warning icon but avoids accusatory language — "likely created with AI assistance" rather than anything that implies plagiarism or bad intent. Variant C explicitly says "this does not imply wrongdoing" to protect creators whose genuine work falls in the uncertain band. The confidence percentage is shown in Variants A and B where the score is directional; Variant C omits it to avoid giving a borderline number ("48% AI") that implies false precision at the boundary.

---

## Appeals Workflow

Any creator who receives a classification they believe is incorrect can file an appeal using the `content_id` returned by `/submit`.

**Required fields:**
- `content_id` — the UUID from the original submission
- `creator_reasoning` — free-text explanation, 10–2000 characters

**What the system does on appeal:**
1. Looks up the `content_id` in the database. Returns `404` if not found.
2. Validates that `status == "classified"`. Returns `409` if an appeal has already been filed (prevents duplicates).
3. Updates the record: sets `status = "under_review"`, stores `appeal_reasoning` and `appeal_timestamp`.
4. Returns HTTP 200 with a confirmation.

**What a human reviewer sees** when querying `GET /log?status=under_review`:
- The original `attribution`, `confidence`, `llm_score`, and `stylo_score`
- The creator's full `appeal_reasoning` text
- The `appeal_timestamp`
- The `text_snippet` (first 200 characters) for context

No automated re-classification occurs. The reviewer has the full decision context in a single log entry without needing to cross-reference anything.

**Status lifecycle:**
```
classified  →  under_review  →  reviewed
(on submit)    (on appeal)       (manual action — out of scope for v1)
```

**Example appeal curl command:**
```bash
curl -s -X POST http://localhost:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{
    "content_id": "PASTE-CONTENT-ID-HERE",
    "creator_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical casual prose."
  }' | python -m json.tool
```

---

## Rate Limiting

The `POST /submit` endpoint is rate-limited using Flask-Limiter:

```
10 requests per minute
100 requests per day
```

**Reasoning behind these numbers:**

A real creator submitting their own work does so infrequently. Even a prolific writer completing multiple pieces in one session is unlikely to exceed 10 submissions in a minute. The per-minute limit stops scripted flooding: an adversary trying to probe classifier behavior or overwhelm the Groq API would need to submit at least 11 times in 60 seconds, which this blocks. The per-day limit of 100 is generous for any single creator while preventing automated bulk submissions that could rack up Groq API costs or fill the audit log with noise.

`GET /log` and `POST /appeal` are not rate-limited — moderators querying the log need unrestricted access, and the appeal endpoint naturally limits itself because each `content_id` can only be appealed once.

**Rate limit in action** — sending 12 rapid requests should produce 10 × `200` then 2 × `429`:

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5000/submit \
    -H "Content-Type: application/json" \
    -d '{"text": "This is a rate limit test submission with enough words to pass validation and trigger the endpoint processing pipeline correctly.", "creator_id": "ratelimit-test"}'
done
```

Expected output:
```
200
200
200
200
200
200
200
200
200
200
429
429
```

---

## Audit Log

Every attribution decision is written to a SQLite database (`provenance.db`) at the time of classification. The log is queryable via `GET /log`.

**Schema:**

| Field | Type | Description |
|-------|------|-------------|
| `content_id` | TEXT | UUID4 primary key |
| `creator_id` | TEXT | Submitter identifier |
| `text_snippet` | TEXT | First 200 characters of submitted text |
| `attribution` | TEXT | `likely_ai`, `uncertain`, or `likely_human` |
| `confidence` | REAL | Combined confidence score (0.0–1.0) |
| `llm_score` | REAL | Signal 1 output |
| `stylo_score` | REAL | Signal 2 output |
| `label_text` | TEXT | Full transparency label shown to user |
| `status` | TEXT | `classified` or `under_review` |
| `appeal_reasoning` | TEXT | Creator's appeal text (NULL until appealed) |
| `appeal_timestamp` | TEXT | ISO-8601 timestamp of appeal (NULL until appealed) |
| `created_at` | TEXT | ISO-8601 classification timestamp |

**Sample log output** (`GET /log`):

```json
{
  "count": 3,
  "entries": [
    {
      "content_id": "3f7a2b1e-9c4d-4a1b-8e2f-1a2b3c4d5e6f",
      "creator_id": "test-user-1",
      "text_snippet": "The sun dipped below the horizon, painting the sky in hues of amber and rose...",
      "attribution": "likely_human",
      "confidence": 0.1420,
      "llm_score": 0.0800,
      "stylo_score": 0.2400,
      "label_text": "✅ Human-Written Content\n\nOur analysis suggests...",
      "status": "classified",
      "appeal_reasoning": null,
      "appeal_timestamp": null,
      "created_at": "2025-04-01T14:32:10.123Z"
    },
    {
      "content_id": "7c9e1a3d-2f4b-4c8e-9d1a-5b6c7d8e9f0a",
      "creator_id": "test-user-2",
      "text_snippet": "Artificial intelligence represents a transformative paradigm shift in modern society...",
      "attribution": "likely_ai",
      "confidence": 0.8240,
      "llm_score": 0.9500,
      "stylo_score": 0.6100,
      "label_text": "⚠️ AI-Generated Content Detected\n\nOur analysis indicates...",
      "status": "classified",
      "appeal_reasoning": null,
      "appeal_timestamp": null,
      "created_at": "2025-04-01T14:35:22.456Z"
    },
    {
      "content_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "creator_id": "test-user-3",
      "text_snippet": "I've been thinking a lot about remote work lately. There are genuine tradeoffs...",
      "attribution": "uncertain",
      "confidence": 0.4808,
      "llm_score": 0.6200,
      "stylo_score": 0.2705,
      "label_text": "🔍 Attribution Unclear\n\nOur automated analysis could not confidently determine...",
      "status": "under_review",
      "appeal_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical.",
      "appeal_timestamp": "2025-04-01T14:50:00.000Z",
      "created_at": "2025-04-01T14:38:47.789Z"
    }
  ]
}
```

---

## Known Limitations

**1. Stylometric signal loses discrimination on short text (under ~80 words)**

The sentence-length variance metric requires at least 6–8 sentences to produce a statistically meaningful coefficient of variation. At 30–55 words (3–4 sentences), both AI and human text produce nearly identical SLV values, causing `stylo_score` to cluster near 0.40–0.45 for both classes. In these cases the LLM signal (60% weight) carries the classification. On texts of 80+ words, the stylometric signal correctly separates AI prose (~0.53–0.61) from human prose (~0.27–0.32). This is a real limitation documented as a design constraint, not a bug — the system is honest about insufficient data by landing in the uncertain band.

**2. Poetry and structured verse will produce unreliable stylometric scores**

The stylometric engine was calibrated on prose. Line-break-heavy text breaks sentence segmentation; intentionally constrained vocabulary (haiku, villanelle) reduces TTR artificially; unconventional punctuation disrupts the punctuation density metric. A poem may score as AI-generated even when it is demonstrably human. A production deployment would detect line-break-heavy text and suppress stylometric scoring weight, or route it to a human reviewer immediately. This is out of scope for v1.

**3. Non-native English speakers writing formally are the primary false-positive risk**

A creator writing carefully in a second language — avoiding colloquialisms, using complete sentences, maintaining consistent grammar — produces surface statistics that closely resemble AI output. The stylometric signal has no way to distinguish "deliberate formal register" from "AI uniformity." The LLM signal may partially compensate if the prose carries idiosyncratic phrasing or personal content, but there is no guarantee. The label design acknowledges this: Variant C says "this does not imply wrongdoing" and all variants surface the appeals path prominently.

---

## Spec Reflection

**One way the spec guided implementation:**

Writing the three label variants verbatim in `planning.md` before writing any code was the most valuable constraint in the project. When it came time to implement `labels.py`, the function was trivial — the hard design work (what tone to use, whether to show confidence percentages, what Variant C says about wrongdoing) had already been done. Without that pre-commitment, it would have been easy to write a generic label that just said "AI detected: 82%" and called it done. The spec forced a UX decision before a technical one.

**One way implementation diverged from the spec:**

The spec set a 50-word minimum for submissions. In practice, the four canonical test inputs from Milestone 4 are 39–55 words — meaning they would have been rejected by the endpoint I designed. Rather than rewriting the test inputs, I lowered the minimum to 30 words after measuring that the LLM signal still produces meaningful scores at that length. The stylometric signal degrades at short lengths regardless of where the cutoff is, but that degradation is honest (it produces mid-range scores rather than wrong ones). The spec's 50-word floor was a theoretical calibration guess; 30 words is the empirically tested floor for the LLM signal.

---

## AI Usage

**Instance 1 — Flask app skeleton and LLM signal function**

I provided the architecture diagram and Detection Signals section from `planning.md` and asked Claude to generate the Flask app skeleton with the `POST /submit` stub and a standalone `classify_with_llm()` function. The generated skeleton matched the API contract correctly. I revised: the system prompt for the Groq call was too permissive — the model still occasionally returned a brief explanation before the JSON object despite the instruction. I added explicit stripping of markdown fences and tightened the prompt to "Do not add any text outside the JSON object."

**Instance 2 — Stylometric engine and confidence scoring**

I provided the Signal 2 detail (including the normalization formula and bounds table) and the Uncertainty Representation section, then asked Claude to generate `compute_stylometric_score()` and `compute_confidence()`. The generated normalization bounds (`_SLV_MIN=0.10, _SLV_MAX=0.80`) were theoretically reasonable but empirically wrong — when tested against real text, the bounds were too wide, causing all inputs to cluster near 0.4–0.5. I measured the actual raw metric values on sample texts, discovered the real AI/human SLV range is roughly 0.29 vs. 0.56 (not 0.10–0.80), and recalibrated the bounds to `0.15–0.65`. The generated logic was correct; the constants needed real-world calibration that only testing could provide.

## Milestone 5 Test 
To test that rate limiting is working, run this in a new terminal window while your Flask server is running (it sends 12 rapid requests — more than the 10/minute limit):

for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5000/submit \
    -H "Content-Type: application/json" \
    -d '{"text": "This is a test submission for rate limit testing purposes only.", "creator_id": "ratelimit-test"}'
done

$ for i in $(seq 1 12); do   curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5000/submit     -H "Content-Type: application/json"     -d '{"text": "This is a test submission for rate limit testing purposes only. Please do not try this at home. Please play table tennis like a serious person instead. Who is JSON and who is son?", "creator_id": "ratelimit-test"}'; done
200
200
200
200
200
200
200
200
200
200
429
429
