# llm/pipeline/outcome.py
from dataclasses import dataclass, field


@dataclass
class ProcessingOutcome:
    """
    Returned by every pipeline entry point.

    Pipeline functions never write to the database. The task layer (tasks.py)
    reads this dataclass and writes all job state via _apply_outcome.
    """
    created:        list = field(default_factory=list)
    notes:          str  = ''
    status:         str  = 'done'   # 'done' | 'needs_review' | 'failed'
    failure_reason: str  = ''       # ScanJob.REASON_* constant or ''
