"""Optional native startup guards; no model downloads or inference."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from research_agent.hub_cli import prepare_service_dependencies


def test_lexical_service_does_not_require_optional_native_dependencies():
    for semantic, postgres in ((None, None), ({"engine": "ollama"}, None),
                               (None, SimpleNamespace(stage="lexical"))):
        hub = SimpleNamespace(connection=lambda: nullcontext(None), _postgres=postgres)
        with patch("research_agent.hub_semantic.configuration", return_value=semantic), \
                patch("research_agent.hub_cli.importlib.import_module") as load:
            prepare_service_dependencies(hub)
        load.assert_not_called()


def test_e5_and_cross_encoder_services_preload_libraries_without_models():
    for semantic, postgres in (({"engine": "e5"}, None),
                               ({"engine": "e5"}, SimpleNamespace(stage="rrf")),
                               ({"engine": "e5"}, SimpleNamespace(stage="lexical")),
                               (None, SimpleNamespace(stage="rerank"))):
        hub = SimpleNamespace(connection=lambda: nullcontext(None), _postgres=postgres)
        with patch("research_agent.hub_semantic.configuration", return_value=semantic), \
                patch("research_agent.hub_cli.importlib.import_module") as load:
            prepare_service_dependencies(hub)
        assert [call.args[0] for call in load.call_args_list] == [
            "numpy", "onnxruntime", "tokenizers"]


def test_missing_native_extra_keeps_query_fallback_without_exposing_errors(capsys):
    hub = SimpleNamespace(connection=lambda: nullcontext(None), _postgres=None)
    with patch("research_agent.hub_semantic.configuration", return_value={"engine": "e5"}), \
            patch("research_agent.hub_cli.importlib.import_module",
                  side_effect=ImportError("private-provider-error")):
        prepare_service_dependencies(hub)
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "semantic_native_preload_unavailable\n"
