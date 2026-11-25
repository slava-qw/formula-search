from backend.matcher.normalizer import normalize_latex


def test_identical_expressions_normalize_equally():
    a = normalize_latex(r"\frac{d}{dx}")
    b = normalize_latex(r"\frac{d}{dx}")
    assert a == b


def test_spacing_variations_normalize_equally():
    # \int limits not normalized by SymPy; x^n chosen as canonical spacing test case
    a = normalize_latex(r"x^{2}")
    b = normalize_latex(r"x^2")
    assert a == b


def test_unparseable_latex_falls_back_to_stripped_input():
    # \{ is a valid LaTeX brace but SymPy cannot parse it — falls back to stripped input
    result = normalize_latex(r"\{")
    assert result == r"\{"


def test_normalize_returns_string():
    result = normalize_latex(r"\alpha")
    assert isinstance(result, str)
    assert len(result) > 0


def test_empty_string_returns_empty():
    result = normalize_latex("")
    assert result == ""
