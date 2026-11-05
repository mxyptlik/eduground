from evaluation.program.metrics.citations import CitationLink, CitationTarget, validate_citation_targets


def test_explicit_link_targets_distinguish_source_context_and_locator():
    known = {"k1": CitationTarget("k1", "s1", "p. 2"),
             "k2": CitationTarget("k2", "s2", "p. 3")}
    links = [
        CitationLink("claim-1", "[1]", "k1", "s1", "p. 2"),
        CitationLink("claim-2", "[2]", "missing", "s1"),
        CitationLink("claim-3", "[3]", "k2", "s2"),
        CitationLink("claim-4", "[4]", "k1", "s2"),
        CitationLink("claim-5", "[5]", "k1", "s1", "p. 9"),
    ]
    checks = validate_citation_targets(links, context_chunk_ids={"k1"}, known_targets=known)
    assert [check.status for check in checks] == [
        "valid_target", "missing_target", "out_of_context", "wrong_source", "wrong_locator"
    ]


def test_source_cards_cannot_be_inferred_as_answer_links():
    checks = validate_citation_targets([], context_chunk_ids={"k1"},
                                       known_targets={"k1": CitationTarget("k1", "s1")})
    assert checks == ()
