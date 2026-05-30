import email
from datetime import datetime
from typing import Any, List


class MockEmailProvider:
    """
    Mock IMAP email provider for integration testing.
    Can be initialized with predefined emails to return.
    """

    def __init__(self, emails_by_folder: dict[str, list[dict[str, Any]]] | None = None):
        """
        Initialize with a dictionary of emails organized by folder.

        Args:
            emails_by_folder: A dictionary mapping folder names to lists of email dictionaries.
                Each email dict should have: 'uid', 'flags', 'date', 'from', 'to', 'subject',
                'message_id', 'body', and optionally 'attachments'.
        """
        self.emails_by_folder = emails_by_folder or {
            "INBOX": [],
            "Sent": [],
            "Archive": [],
        }
        self.current_folder = None
        self.is_connected = False

    def _generate_email_string(self, email_data: dict[str, Any]) -> str:
        """Generate a raw email string from the provided email data."""
        msg = email.message.EmailMessage()  # type: ignore
        msg["From"] = email_data.get("from", "sender@example.com")
        msg["To"] = email_data.get("to", "recipient@example.com")
        msg["Subject"] = email_data.get("subject", "Test Subject")
        msg["Message-ID"] = email_data.get(
            "message_id", f"<test-{email_data['uid']}@example.com>"
        )
        msg["Date"] = email_data.get(
            "date", datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")
        )

        # Set the body content
        msg.set_content(
            email_data.get("body", f"This is test email body {email_data['uid']}")
        )

        # Add attachments if present
        for attachment in email_data.get("attachments", []):
            if (
                isinstance(attachment, dict)
                and "filename" in attachment
                and "content" in attachment
            ):
                msg.add_attachment(
                    attachment["content"],
                    maintype=attachment.get("maintype", "application"),
                    subtype=attachment.get("subtype", "octet-stream"),
                    filename=attachment["filename"],
                )

        return msg.as_string()

    def login(self, username: str, password: str) -> tuple[str, list[bytes]]:
        """Mock login method."""
        self.is_connected = True
        return ("OK", [b"Login successful"])

    def logout(self) -> tuple[str, list[bytes]]:
        """Mock logout method."""
        self.is_connected = False
        return ("OK", [b"Logout successful"])

    def select(self, folder: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        """
        Select a folder and make it the current active folder.

        Args:
            folder: Folder name to select
            readonly: Whether to open in readonly mode

        Returns:
            IMAP-style response with message count
        """
        folder_name = folder.decode() if isinstance(folder, bytes) else folder
        self.current_folder = folder_name
        message_count = len(self.emails_by_folder.get(folder_name, []))
        return ("OK", [str(message_count).encode()])

    def list(self, directory: str = "", pattern: str = "*") -> tuple[str, list[bytes]]:
        """List available folders."""
        folders = []
        for folder in self.emails_by_folder.keys():
            folders.append(f'(\\HasNoChildren) "/" "{folder}"'.encode())
        return ("OK", folders)

    def _current_emails(self) -> List[dict[str, Any]]:
        """Emails in the currently-selected folder (empty if none/unknown).

        Annotated with ``typing.List`` rather than ``list[...]`` because the IMAP
        ``list()`` method below shadows the builtin within this class body, so a
        bare ``list[...]`` annotation here would resolve to the method.
        """
        if not self.current_folder or self.current_folder not in self.emails_by_folder:
            return []
        return self.emails_by_folder[self.current_folder]

    def _fetch_response(self, email_data: dict[str, Any], seqno: int):
        """Build an IMAP FETCH response tuple for a single message.

        The leading token is the message *sequence number*; ``UID <uid>`` carries
        the real UID. This mirrors real IMAP, where the two are distinct and the
        UID must be read from the ``UID`` data item, not the leading token.
        """
        email_string = self._generate_email_string(email_data)
        uid = email_data["uid"]
        flags = email_data.get("flags", "\\Seen")
        date = email_data.get("date_internal", "01-Jan-2023 00:00:00 +0000")
        header = (
            f'{seqno} (UID {uid} FLAGS ({flags}) INTERNALDATE "{date}" RFC822 '
            f"{{{len(email_string)}}}"
        ).encode()
        return ("OK", [(header, email_string.encode())])

    def search(self, charset, *criteria):
        """Sequence-number SEARCH: returns 1-based message positions.

        Like ``imaplib.IMAP4.search``, this returns message *sequence numbers*,
        NOT UIDs. Criteria are ignored (the mock returns the whole folder).
        """
        emails = self._current_emails()
        seqs = [str(i + 1).encode() for i in range(len(emails))]
        return ("OK", [b" ".join(seqs) if seqs else b""])

    def uid(self, command: str, *args):
        """UID command dispatch (``SEARCH`` / ``FETCH``) keyed on real UIDs.

        Mirrors ``imaplib.IMAP4.uid``: ``SEARCH`` returns the messages' real
        UIDs; ``FETCH`` selects the message whose UID matches the argument.
        """
        cmd = command.upper()
        emails = self._current_emails()
        if cmd == "SEARCH":
            uids = [str(e["uid"]).encode() for e in emails]
            return ("OK", [b" ".join(uids) if uids else b""])
        if cmd == "FETCH":
            target = int(args[0].decode() if isinstance(args[0], bytes) else args[0])
            for seqno, email_data in enumerate(emails, start=1):
                if email_data["uid"] == target:
                    return self._fetch_response(email_data, seqno)
            return ("NO", [b"Email not found"])
        raise ValueError(f"Unsupported uid command: {command}")

    def fetch(self, message_set: bytes | str, message_parts: bytes | str):
        """Sequence-number FETCH: ``message_set`` is a 1-based position."""
        emails = self._current_emails()
        seq = int(
            message_set.decode() if isinstance(message_set, bytes) else message_set
        )
        if 1 <= seq <= len(emails):
            return self._fetch_response(emails[seq - 1], seq)
        return ("NO", [b"Email not found"])
