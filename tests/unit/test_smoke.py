"""Smoke test to verify that all modules import and configuration loads properly."""

from pathlib import Path


def test_import_all_modules() -> None:
    """Import every module in careercompass to ensure no syntax/import errors."""
    import careercompass
    import careercompass.config
    import careercompass.db
    import careercompass.graph
    import careercompass.llm
    import careercompass.parser
    import careercompass.planner
    import careercompass.retrieval
    import careercompass.run
    import careercompass.schemas
    import careercompass.scoring

    assert careercompass.__version__ == "0.1.0"
    assert careercompass.config is not None
    assert careercompass.db is not None
    assert careercompass.graph is not None
    assert careercompass.llm is not None
    assert careercompass.parser is not None
    assert careercompass.planner is not None
    assert careercompass.retrieval is not None
    assert careercompass.run is not None
    assert careercompass.schemas is not None
    assert careercompass.scoring is not None


def test_config_loads() -> None:
    """Assert configuration loads typed constants correctly from environment or defaults."""
    from careercompass import config

    assert isinstance(config.ONET_DIR, Path)
    assert isinstance(config.ONET_DB, Path)
    assert isinstance(config.ONET_RELEASE, str)
    assert isinstance(config.LLM_PROVIDER, str)
    assert isinstance(config.LLM_MODEL, str)

    assert len(config.ONET_RELEASE) > 0
    assert len(config.LLM_PROVIDER) > 0
    assert len(config.LLM_MODEL) > 0
