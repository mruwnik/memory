import base64
from dataclasses import dataclass, field
from typing import Any, Callable

import httplib2
from googleapiclient.errors import HttpError


@dataclass
class FakeRequest:
    run: Callable[[], dict]

    def execute(self) -> dict:
        return self.run()


@dataclass
class FakeBatch:
    callback: Callable[[str, Any, Exception | None], None]
    sizes: list[int]
    requests: list[tuple[str, FakeRequest]] = field(default_factory=list)

    def add(self, request: FakeRequest, request_id: str) -> None:
        self.requests.append((request_id, request))

    def execute(self) -> None:
        self.sizes.append(len(self.requests))
        for request_id, request in self.requests:
            try:
                self.callback(request_id, request.execute(), None)
            except HttpError as e:
                self.callback(request_id, None, e)


def http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"error")


class FakeGmailService:
    """In-memory Gmail API: ``mailbox`` maps id -> label ids; absent ids are purged.

    ``list`` follows Gmail's semantics: a message matches when it carries every
    requested label, and Spam/Trash are left out unless ``includeSpamTrash``.
    """

    def __init__(
        self,
        mailbox: dict[str, list[str]],
        raw: Callable[[str], str] = lambda msg_id: f"Message-ID: <{msg_id}@x>\n\nbody",
        errors: dict[str, int] | None = None,
    ):
        self.mailbox = mailbox
        self.raw = raw
        self.errors = errors or {}
        self.list_calls: list[dict] = []
        self.raw_fetches: list[str] = []
        self.batch_sizes: list[int] = []

    def users(self):
        return self

    def labels(self):
        return FakeLabels()

    def new_batch_http_request(self, callback):
        return FakeBatch(callback, self.batch_sizes)

    def list(self, userId, q=None, labelIds=None, maxResults=500, includeSpamTrash=False):
        self.list_calls.append({"labelIds": labelIds, "includeSpamTrash": includeSpamTrash})
        matching = [
            msg_id
            for msg_id, labels in self.mailbox.items()
            if set(labelIds or []) <= set(labels)
            and (includeSpamTrash or not {"SPAM", "TRASH"} & set(labels))
        ]
        return FakeRequest(lambda: {"messages": [{"id": m} for m in matching]})

    def list_next(self, request, response):
        return None

    def get(self, userId, id, format="full", fields=None):
        def run() -> dict:
            if id in self.errors:
                raise http_error(self.errors[id])
            if id not in self.mailbox:
                raise http_error(404)
            if format == "raw":
                self.raw_fetches.append(id)
                return {"id": id, "raw": base64.urlsafe_b64encode(self.raw(id).encode()).decode()}
            return {"id": id, "labelIds": self.mailbox[id]}

        return FakeRequest(run)

    def messages(self):
        return self


class FakeLabels:
    def list(self, userId):
        return FakeRequest(lambda: {"labels": []})
