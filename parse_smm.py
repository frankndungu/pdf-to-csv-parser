# parse_smm_pgsql.py
import pdfplumber, re, csv, unicodedata
from smm_structure import SMM_STRUCTURE

PDF_PATH = "SMM.pdf"
OUTPUT_CSV = "smm_clean.csv"


# --------------------------
# Helpers
# --------------------------
def clean_text(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    s = s.replace("-\n", "")  # join hyphenated words
    s = s.replace("\n", " ")  # join wrapped lines
    s = re.sub(r"[\u00A0]", " ", s)  # non-breaking spaces
    s = re.sub(r"\s+", " ", s).strip()
    return s


def split_title_and_body(body: str):
    m = re.search(
        r"\b(shall|are|is|were|will|should|must|means?|includes?|consists?|comprises?|apply|covers?)\b",
        body,
        flags=re.IGNORECASE,
    )
    if m:
        return body[: m.start()].strip(" :-—–.,;"), body[m.start() :].strip()
    return None, body


row_counter = {}


def emit_row(ref, title, text, clause_type, section_code, subsection_title=None):
    """Emit a CSV row with structure info, generating IDs for headers/subsections."""
    section_items = SMM_STRUCTURE.get(section_code, ["Unknown"])
    section_ref = section_items[0] if section_items else "Unknown"

    # counter for ordering
    row_counter.setdefault(section_code, 0)
    row_counter[section_code] += 1
    order_in_section = row_counter[section_code]

    # generate synthetic IDs if missing
    uid_ref = ref
    if not uid_ref:
        if clause_type == "section_header":
            uid_ref = f"{section_code}_HEADER"
        elif clause_type == "subsection":
            uid_ref = f"{section_code}_SUB_{order_in_section}"

    results.append(
        {
            "id": uid_ref,
            "section_code": section_code,
            "section_ref": section_ref,
            "subsection_title": subsection_title,
            "clause_ref": (
                ref if ref and "(" not in ref else (ref.split("(")[0] if ref else None)
            ),
            "subclause_ref": ref if ref and "(" in ref else None,
            "clause_title": title,
            "clause_text": text.strip() if text else "",
            "clause_type": clause_type,
            "order_in_section": order_in_section,
        }
    )


# --------------------------
# Regex patterns
# --------------------------
SECTION_LINE = re.compile(r"^SECTION\s+([A-Z])\b", re.IGNORECASE)
TOP_CLAUSE = re.compile(r"^([A-Z])(\d{1,3})\s+(.+)$")
CLAUSE_ANYWHERE = re.compile(
    r"^(?P<title>.*?)\b(?P<sec>[A-Z])(?P<num>\d{1,3})\b\s+(?P<body>.+)$"
)
SUBCLAUSE_SPLIT = re.compile(r"(?<!\w)\(([a-z])\)\s+")
NOISE = re.compile(r"^(Downloaded by |lOMoARcPSD|Studocu\b)", re.IGNORECASE)

# --------------------------
# State
# --------------------------
results = []
cur_sec, cur_sub = None, None
pdf_ids = set()

# --------------------------
# Parse PDF
# --------------------------
with pdfplumber.open(PDF_PATH) as pdf:
    for page in pdf.pages:
        raw = page.extract_text() or ""
        for line in raw.split("\n"):
            if not line or NOISE.search(line):
                continue
            s = clean_text(line)

            # Section start
            m = SECTION_LINE.match(s)
            if m:
                cur_sec = m.group(1).upper()
                cur_sub = None

                section_items = SMM_STRUCTURE.get(cur_sec, [])
                emit_row(
                    None,
                    None,
                    section_items[0] if section_items else "",
                    "section_header",
                    cur_sec,
                )

                for sub in section_items[1:]:
                    emit_row(
                        None, None, sub, "subsection", cur_sec, subsection_title=sub
                    )
                continue

            # Clause at start of line
            m = TOP_CLAUSE.match(s)
            if m and cur_sec and m.group(1) == cur_sec:
                clause_ref = f"{m.group(1)}{m.group(2)}"
                pdf_ids.add(clause_ref)
                body = clean_text(m.group(3))
                title_guess, body_after = split_title_and_body(body)

                if body_after:
                    parts = SUBCLAUSE_SPLIT.split(body_after)
                    if len(parts) > 1:
                        emit_row(
                            clause_ref,
                            title_guess,
                            parts[0],
                            "clause",
                            cur_sec,
                            cur_sub,
                        )
                        for i in range(1, len(parts), 2):
                            letter = parts[i]
                            text = parts[i + 1].strip() if i + 1 < len(parts) else ""
                            subref = f"{clause_ref}({letter})"
                            pdf_ids.add(subref)
                            emit_row(
                                subref, title_guess, text, "subclause", cur_sec, cur_sub
                            )
                    else:
                        emit_row(
                            clause_ref,
                            title_guess,
                            body_after,
                            "clause",
                            cur_sec,
                            cur_sub,
                        )
                else:
                    emit_row(clause_ref, title_guess, "", "clause", cur_sec, cur_sub)
                continue

            # Clause inline
            m2 = CLAUSE_ANYWHERE.match(s) if cur_sec else None
            if m2 and m2.group("sec") == cur_sec:
                clause_ref = f"{m2.group('sec')}{m2.group('num')}"
                pdf_ids.add(clause_ref)
                title_guess = (m2.group("title") or "").strip(" :-—–.,;") or None
                body_after = clean_text(m2.group("body"))
                emit_row(
                    clause_ref, title_guess, body_after, "clause", cur_sec, cur_sub
                )
                continue

# --------------------------
# Write CSV
# --------------------------
with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "id",
            "section_code",
            "section_ref",
            "subsection_title",
            "clause_ref",
            "subclause_ref",
            "clause_title",
            "clause_text",
            "clause_type",
            "order_in_section",
        ],
    )
    writer.writeheader()
    writer.writerows(results)

# --------------------------
# Validation
# --------------------------
csv_ids = {row["id"] for row in results if row["id"]}
missing = sorted(pdf_ids - csv_ids)
extra = sorted(csv_ids - pdf_ids)

print(f"✅ Parsed {len(results)} rows into {OUTPUT_CSV}")
print(f"📑 Unique clauses detected in PDF: {len(pdf_ids)}")
print(f"📝 Unique clauses written to CSV: {len(csv_ids)}")
print(f"❌ Missing in CSV: {missing if missing else 'None'}")
print(f"⚠️ Extra in CSV: {extra[:20]} (showing first 20)")
