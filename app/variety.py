def violations(rows: list[dict], rules: dict, scene_seconds: float = 0) -> list[int]:
    """Cảnh lặp cùng kiểu hình quá max_same_type lần liên tiếp. Camera không bị ép chuyển động."""
    max_same = rules.get("max_same_type", 2)
    bad, run = [], 1
    for i, s in enumerate(rows):
        if i and s.get("visual_type") == rows[i - 1].get("visual_type"):
            run += 1
            if run > max_same:
                bad.append(i)
        else:
            run = 1
    return bad
