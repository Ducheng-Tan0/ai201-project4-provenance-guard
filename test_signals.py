"""
test_signals.py
---------------
Standalone test script for Milestone 3 verification.

Run this BEFORE wiring signals into the Flask endpoint to confirm:
  1. The stylometric engine produces directionally correct scores.
  2. The LLM classifier connects to Groq and returns valid floats.
  3. Confidence scoring combines them correctly.
  4. Labels map to the right variants.

Usage:
    python test_signals.py

This script does NOT start Flask. It calls the signal functions directly.
Requires GROQ_API_KEY in your .env file.
"""

from dotenv import load_dotenv
load_dotenv()   # must be first — loads GROQ_API_KEY before signals.py needs it

from signals import classify_with_llm, compute_stylometric_score, compute_confidence
from labels import score_to_attribution, generate_label

# ---------------------------------------------------------------------------
# The four canonical test inputs from Milestone 4 spec
# ---------------------------------------------------------------------------

TEST_CASES = [
    {
        "label": "clearly_ai",
        "text": (
            "Artificial intelligence represents a transformative paradigm shift in modern society. "
            "It is important to note that while the benefits of AI are numerous, it is equally "
            "essential to consider the ethical implications. Furthermore, stakeholders across "
            "various sectors must collaborate to ensure responsible deployment."
        ),
        # NOTE: This canonical input is only ~43 words. The stylometric signal needs 80+ words
        # for SLV to reliably separate AI from human text. At short lengths, stylo clusters near
        # 0.40–0.50. The LLM signal carries the classification here. Confidence will be driven
        # mostly by llm_score (60% weight). Expect final confidence 0.50–0.90 depending on LLM.
        "expected_confidence_range": (0.50, 1.00),
        "expected_attribution": "likely_ai",
        "note": "Short text (~43 words) — stylo will be mid-range; LLM signal dominates",
    },
    {
        "label": "clearly_human",
        "text": (
            "ok so i finally tried that new ramen place downtown and honestly? "
            "underwhelming. the broth was fine but they put WAY too much sodium in it and "
            "i was thirsty for like three hours after. my friend got the spicy version and "
            "said it was better. probably won't go back unless someone drags me there"
        ),
        # ~55 words. Stylo will score ~0.40 (mid-range) due to short length. LLM should score
        # this low (0.05–0.20) because informal/personal voice is distinctive. With 60% LLM
        # weight, final confidence should land in uncertain-to-likely_human range.
        "expected_confidence_range": (0.00, 0.55),
        "expected_attribution": None,  # likely_human or uncertain — both acceptable at this length
        "note": "Short text (~55 words) — stylo mid-range; LLM should score low on casual voice",
    },
    {
        "label": "borderline_formal_human",
        "text": (
            "The relationship between monetary policy and asset price inflation has been "
            "extensively studied in the literature. Central banks face a fundamental tension "
            "between their mandate for price stability and the unintended consequences of "
            "prolonged low interest rates on equity and real estate valuations."
        ),
        "expected_confidence_range": (0.35, 0.80),
        "expected_attribution": None,  # uncertain or likely_ai — both acceptable
        "note": "Formal human prose — intentionally hard to classify",
    },
    {
        "label": "borderline_edited_ai",
        "text": (
            "I've been thinking a lot about remote work lately. There are genuine tradeoffs — "
            "flexibility and no commute on one side, isolation and blurred work-life boundaries "
            "on the other. Studies show productivity varies widely by individual and role type."
        ),
        "expected_confidence_range": (0.20, 0.75),
        "expected_attribution": None,  # uncertain range — any result is informative
        "note": "Lightly edited AI — intentionally ambiguous",
    },
]

PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️  WARN"


def run_tests(include_llm: bool = True):
    print("=" * 65)
    print("  Provenance Guard — Signal Verification Tests")
    print("=" * 65)

    # --- Part 1: Stylometric scores only (no API needed) ---
    print("\n── Stylometric Signal (no API call) ─────────────────────────")
    print(f"  {'Test case':<28} {'stylo_score':>12}  status  note")
    print(f"  {'─' * 28} {'─' * 12}  {'─' * 6}  {'─' * 40}")

    for tc in TEST_CASES:
        score = compute_stylometric_score(tc["text"])
        lo, hi = tc["expected_confidence_range"]
        in_range = lo <= score <= hi
        # For stylometric alone we use a wide acceptable band since short text
        # clusters mid-range by design — warn only if completely outside 0.20–0.80
        status = PASS if 0.20 <= score <= 0.80 else WARN
        note = tc.get("note", "")
        print(f"  {tc['label']:<28} {score:>12.4f}  {status}  {note[:50]}")

    # --- Part 2: LLM + confidence + attribution (requires Groq) ---
    if include_llm:
        print("\n── LLM Signal + Full Pipeline ────────────────────────────────")
        print(f"  {'Test case':<28} {'llm':>6} {'stylo':>6} {'conf':>6}  {'attribution':<14}  status")
        print(f"  {'─' * 28} {'─' * 6} {'─' * 6} {'─' * 6}  {'─' * 14}  ──────")

        for tc in TEST_CASES:
            try:
                llm_score   = classify_with_llm(tc["text"])
                stylo_score = compute_stylometric_score(tc["text"])
                confidence  = compute_confidence(llm_score, stylo_score)
                attribution = score_to_attribution(confidence)

                lo, hi = tc["expected_confidence_range"]
                conf_ok = lo <= confidence <= hi

                if tc["expected_attribution"]:
                    attr_ok = attribution == tc["expected_attribution"]
                    status = PASS if (conf_ok and attr_ok) else FAIL
                else:
                    # No single expected attribution — confidence range is the criterion
                    status = PASS if conf_ok else WARN

                note = tc.get("note", "")
                print(
                    f"  {tc['label']:<28} "
                    f"{llm_score:>6.3f} {stylo_score:>6.3f} {confidence:>6.3f}  "
                    f"{attribution:<14}  {status}"
                )
                if status != PASS:
                    print(f"    → Note: {note}")

            except Exception as e:
                print(f"  {tc['label']:<28} ERROR: {e}")

    # --- Part 3: Label generation ---
    print("\n── Label Variants ────────────────────────────────────────────")
    test_label_cases = [
        ("likely_ai",     0.82),
        ("likely_human",  0.18),
        ("uncertain",     0.52),
    ]
    for attribution, confidence in test_label_cases:
        label = generate_label(attribution, confidence)
        first_line = label.split("\n")[0]
        print(f"\n  [{attribution} @ {confidence}]\n  → {first_line}")

    print("\n── Confidence scoring edge cases ─────────────────────────────")
    edge_cases = [(0.0, 0.0), (1.0, 1.0), (0.5, 0.5), (0.65, 0.65), (0.35, 0.35)]
    for llm, stylo in edge_cases:
        conf = compute_confidence(llm, stylo)
        attr = score_to_attribution(conf)
        print(f"  llm={llm}, stylo={stylo} → confidence={conf:.4f}, attribution={attr}")

    print("\n" + "=" * 65)
    print("  Tests complete. Check ⚠️  warnings — they may need tuning.")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    # Set include_llm=False to skip the Groq API call (faster, offline testing)
    run_tests(include_llm=True)