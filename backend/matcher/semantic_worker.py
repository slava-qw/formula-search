"""Bounded scalar evaluation, with one-shot and reusable line-protocol modes."""
import json
import sys
import time

from backend.matcher.ast_match import parse_query, structural_signature, semantically_equal, MAX_SEMANTIC_OPS


def evaluate_request(data):
    query = parse_query(data["query"])
    signature = structural_signature(query)
    # Candidate budget starts after parsing the query. The parent separately
    # enforces a hard wall timeout covering startup and query parsing as well.
    deadline = time.monotonic() + data["budget"]
    indices = []
    status = "complete" if signature is not None else "unsupported"
    if signature is not None and signature[1] > MAX_SEMANTIC_OPS:
        status = "limited"
    elif signature is not None:
        for index, latex in data["candidates"]:
            if time.monotonic() >= deadline:
                status = "limited"
                break
            candidate = parse_query(latex)
            candidate_signature = structural_signature(candidate)
            if candidate_signature is None or candidate_signature[1] > MAX_SEMANTIC_OPS:
                status = "limited"
                continue
            # Operation/symbol-count differences are not necessary conditions
            # for equivalence: expansion or cancellation changes both counts.
            # The existing candidate, operation and wall limits bound this work.
            if semantically_equal(query, candidate):
                indices.append(index)
    return {"indices": indices, "status": status}


def main():
    print(json.dumps(evaluate_request(json.load(sys.stdin))))


def serve():
    # Retire even if a future parent forgets its request-count policy. The
    # parent also expires idle workers; EOF ends an orphaned idle child.
    max_frame = 512*81*12+8192  # supplementary Unicode uses two escaped surrogates
    for _ in range(32):
        line = sys.stdin.readline(max_frame+1)
        if not line or len(line) > max_frame or not line.endswith('\n'):
            return
        data = json.loads(line)
        print(json.dumps({'id': data['id'], 'result': evaluate_request(data)}), flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ['--serve']:
        serve()
    else:
        main()
