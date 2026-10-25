"""Batch APIs, which bill half the synchronous price: one client per paying key.

Each turns a batch's requests into the provider's batch, reports when it has
ended, and reads its results back as provider-neutral answers.
"""

import json
import logging
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, ClassVar

import anthropic
import httpx
import httpx2
import openai
from openai.types.responses import ResponseUsage

from memory.common.llms.anthropic_provider import AnthropicProvider
from memory.common.llms.base import BaseLLMProvider
from memory.common.llms.neutral import AgentUsage, Completion
from memory.common.llms.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

# Anthropic keeps results for 29 days after the batch is created; OpenAI's output
# files are set to expire after 30.
RESULTS_RETENTION = timedelta(days=29)
OPENAI_OUTPUT_EXPIRY = int(timedelta(days=30).total_seconds())
OPENAI_ENDPOINT = "/v1/responses"
# OpenAI's codes for the requests an expired or cancelled batch never ran
OPENAI_UNRUN = {"batch_expired": "expired", "batch_cancelled": "canceled"}
OPENAI_ENDED = {"completed", "expired", "cancelled", "failed"}
# The project's cap on tokens enqueued for the model, across its batches
OPENAI_QUEUE_LIMIT = "token_limit_exceeded"
OPENAI_NO_MODEL = "model_not_found"


@dataclass(frozen=True)
class BatchState:
    ended: bool
    created_at: datetime
    # Refused for the provider's queue limit: none of its requests ran
    over_queue_limit: bool = False
    # Refused because the model doesn't exist, or the key can't see it
    model_unavailable: bool = False


@dataclass(frozen=True)
class BatchResult:
    custom_id: str
    # "errored", "expired" or "canceled" when the request got no answer
    failure: str | None = None
    # The provider says the model doesn't exist, or the key can't see it
    model_unavailable: bool = False
    usage: AgentUsage = field(default_factory=AgentUsage)
    # Builds the answer at its charged cost; it can raise on an answer the
    # provider billed but that can't be read
    completion: Callable[[Decimal], Completion] | None = None


class BatchAPI[P: BaseLLMProvider](ABC):
    provider: P
    KEY_REJECTED: ClassVar[tuple[type[Exception], ...]]
    # Failures that say nothing about the requests, so they don't use up an attempt
    TRANSIENT: ClassVar[tuple[type[Exception], ...]]
    # The provider refused the batch; its `message` says why
    REFUSED: ClassVar[type[anthropic.APIError] | type[openai.APIError]]
    # Refused because the key may not use the batch API
    FORBIDDEN: ClassVar[tuple[type[Exception], ...]]
    NOT_FOUND: ClassVar[tuple[type[Exception], ...]]
    # Hold a key's next batch until its last one ended
    ONE_AT_A_TIME: ClassVar[bool] = False
    # Requests a batch may hold; None leaves it to SUMMARIZER_BATCH_MAX_REQUESTS
    MAX_REQUESTS: ClassVar[int | None] = None

    @abstractmethod
    def __init__(self, api_key: str, model: str): ...

    @abstractmethod
    def create(self, requests: Iterable[tuple[str, dict[str, Any]]]) -> str:
        """Send (custom id, `acomplete` arguments) pairs as one batch; return its id."""

    @abstractmethod
    def state(self, batch_id: str) -> BatchState: ...

    @abstractmethod
    def results(self, batch_id: str) -> Iterator[BatchResult]:
        """The ended batch's results; requests it has none for got none."""

    def finish(self, batch_id: str) -> None:
        """Called once the batch's results are applied."""

    @abstractmethod
    def cancel(self, batch_id: str) -> None: ...


class AnthropicBatches(BatchAPI[AnthropicProvider]):
    KEY_REJECTED = (anthropic.AuthenticationError,)
    TRANSIENT = (
        anthropic.APIConnectionError,
        anthropic.RateLimitError,
        anthropic.InternalServerError,
        httpx2.RequestError,
        httpx.RequestError,
    )
    REFUSED = anthropic.APIError
    FORBIDDEN = (anthropic.PermissionDeniedError,)
    NOT_FOUND = (anthropic.NotFoundError,)

    def __init__(self, api_key: str, model: str):
        self.provider = AnthropicProvider(api_key=api_key, model=model)
        self.client = anthropic_client(api_key)

    def create(self, requests: Iterable[tuple[str, dict[str, Any]]]) -> str:
        payload = [
            {
                "custom_id": custom_id,
                "params": self.provider.build_agent_request(**args),
            }
            for custom_id, args in requests
        ]
        return self.client.messages.batches.create(requests=payload).id  # type: ignore[arg-type]

    def cancel(self, batch_id: str) -> None:
        self.client.messages.batches.cancel(batch_id)

    def state(self, batch_id: str) -> BatchState:
        batch = self.client.messages.batches.retrieve(batch_id)
        return BatchState(
            ended=batch.processing_status == "ended", created_at=batch.created_at
        )

    def results(self, batch_id: str) -> Iterator[BatchResult]:
        for result in self.client.messages.batches.results(batch_id):
            if result.result.type == "errored":
                missing = result.result.error.error.type == "not_found_error"
                yield BatchResult(
                    result.custom_id, failure="errored", model_unavailable=missing
                )
                continue
            if result.result.type != "succeeded":
                yield BatchResult(result.custom_id, failure=result.result.type)
                continue
            message = result.result.message
            usage = AnthropicProvider.agent_usage(message)
            yield BatchResult(
                result.custom_id,
                usage=usage,
                completion=lambda cost, message=message, usage=usage: (
                    self.provider.agent_completion(
                        [b.to_dict() for b in message.content],
                        message.stop_reason or "",
                        usage,
                        cost,
                    )
                ),
            )


class OpenAIBatches(BatchAPI[OpenAIProvider]):
    """Requests go up as a JSONL file; results come back as output and error files."""

    KEY_REJECTED = (openai.AuthenticationError,)
    TRANSIENT = (
        openai.APIConnectionError,
        openai.RateLimitError,
        openai.InternalServerError,
        httpx.RequestError,
    )
    REFUSED = openai.APIError
    FORBIDDEN = (openai.PermissionDeniedError,)
    NOT_FOUND = (openai.NotFoundError,)
    # Batches take hours: the queue limit counts every batch in flight, and spend,
    # only known once a batch is collected, would outrun the key's daily pool
    ONE_AT_A_TIME = True
    # OpenAI's own cap: with one batch at a time, each should carry all it can
    MAX_REQUESTS = 50_000

    def __init__(self, api_key: str, model: str):
        self.provider = OpenAIProvider(api_key=api_key, model=model)
        self.client = openai_client(api_key)
        self.batches: dict[str, Any] = {}

    def create(self, requests: Iterable[tuple[str, dict[str, Any]]]) -> str:
        # Written and uploaded a line at a time: a full batch is ~140MB of JSONL,
        # several times that as Python strings (the worker's /tmp is tmpfs: RAM, but as bytes)
        with tempfile.TemporaryFile() as upload:
            for custom_id, args in requests:
                line = {
                    "custom_id": custom_id,
                    "method": "POST",
                    "url": OPENAI_ENDPOINT,
                    "body": self.provider.build_agent_request(**args),
                }
                # Escaped non-ASCII would grow the file past the byte budget
                upload.write(json.dumps(line, ensure_ascii=False).encode() + b"\n")
            upload.seek(0)
            file = self.client.files.create(
                file=("summaries.jsonl", upload), purpose="batch"
            )
        try:
            batch = self.client.batches.create(
                input_file_id=file.id,
                endpoint=OPENAI_ENDPOINT,
                completion_window="24h",
                output_expires_after={
                    "anchor": "created_at",
                    "seconds": OPENAI_OUTPUT_EXPIRY,
                },
            )
        except Exception:
            self.delete_files(file.id)
            raise
        return batch.id

    def cancel(self, batch_id: str) -> None:
        self.client.batches.cancel(batch_id)

    def state(self, batch_id: str) -> BatchState:
        batch = self.batches[batch_id] = self.client.batches.retrieve(batch_id)
        failed, codes = batch.status == "failed", self.error_codes(batch)
        return BatchState(
            ended=batch.status in OPENAI_ENDED,
            created_at=datetime.fromtimestamp(batch.created_at, timezone.utc),
            over_queue_limit=failed and OPENAI_QUEUE_LIMIT in codes,
            model_unavailable=failed and OPENAI_NO_MODEL in codes,
        )

    @staticmethod
    def error_codes(batch: Any) -> list[str | None]:
        return [e.code for e in (batch.errors.data or [])] if batch.errors else []

    def results(self, batch_id: str) -> Iterator[BatchResult]:
        batch = self.batches[batch_id]
        if batch.status == "failed":
            logger.warning(
                "openai batch %s failed validation: %s", batch_id, self.error_codes(batch)
            )
        for file_id in filter(None, (batch.output_file_id, batch.error_file_id)):
            for line in self.lines(file_id):
                if result := self.parse(batch_id, line):
                    yield result

    def lines(self, file_id: str) -> list[str]:
        try:
            text = self.client.files.content(file_id).text
        # A concurrent collector that finished first deleted it
        except openai.NotFoundError:
            return []
        return [line for line in text.splitlines() if line.strip()]

    def parse(self, batch_id: str, line: str) -> BatchResult | None:
        """The line's result; an unreadable one is retried, and one naming no
        request is skipped, so its request is requeued as missing."""
        try:
            data = json.loads(line)
            custom_id = str(data["custom_id"])
        except (ValueError, KeyError, TypeError):
            logger.warning("openai batch %s has a line naming no request", batch_id)
            return None
        try:
            return self.result(data)
        except (KeyError, TypeError, ValueError, AttributeError):
            logger.warning("openai batch %s: unreadable result for request %s", batch_id, custom_id)
            return BatchResult(custom_id, failure="errored")

    def result(self, line: dict[str, Any]) -> BatchResult:
        custom_id = line["custom_id"]
        response = line.get("response") or {}
        status_code = response.get("status_code")
        if status_code != 200:
            code = (line.get("error") or {}).get("code")
            return BatchResult(
                custom_id,
                failure=OPENAI_UNRUN.get(code or "", "errored"),
                model_unavailable=status_code == 404,
            )
        body = response["body"]
        items = body.get("output") or []
        details = body.get("incomplete_details") or {}
        error = body.get("error")
        if not body.get("usage"):
            return BatchResult(custom_id, completion=no_usage)
        usage = OpenAIProvider.agent_usage(ResponseUsage.construct(**body["usage"]), items)
        return BatchResult(
            custom_id,
            usage=usage,
            completion=lambda cost: self.provider.agent_completion(
                items,
                body.get("status") or "",
                details.get("reason"),
                usage,
                cost,
                f"{error.get('code')}: {error.get('message')}" if error else None,
            ),
        )

    def finish(self, batch_id: str) -> None:
        batch = self.batches[batch_id]
        self.delete_files(
            batch.input_file_id, batch.output_file_id, batch.error_file_id
        )

    def delete_files(self, *file_ids: str | None) -> None:
        # Left behind, a file expires on its own: batch input after 30 days, output as set
        for file_id in filter(None, file_ids):
            try:
                self.client.files.delete(file_id)
            except (openai.APIError, httpx.RequestError) as e:
                logger.warning(
                    "openai batch file %s not deleted: %s", file_id, type(e).__name__
                )


def no_usage(cost: Decimal) -> Completion:
    raise ValueError("openai returned a response without usage")


def anthropic_client(api_key: str) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key)


def openai_client(api_key: str) -> openai.OpenAI:
    return openai.OpenAI(api_key=api_key)


# Failures of any provider's batch API that may pass by themselves
TRANSIENT_ERRORS = AnthropicBatches.TRANSIENT + OpenAIBatches.TRANSIENT

BATCH_APIS: dict[str, type[BatchAPI[Any]]] = {
    AnthropicProvider.provider: AnthropicBatches,
    OpenAIProvider.provider: OpenAIBatches,
}
