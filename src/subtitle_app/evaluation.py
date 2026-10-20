"""Character error counts for Chinese ASR; references must match the audio."""

from functools import lru_cache
import unicodedata


@lru_cache(maxsize=1)
def _converter():
    from opencc import OpenCC
    return OpenCC("t2s")


def normalize_for_cer(text: str) -> str:
    text = _converter().convert(unicodedata.normalize("NFKC", text)).casefold()
    return "".join(char for char in text if unicodedata.category(char)[0] in "LN")


def character_errors(reference: str, hypothesis: str) -> dict:
    ref, hyp = normalize_for_cer(reference), normalize_for_cer(hypothesis)
    rows, cols = len(ref) + 1, len(hyp) + 1
    distance = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        distance[i][0] = i
    for j in range(cols):
        distance[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            distance[i][j] = min(distance[i - 1][j] + 1, distance[i][j - 1] + 1,
                                 distance[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]))
    i, j = len(ref), len(hyp)
    edits = []
    counts = {"substitutions": 0, "deletions": 0, "insertions": 0}
    while i or j:
        if i and j and ref[i - 1] == hyp[j - 1]:
            i, j = i - 1, j - 1
        elif i and j and distance[i][j] == distance[i - 1][j - 1] + 1:
            i, j = i - 1, j - 1
            counts["substitutions"] += 1
            edits.append({"operation": "replace", "reference": ref[i], "hypothesis": hyp[j], "reference_index": i})
        elif i and distance[i][j] == distance[i - 1][j] + 1:
            i -= 1
            counts["deletions"] += 1
            edits.append({"operation": "delete", "reference": ref[i], "hypothesis": "", "reference_index": i})
        else:
            j -= 1
            counts["insertions"] += 1
            edits.append({"operation": "insert", "reference": "", "hypothesis": hyp[j], "reference_index": i})
    return {**counts, "reference_characters": len(ref), "hypothesis_characters": len(hyp),
            "cer": distance[-1][-1] / len(ref) if ref else None,
            "edits": list(reversed(edits))}
