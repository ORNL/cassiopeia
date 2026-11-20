# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""The synthesis follows the size of the library, not the papers one search added."""
from utils.synthesis_size import n_abstracts_for, n_proposals_for


def test_proposals_grow_with_the_library_and_cap_at_ten():
    assert [n_proposals_for(n) for n in (0, 49, 50, 100, 360, 400, 5000)] == [2, 2, 3, 4, 9, 10, 10]


def test_each_proposal_gets_its_own_abstracts_within_bounds():
    assert [n_abstracts_for(p) for p in (2, 3, 4, 9, 10)] == [12, 12, 16, 36, 40]


def test_a_search_passes_the_library_size_to_the_synthesis():
    """Guard against going back to sizing on the search's new papers."""
    import inspect

    import api_server

    source = inspect.getsource(api_server.search)
    assert "n_proposals_for(len(papers))" in source
    assert "n_papers=n_abstracts_for(n_proposals)" in inspect.getsource(api_server._run_rag_synthesis)
