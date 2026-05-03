"""
JournalLearningStore

Responsibility: Persist every pipeline cycle's decision and fill as a
structured record for later analysis.

v1 storage: append-only JSONL file at data/journal.jsonl.
One JSON line per JournalEntry. Each line is a valid JSON object.

No ML, no inference, no autonomous optimization.
This is a data foundation — analysis happens externally (notebooks, scripts).

Future: migrate to SQLite when queries become necessary.
"""

from pathlib import Path

from ..config import BITConfig
from ..domain.journal import JournalEntry


class JournalLearningStore:
    DEFAULT_PATH = Path("data/journal.jsonl")

    def __init__(
        self,
        config: BITConfig,
        journal_path: Path | None = None,
    ) -> None:
        self._config = config
        self._path = journal_path or self.DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, entry: JournalEntry) -> None:
        """Append one journal entry to the JSONL file. Thread-safe for single-process use."""
        with self._path.open("a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def read_all(self) -> list[JournalEntry]:
        """Read and deserialize all entries from the journal file."""
        if not self._path.exists():
            return []
        entries: list[JournalEntry] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(JournalEntry.model_validate_json(line))
                except Exception as exc:
                    # Log and skip malformed lines rather than aborting.
                    # TODO: replace print with proper logging.
                    print(f"WARNING: Skipping malformed journal line {line_num}: {exc}")
        return entries

    def entry_count(self) -> int:
        """Return the number of recorded entries."""
        return len(self.read_all())

    @property
    def path(self) -> Path:
        """Path to the JSONL journal file."""
        return self._path
