"""Local request budgets, not an estimate of a provider's context window."""
from dataclasses import dataclass
from .llm_conversation import canonical_json


class InferenceBudgetExceeded(RuntimeError):
    pass


@dataclass
class InferenceBudget:
    max_message_bytes: int = 300000
    max_calls: int = 30
    attempted_calls: int = 0
    max_total_message_bytes: int | None = None
    total_message_bytes: int = 0

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in (self.max_message_bytes, self.max_calls)):
            raise ValueError('budgets must be positive integers')
        if self.max_total_message_bytes is not None and (type(self.max_total_message_bytes) is not int or self.max_total_message_bytes < 1):
            raise ValueError('cumulative message budget must be a positive integer')

    def complete(self, client, messages):
        size = len(canonical_json(messages).encode('utf-8'))
        if size > self.max_message_bytes:
            raise InferenceBudgetExceeded(f'message_bytes={size} exceeds limit={self.max_message_bytes}; history retained, no request sent')
        if self.attempted_calls >= self.max_calls:
            raise InferenceBudgetExceeded('call budget exhausted; no request sent')
        if self.max_total_message_bytes is not None and self.total_message_bytes + size > self.max_total_message_bytes:
            raise InferenceBudgetExceeded('cumulative message budget exhausted; no request sent')
        if getattr(client, 'retries', 0) != 0:
            raise ValueError('budgeted client must disable implicit retries')
        self.attempted_calls += 1
        self.total_message_bytes += size
        return client.complete(messages)
