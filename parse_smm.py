# parse_smm_v3.py
import pdfplumber, re, csv, unicodedata
from difflib import SequenceMatcher
from smm_structure import SMM_STRUCTURE

PDF_PATH = "SMM.pdf"
OUTPUT_CSV = "smm_clean.csv"


# --------------------------
# Helpers
# --------------------------
def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    s = re.sub(r"[\u00A0]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm_key(s: str) -> str:
    s = norm(s).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fuzzy_in(line_norm, options_norm):
    if line_norm in options_norm:
        return options_norm[line_norm]
    best, best_score = None, 0.0
    for k, v in options_norm.items():
        if line_norm.startswith(k) or k.startswith(line_norm):
            score = 1.0
        else:
            score = SequenceMatcher(None, line_norm, k).ratio()
        if score > best_score:
            best, best_score = v, score
    return best if best_score >= 0.88 else None


def split_title_and_text_after_number(body: str):
    """
    For lines like: 'Bills of Quantities shall fully describe...'
    Try to split 'Bills of Quantities' (title) from the rest (text).
    Heuristic: split before the first common verb or punctuation separator.
    """
    # 1) Verb boundary heuristic
    m = re.search(
        r"\b(shall|are|is|were|will|should|must|means?|includes?|consists?|comprises?|apply|covers?)\b",
        body,
        flags=re.IGNORECASE,
    )
    if m:
        title = body[: m.start()].strip(" :-—–.,;")
        text = body[m.start() :].strip()
        if len(title) >= 3:
            return title, text

    # 2) Punctuation delimiter like ":" "-" "—" "–"
    m2 = re.match(r"^(?P<title>[^:—–-]{3,100})[:—–-]\s*(?P<text>.+)$", body)
    if m2:
        return m2.group("title").strip(), m2.group("text").strip()

    # Fallback: no obvious split -> no title
    return None, body


# Build normalized subsection lookup
SUBS_LOOKUP = {}
for sec, items in SMM_STRUCTURE.items():
    SUBS_LOOKUP[sec] = {norm_key(it): it for it in items}

# --------------------------
# Patterns
# --------------------------
CONTENTS_START = re.compile(r"^CONTENTS$", re.IGNORECASE)
SECTION_LINE = re.compile(r"^SECTION\s+([A-Z])\b", re.IGNORECASE)
TOC_SECTION = re.compile(
    r"^([A-Z])[:.]?\s+([A-Z][A-Z\s,&]+)$"
)  # e.g., "A: GENERAL RULES"

# e.g., "A1 Bills of Quantities..."
TOP_CLAUSE = re.compile(r"^([A-Z])(\d{1,3})\s+(.+)$")

# e.g., "... Bills of Quantities A1 shall fully describe ..."
# title: any text before the number; sec: A; num: 1; body: trailing text after the number
CLAUSE_ANYWHERE = re.compile(
    r"^(?P<title>.*?)\b(?P<sec>[A-Z])(?P<num>\d{1,3})\b\s+(?P<body>.+)$"
)

SUBCLAUSE_SPLIT = re.compile(r"(?<!\w)\(([a-z])\)\s+")
NOISE = re.compile(r"^(Downloaded by |lOMoARcPSD|Studocu\b)", re.IGNORECASE)

# --------------------------
# State
# --------------------------
results = []
cur_sec = None
cur_sec_title = None
cur_sub = None
cur_clause_ref = None
cur_clause_buf = []
cur_clause_title = None
in_toc = False


def flush_clause(clause_type="clause"):
    """
    Flush the current clause buffer as a single 'clause' row.
    If subclauses were already emitted, the buffer may be empty (and we emit nothing).
    """
    global cur_clause_buf
    if cur_clause_ref and cur_clause_buf:
        text = " ".join(cur_clause_buf).strip()
        if text:
            results.append(
                {
                    "id": cur_clause_ref,
                    "section_code": cur_sec,
                    "section_title": cur_sec_title,
                    "subsection_title": cur_sub,
                    "clause_ref": cur_clause_ref,
                    "subclause_ref": None,
                    "clause_title": cur_clause_title,
                    "clause_text": text,
                    "clause_type": clause_type,
                }
            )
    cur_clause_buf.clear()


def emit_subclauses(parent_ref, paragraph_text, clause_title_for_parent):
    """
    If paragraph_text contains (a) (b) ... subclauses, emit one parent clause row (for head text, if any)
    and one row per subclause. Returns True if subclauses were emitted.
    """
    parts = SUBCLAUSE_SPLIT.split(paragraph_text)
    if len(parts) <= 1:
        return False

    buf_head = parts[0].strip()
    if buf_head:
        # Emit the parent clause's preface text (still type 'clause')
        results.append(
            {
                "id": parent_ref,
                "section_code": cur_sec,
                "section_title": cur_sec_title,
                "subsection_title": cur_sub,
                "clause_ref": parent_ref,
                "subclause_ref": None,
                "clause_title": clause_title_for_parent,
                "clause_text": buf_head,
                "clause_type": "clause",
            }
        )

    # Emit each (a)/(b)/... subclause
    for i in range(1, len(parts), 2):
        letter = parts[i]
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if body:
            subref = f"{parent_ref}({letter})"
            results.append(
                {
                    "id": subref,
                    "section_code": cur_sec,
                    "section_title": cur_sec_title,
                    "subsection_title": cur_sub,
                    "clause_ref": parent_ref,
                    "subclause_ref": subref,
                    "clause_title": clause_title_for_parent,  # keep the parent title context
                    "clause_text": body,
                    "clause_type": "subclause",
                }
            )
    return True


with pdfplumber.open(PDF_PATH) as pdf:
    pending_title_for_section = False

    for page in pdf.pages:
        raw = page.extract_text() or ""
        # Basic de-hyphenation at line wraps
        raw = raw.replace("-\n", "")

        for line in raw.split("\n"):
            if not line or NOISE.search(line):
                continue
            s = norm(line)

            # Detect TOC start
            if CONTENTS_START.match(s):
                in_toc = True
                continue

            # Skip TOC entries (they'll be re-parsed as actual content later)
            if in_toc:
                # End TOC when we hit actual section content
                if SECTION_LINE.match(s):
                    in_toc = False
                else:
                    continue

            # Section header (two-line pattern)
            m = SECTION_LINE.match(s)
            if m:
                flush_clause()
                cur_sec = m.group(1).upper()
                cur_sec_title = None
                cur_sub = None
                cur_clause_ref = None
                cur_clause_title = None
                pending_title_for_section = True
                continue

            # Section title (next line after SECTION X)
            if pending_title_for_section and s:
                cur_sec_title = s.title()
                pending_title_for_section = False
                results.append(
                    {
                        "id": None,
                        "section_code": cur_sec,
                        "section_title": cur_sec_title,
                        "subsection_title": None,
                        "clause_ref": None,
                        "subclause_ref": None,
                        "clause_title": None,
                        "clause_text": cur_sec_title,
                        "clause_type": "section_header",
                    }
                )
                continue

            # Subsection detection
            if cur_sec:
                sub_norm = norm_key(s)
                hit = fuzzy_in(sub_norm, SUBS_LOOKUP[cur_sec])
                if hit:
                    flush_clause()
                    cur_sub = hit
                    cur_clause_ref = None
                    cur_clause_title = None
                    results.append(
                        {
                            "id": None,
                            "section_code": cur_sec,
                            "section_title": cur_sec_title,
                            "subsection_title": cur_sub,
                            "clause_ref": None,
                            "subclause_ref": None,
                            "clause_title": None,
                            "clause_text": cur_sub,
                            "clause_type": "subsection",
                        }
                    )
                    continue

            # Clause detection (A1 at start) -> "A1 Bills of Quantities ..."
            m = TOP_CLAUSE.match(s)
            if m and cur_sec and m.group(1) == cur_sec:
                flush_clause()
                cur_clause_ref = f"{m.group(1)}{m.group(2)}"
                body = m.group(3).strip()
                # Try to split clause_title from the body text
                title_guess, body_after = split_title_and_text_after_number(body)
                cur_clause_title = title_guess
                # Emit subclauses if present; otherwise start buffering the body
                if not emit_subclauses(cur_clause_ref, body_after, cur_clause_title):
                    cur_clause_buf = [body_after] if body_after else []
                continue

            # Fallback: Clause detection when number appears later in the line
            # e.g., "Bills of Quantities A1 shall fully describe ..."
            m2 = CLAUSE_ANYWHERE.match(s) if cur_sec else None
            if m2 and m2.group("sec") == cur_sec:
                flush_clause()
                cur_clause_ref = f"{m2.group('sec')}{m2.group('num')}"
                cur_clause_title = (m2.group("title") or "").strip(" :-—–.,;") or None
                body_after = (m2.group("body") or "").strip()
                if not emit_subclauses(cur_clause_ref, body_after, cur_clause_title):
                    cur_clause_buf = [body_after] if body_after else []
                continue

            # Continuation text for current clause
            if cur_clause_ref:
                cur_clause_buf.append(s)
            elif cur_sec or cur_sub:
                # Orphan text in known section/subsection -> keep as a note
                results.append(
                    {
                        "id": None,
                        "section_code": cur_sec,
                        "section_title": cur_sec_title,
                        "subsection_title": cur_sub,
                        "clause_ref": None,
                        "subclause_ref": None,
                        "clause_title": None,
                        "clause_text": s,
                        "clause_type": "note",
                    }
                )

# Final flush for trailing buffered clause text
flush_clause()

# Write CSV
with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "id",  # A1, A2(a), etc.
            "section_code",  # A, B, ...
            "section_title",  # e.g., General Rules
            "subsection_title",  # e.g., Bills of Quantities (if you model subsections that way)
            "clause_ref",  # e.g., A2
            "subclause_ref",  # e.g., A2(a)
            "clause_title",  # e.g., Bills of Quantities
            "clause_text",  # body text
            "clause_type",  # section_header | subsection | clause | subclause | note
        ],
    )
    writer.writeheader()
    writer.writerows(results)

print(f"✅ Parsed {len(results)} rows into {OUTPUT_CSV}")
