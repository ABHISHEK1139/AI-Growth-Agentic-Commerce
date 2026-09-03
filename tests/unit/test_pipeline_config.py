"""The pipeline reads its own configuration and never imports the API settings.

The import contract in ``pyproject.toml`` forbids ``pipeline -> apps.api``, so
these settings are duplicated deliberately. The tests below pin the two things
that duplication could silently get wrong: the defaults, and the meaning of an
empty ``MAX_LINES_DEBUG``.
"""

from __future__ import annotations

import pytest

from pipeline.config import (
    DEFAULT_OUT_DIR,
    DEFAULT_RAW_DIR,
    REPO_ROOT,
    PipelineConfig,
    load_config,
    read_env_file,
)


def test_defaults_match_the_application_settings():
    config = load_config(env={})

    assert config.raw_dir == DEFAULT_RAW_DIR
    assert config.out_dir == DEFAULT_OUT_DIR
    assert config.max_lines_debug is None
    assert config.candidates_db.name == "candidates.sqlite"
    assert config.candidates_db.parent == config.out_dir


def test_relative_directories_resolve_against_the_repository_root():
    config = load_config(env={"AGENTPAY_RAW_DIR": "../datasets", "AGENTPAY_OUT_DIR": "./data/out"})

    assert config.raw_dir == REPO_ROOT.parent / "datasets"
    assert config.out_dir == REPO_ROOT / "data" / "out"


def test_absolute_directories_are_honoured(tmp_path):
    config = load_config(env={"AGENTPAY_RAW_DIR": str(tmp_path)})

    assert config.raw_dir == tmp_path


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_cap_means_read_every_line(value):
    assert load_config(env={"MAX_LINES_DEBUG": value}).max_lines_debug is None


def test_cap_is_parsed_as_an_integer():
    assert load_config(env={"MAX_LINES_DEBUG": "1000"}).max_lines_debug == 1000


@pytest.mark.parametrize("value", ["nope", "0", "-5"])
def test_invalid_cap_fails_loudly(value):
    with pytest.raises(ValueError, match="MAX_LINES_DEBUG"):
        load_config(env={"MAX_LINES_DEBUG": value})


def test_env_file_parsing_ignores_comments_and_blank_lines(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "AGENTPAY_RAW_DIR=../datasets   # inline comment",
                'AGENTPAY_OUT_DIR="./data/out"',
                "MAX_LINES_DEBUG=250",
                "NOT_A_PAIR",
            ]
        ),
        encoding="utf-8",
    )

    values = read_env_file(env_file)

    assert values["AGENTPAY_RAW_DIR"] == "../datasets"
    assert values["AGENTPAY_OUT_DIR"] == "./data/out"
    assert values["MAX_LINES_DEBUG"] == "250"
    assert "NOT_A_PAIR" not in values


def test_missing_env_file_is_not_an_error(tmp_path):
    assert read_env_file(tmp_path / "absent.env") == {}


def test_config_is_frozen(tmp_path):
    config = PipelineConfig(raw_dir=tmp_path, out_dir=tmp_path / "out")

    with pytest.raises(Exception):  # noqa: B017 - dataclasses raise FrozenInstanceError
        config.max_lines_debug = 5  # type: ignore[misc]
