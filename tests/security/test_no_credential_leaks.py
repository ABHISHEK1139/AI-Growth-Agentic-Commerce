"""NFR-3 / NFR-9: credentials must not escape the process.

These assertions use realistically-shaped fake credentials. A real key must never
appear in this file, or in any tracked file other than as an empty placeholder in
`.env.example`.

agentpay:allow-credential-shapes - this module defines the detection patterns and
the structurally-valid-but-worthless fixtures they are tested against.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from packages.observability.logging import REDACTED, redact

REPO_ROOT = Path(__file__).resolve().parents[2]

# Shapes only. Structurally identical to the real thing, cryptographically worthless.
FAKE_GROQ_KEY = "gsk_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8S9t0"
FAKE_RAZORPAY_KEY_ID = "rzp_test_" + "A1b2C3d4E5f6G7"
FAKE_RAZORPAY_SECRET = "S9t8R7q6P5o4N3m2L1k0"

pytestmark = pytest.mark.security


class TestRedactionCoversRealCredentialShapes:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("model_api_key", FAKE_GROQ_KEY),
            ("razorpay_key_id", FAKE_RAZORPAY_KEY_ID),
            ("razorpay_key_secret", FAKE_RAZORPAY_SECRET),
        ],
    )
    def test_masked_when_the_key_name_is_sensitive(self, field: str, value: str) -> None:
        assert redact({field: value})[field] == REDACTED

    @pytest.mark.parametrize("value", [FAKE_GROQ_KEY, FAKE_RAZORPAY_KEY_ID])
    def test_masked_even_under_an_innocent_key_name(self, value: str) -> None:
        """The realistic leak is a debug line, not a field literally named
        `api_key`. Value-shape detection is what catches that."""
        result = redact({"note": f"calling provider with {value} now"})

        assert value not in result["note"]
        assert REDACTED in result["note"]

    def test_masked_inside_a_nested_header_dict(self) -> None:
        payload = {"request": {"headers": {"Authorization": f"Bearer {FAKE_GROQ_KEY}"}}}

        assert FAKE_GROQ_KEY not in repr(redact(payload))

    def test_masked_inside_a_list_of_attempts(self) -> None:
        payload = {"attempts": [{"api_key": FAKE_GROQ_KEY}, {"api_key": FAKE_GROQ_KEY}]}
        result = redact(payload)

        assert all(attempt["api_key"] == REDACTED for attempt in result["attempts"])


class TestTrackedFilesCarryNoCredentials:
    """A repository-wide sweep. This is the check that would have caught a key
    pasted into a config file, a notebook, or a README example."""

    #: Live-credential shapes. `rzp_test_` is included because even a test-mode
    #: key belongs in an untracked .env, not in version control.
    FORBIDDEN = (
        re.compile(r"\bgsk_[A-Za-z0-9]{20,}"),
        re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
        re.compile(r"\brzp_live_[A-Za-z0-9]{8,}"),
    )

    SCANNED_SUFFIXES = frozenset(
        {
            ".py",
            ".toml",
            ".yml",
            ".yaml",
            ".md",
            ".json",
            ".ini",
            ".cfg",
            ".env",
            ".txt",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".sh",
            ".example",
            ".Dockerfile",
            "",
        }
    )

    SKIPPED_DIRS = frozenset(
        {
            ".venv",
            ".git",
            "node_modules",
            "__pycache__",
            ".next",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "data",
        }
    )

    #: A file may opt out by declaring this sentinel, which keeps the exemption
    #: list explicit, greppable, and adjacent to the fixtures that need it —
    #: rather than a filename allowlist that silently rots.
    OPT_OUT_SENTINEL = "agentpay:allow-credential-shapes"

    def _candidate_files(self) -> list[Path]:
        files: list[Path] = []
        for path in REPO_ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in self.SKIPPED_DIRS for part in path.parts):
                continue
            # `.env` is untracked by design; this test asserts what git would carry.
            is_env_file = path.name == ".env" or path.name.startswith(".env.")
            if is_env_file and path.name != ".env.example":
                continue
            if path.suffix not in self.SCANNED_SUFFIXES:
                continue
            files.append(path)
        return files

    def test_no_tracked_file_contains_a_live_credential(self) -> None:
        offenders: list[str] = []
        for path in self._candidate_files():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if self.OPT_OUT_SENTINEL in text:
                continue
            for pattern in self.FORBIDDEN:
                if pattern.search(text):
                    offenders.append(f"{path.relative_to(REPO_ROOT)} matched {pattern.pattern}")

        assert not offenders, "Credential-shaped strings found in tracked files: " + "; ".join(
            offenders
        )

    def test_env_example_ships_empty_credential_placeholders(self) -> None:
        """The template must be committable, which means every secret is blank."""
        lines = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        must_be_empty = {
            "MODEL_API_KEY",
            "RAZORPAY_KEY_ID",
            "RAZORPAY_KEY_SECRET",
            "RAZORPAY_WEBHOOK_SECRET",
            "OBJECT_STORAGE_ACCESS_KEY",
            "OBJECT_STORAGE_SECRET_KEY",
        }
        seen: set[str] = set()
        for line in lines:
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            name, _, value = line.partition("=")
            name = name.strip()
            if name in must_be_empty:
                seen.add(name)
                assert value.strip() == "", f"{name} must be empty in .env.example"

        assert seen == must_be_empty, f"missing from .env.example: {must_be_empty - seen}"

    def test_gitignore_excludes_env_but_keeps_the_template(self) -> None:
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

        assert "\n.env\n" in gitignore
        assert "!.env.example" in gitignore
