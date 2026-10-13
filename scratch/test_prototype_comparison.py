import re
from collections import Counter, defaultdict

def resolve_svc_column(headers, rows):
    for h in headers:
        hn = re.sub(r"[^a-z0-9]+", " ", (h or "").lower()).strip()
        if hn in ("service code", "servicecode", "service cd", "service no", "service id", "code"):
            return h
        if "service" in hn and "code" in hn:
            return h
    if rows:
        svc_pattern = re.compile(r"^(?:015[1-9]|0178|0185|\d{4})$")
        for h in headers:
            sample_vals = [str(r.get(h) or "").strip() for r in rows[:10] if str(r.get(h) or "").strip()]
            if sample_vals and all(svc_pattern.match(v) for v in sample_vals):
                return h
    return None

def compute_comparison(doc_a_name, headers_a, rows_a, doc_b_name, headers_b, rows_b, filter_diff_only=False, is_ta=False):
    col_a = resolve_svc_column(headers_a, rows_a)
    col_b = resolve_svc_column(headers_b, rows_b)
    if not col_a or not col_b:
        return f"Could not find service code column: col_a={col_a}, col_b={col_b}"
    
    codes_a = []
    rows_by_code_a = defaultdict(list)
    for idx, r in enumerate(rows_a, 1):
        c = str(r.get(col_a) or "").strip()
        if c:
            codes_a.append(c)
            rows_by_code_a[c].append(idx)
            
    codes_b = []
    rows_by_code_b = defaultdict(list)
    for idx, r in enumerate(rows_b, 1):
        c = str(r.get(col_b) or "").strip()
        if c:
            codes_b.append(c)
            rows_by_code_b[c].append(idx)
            
    counts_a = Counter(codes_a)
    counts_b = Counter(codes_b)
    unique_a = sorted(list(counts_a.keys()))
    unique_b = sorted(list(counts_b.keys()))
    common = sorted(list(set(unique_a) & set(unique_b)))
    only_a = sorted(list(set(unique_a) - set(unique_b)))
    only_b = sorted(list(set(unique_b) - set(unique_a)))
    
    unique_a_str = ", ".join(unique_a) if unique_a else "None"
    unique_b_str = ", ".join(unique_b) if unique_b else "None"
    common_str = ", ".join(common) if common else "None"
    only_a_str = ", ".join(only_a) if only_a else "None"
    only_b_str = ", ".join(only_b) if only_b else "None"
    
    total_a_breakdown = f"{len(codes_a)} (" + ", ".join(f"{c}: {counts_a[c]}" for c in unique_a) + ")" if unique_a else "0"
    total_b_breakdown = f"{len(codes_b)} (" + ", ".join(f"{c}: {counts_b[c]}" for c in unique_b) + ")" if unique_b else "0"
    
    if filter_diff_only:
        table = (
            f"| Comparison | File A ({doc_a_name}) | File B ({doc_b_name}) |\n"
            f"|---|---|---|\n"
            f"| Unique service codes | {unique_a_str} | {unique_b_str} |\n"
            f"| Codes only in this file (Differences) | {only_a_str} | {only_b_str} |\n"
            f"| Total occurrences | {total_a_breakdown} | {total_b_breakdown} |"
        )
    else:
        table = (
            f"| Comparison | File A ({doc_a_name}) | File B ({doc_b_name}) |\n"
            f"|---|---|---|\n"
            f"| Unique service codes | {unique_a_str} | {unique_b_str} |\n"
            f"| Codes found in both | {common_str} | {common_str} |\n"
            f"| Codes only in this file | {only_a_str} | {only_b_str} |\n"
            f"| Total occurrences | {total_a_breakdown} | {total_b_breakdown} |"
        )
        
    lines = [table, ""]
    if only_a or only_b:
        lines.append("### Service Code Comparison Summary:")
        lines.append("- **Discrepancy Confirmed**: The files do **not** have the same service codes.")
        if not filter_diff_only and common:
            c_a = "; ".join(f"{c} (row {', '.join(map(str, rows_by_code_a[c]))})" for c in common)
            c_b = "; ".join(f"{c} (row {', '.join(map(str, rows_by_code_b[c]))})" for c in common)
            lines.append(f"- **Codes found in both**: {common_str} ({doc_a_name}: {c_a}; {doc_b_name}: {c_b}).")
        if only_a:
            c_a = "; ".join(f"{c} (row {', '.join(map(str, rows_by_code_a[c]))})" for c in only_a)
            lines.append(f"- **Codes only in File A ({doc_a_name})**: {only_a_str} ({c_a}).")
        if only_b:
            c_b = "; ".join(f"{c} (row {', '.join(map(str, rows_by_code_b[c]))})" for c in only_b)
            lines.append(f"- **Codes only in File B ({doc_b_name})**: {only_b_str} ({c_b}).")
    else:
        lines.append("### Service Code Comparison Summary:")
        lines.append(f"- **Exact Match Confirmed**: Both files contain the exact same set of unique service codes (`{common_str}`).")
        
    return "\n".join(lines)

rows_a = [
    {"application_number": "2026/0153/01/0001", "service_code": "0153", "applicant_name": "Ramesh"},
    {"application_number": "2026/0154/01/0002", "service_code": "0154", "applicant_name": "Suresh"},
    {"application_number": "2026/0153/01/0003", "service_code": "0153", "applicant_name": "Mahesh"},
]
rows_b = [
    {"application_number": "2026/0154/01/0004", "service_code": "0154", "applicant_name": "Dinesh"},
    {"application_number": "2026/0178/01/0005", "service_code": "0178", "applicant_name": "Ganesh"},
]

print(compute_comparison("file_a.csv", ["application_number", "service_code", "applicant_name"], rows_a,
                         "file_b.csv", ["application_number", "service_code", "applicant_name"], rows_b))
