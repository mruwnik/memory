"""Google Drive API client for fetching and exporting documents."""

import hashlib
import io
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generator, TypedDict

import defusedxml.ElementTree as DefusedElementTree

from memory.common import settings

logger = logging.getLogger(__name__)

# MIME types we support
SUPPORTED_GOOGLE_MIMES = {
    "application/vnd.google-apps.document",  # Google Docs
    "application/vnd.google-apps.spreadsheet",  # Google Sheets
    "application/vnd.google-apps.presentation",  # Google Slides
    "application/vnd.google-apps.drawing",  # Google Drawings
}

SUPPORTED_FILE_MIMES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "text/plain",
    "text/markdown",
    "text/html",
    "text/csv",
}

# Export mappings for Google native formats
# These formats can't be downloaded directly - must use export
EXPORT_MIMES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.drawing": "application/pdf",
}


@dataclass
class GoogleCredentials:
    """Credentials for Google Drive API access."""

    access_token: str
    refresh_token: str | None
    token_expires_at: datetime | None
    scopes: list[str]


class GoogleFileData(TypedDict):
    """Parsed file data ready for storage."""

    file_id: str
    title: str
    mime_type: str
    original_mime_type: str
    folder_path: str | None
    owner: str | None
    last_modified_by: str | None
    shared_with: list[str]  # Email addresses of users the doc is shared with
    modified_at: datetime | None
    created_at: datetime | None
    content: str
    content_hash: str
    size: int
    word_count: int


def parse_google_date(date_str: str | None) -> datetime | None:
    """Parse RFC 3339 date string from Google API."""
    if not date_str:
        return None
    return datetime.fromisoformat(date_str.replace("Z", "+00:00"))


def compute_content_hash(content: str) -> str:
    """Compute SHA256 hash of content for change detection."""
    return hashlib.sha256(content.encode()).hexdigest()


class GoogleDriveClient:
    """Client for Google Drive API."""

    def __init__(
        self,
        credentials: GoogleCredentials,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_uri: str = "https://oauth2.googleapis.com/token",
    ):
        self.credentials = credentials
        self._service = None
        # Use provided values or fall back to settings
        self._client_id = client_id or settings.GOOGLE_CLIENT_ID
        self._client_secret = client_secret or settings.GOOGLE_CLIENT_SECRET
        self._token_uri = token_uri

    def _get_service(self):
        """Lazily build the Drive service."""
        if self._service is None:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials(
                token=self.credentials.access_token,
                refresh_token=self.credentials.refresh_token,
                token_uri=self._token_uri,
                client_id=self._client_id,
                client_secret=self._client_secret,
                scopes=self.credentials.scopes,
            )
            self._service = build("drive", "v3", credentials=creds)
        return self._service

    def get_file_metadata(self, file_id: str) -> dict:
        """Get metadata for a single file or folder."""
        service = self._get_service()
        return (
            service.files()
            .get(
                fileId=file_id,
                fields="id, name, mimeType, modifiedTime, createdTime, owners, lastModifyingUser, parents, size, permissions(emailAddress, type)",
                supportsAllDrives=True,
            )
            .execute()
        )

    def is_folder(self, file_id: str) -> bool:
        """Check if a file ID refers to a folder."""
        metadata = self.get_file_metadata(file_id)
        return metadata.get("mimeType") == "application/vnd.google-apps.folder"

    # Maximum folder depth to prevent stack overflow on deep/circular structures
    MAX_FOLDER_DEPTH = 50

    def list_files_in_folder(
        self,
        folder_id: str,
        recursive: bool = True,
        since: datetime | None = None,
        page_size: int = 100,
        exclude_folder_ids: set[str] | None = None,
        _current_path: str | None = None,
        _depth: int = 0,
    ) -> Generator[tuple[dict, str], None, None]:
        """List all supported files in a folder with pagination.

        Args:
            folder_id: The Google Drive folder ID to list
            recursive: Whether to recurse into subfolders
            since: Only return files modified after this time
            page_size: Number of files per API page
            exclude_folder_ids: Set of folder IDs to skip during recursive traversal
            _current_path: Internal param tracking the current folder path
            _depth: Internal param tracking recursion depth

        Yields:
            Tuples of (file_metadata, parent_folder_path)
        """
        # Prevent stack overflow on deep/circular folder structures
        if _depth >= self.MAX_FOLDER_DEPTH:
            logger.warning(
                f"Max folder depth ({self.MAX_FOLDER_DEPTH}) exceeded at {_current_path}, "
                f"skipping deeper traversal"
            )
            return

        service = self._get_service()
        exclude_folder_ids = exclude_folder_ids or set()

        # Build the current path if not provided
        if _current_path is None:
            _current_path = self.get_folder_path(folder_id)

        # Build query for supported file types
        all_mimes = SUPPORTED_GOOGLE_MIMES | SUPPORTED_FILE_MIMES
        mime_conditions = " or ".join(f"mimeType='{mime}'" for mime in all_mimes)

        query_parts = [
            f"'{folder_id}' in parents",
            "trashed=false",
            f"({mime_conditions} or mimeType='application/vnd.google-apps.folder')",
        ]

        if since:
            query_parts.append(f"modifiedTime > '{since.isoformat()}'")

        query = " and ".join(query_parts)

        page_token = None
        while True:
            response = (
                service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime, createdTime, owners, lastModifyingUser, parents, size, permissions(emailAddress, type))",
                    pageToken=page_token,
                    pageSize=page_size,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )

            for file in response.get("files", []):
                if file["mimeType"] == "application/vnd.google-apps.folder":
                    if recursive and file["id"] not in exclude_folder_ids:
                        # Recursively list files in subfolder with updated path
                        subfolder_path = f"{_current_path}/{file['name']}"
                        yield from self.list_files_in_folder(
                            file["id"],
                            recursive=True,
                            since=since,
                            exclude_folder_ids=exclude_folder_ids,
                            _current_path=subfolder_path,
                            _depth=_depth + 1,
                        )
                    elif file["id"] in exclude_folder_ids:
                        logger.info(f"Skipping excluded folder: {file['name']} ({file['id']})")
                else:
                    yield file, _current_path

            page_token = response.get("nextPageToken")
            if not page_token:
                break

    def export_file(self, file_id: str, mime_type: str) -> bytes:
        """Export a Google native file to the specified format."""
        from googleapiclient.http import MediaIoBaseDownload

        service = self._get_service()

        if mime_type in SUPPORTED_GOOGLE_MIMES:
            # Export Google Docs to text
            export_mime = EXPORT_MIMES.get(mime_type, "text/plain")
            request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        else:
            # Download regular files directly
            request = service.files().get_media(fileId=file_id)

        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()

        return buffer.getvalue()

    def get_folder_path(self, file_id: str) -> str:
        """Build the full folder path for a file."""
        service = self._get_service()
        path_parts = []

        current_id = file_id
        while current_id:
            try:
                file = (
                    service.files().get(
                        fileId=current_id,
                        fields="name, parents",
                        supportsAllDrives=True,
                    ).execute()
                )
                path_parts.insert(0, file["name"])
                parents = file.get("parents", [])
                current_id = parents[0] if parents else None
            except Exception:
                break

        return "/".join(path_parts)

    def fetch_file(
        self, file_metadata: dict, folder_path: str | None = None
    ) -> GoogleFileData | None:
        """Fetch and parse a single file."""
        file_id = file_metadata["id"]
        mime_type = file_metadata["mimeType"]

        logger.info(f"Fetching file: {file_metadata['name']} ({mime_type})")

        # Download/export content
        content_bytes = self.export_file(file_id, mime_type)

        # Determine the actual format we got (for Google native types, it's the export format)
        exported_mime = EXPORT_MIMES.get(mime_type, mime_type)

        # Handle encoding for text formats
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            content = content_bytes.decode("latin-1")

        # For PDFs and Word docs, we need to extract text
        # Check both original and exported MIME types
        if mime_type == "application/pdf" or exported_mime == "application/pdf":
            extracted = self._extract_pdf_text(content_bytes)
            if extracted is None:
                logger.warning(
                    f"Failed to extract text from PDF: {file_metadata['name']} ({file_id})"
                )
                return None
            content = extracted
        elif mime_type in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        ):
            content = self._extract_docx_text(content_bytes)

        # Extract owner info
        owners = file_metadata.get("owners", [])
        owner = owners[0].get("emailAddress") if owners else None

        last_modifier = file_metadata.get("lastModifyingUser", {})
        last_modified_by = last_modifier.get("emailAddress")

        # Extract shared user emails from permissions (exclude non-user types like "anyone")
        # Note: Reading permissions requires appropriate Drive API scopes. If permissions
        # are empty but the file has an owner, the API credentials may lack access.
        permissions = file_metadata.get("permissions", [])
        shared_with = [
            p.get("emailAddress")
            for p in permissions
            if p.get("type") == "user" and p.get("emailAddress")
        ]
        if not permissions and owner:
            logger.warning(
                "No permissions returned for file %s (owner: %s). "
                "API credentials may lack permissions scope - shared_with will be empty.",
                file_id,
                owner,
            )

        return GoogleFileData(
            file_id=file_id,
            title=file_metadata["name"],
            mime_type=EXPORT_MIMES.get(mime_type, mime_type),
            original_mime_type=mime_type,
            folder_path=folder_path,
            owner=owner,
            last_modified_by=last_modified_by,
            shared_with=shared_with,
            modified_at=parse_google_date(file_metadata.get("modifiedTime")),
            created_at=parse_google_date(file_metadata.get("createdTime")),
            content=content,
            content_hash=compute_content_hash(content),
            size=len(content.encode("utf-8")),
            word_count=len(content.split()),
        )

    def _extract_pdf_text(self, pdf_bytes: bytes) -> str | None:
        """Extract text from PDF using PyMuPDF.

        Returns None on failure so callers can distinguish empty PDFs from errors.
        """
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            text_parts = []
            for page in doc:
                text_parts.append(page.get_text())  # type: ignore[union-attr]
            doc.close()
            return "\n\n".join(text_parts)
        except Exception as e:
            logger.error(f"Failed to extract PDF text: {e}")
            return None

    def _extract_docx_text(self, docx_bytes: bytes) -> str:
        """Extract text from Word document."""
        try:
            # Using defusedxml to prevent XXE attacks from untrusted DOCX files
            with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
                with zf.open("word/document.xml") as doc_xml:
                    tree = DefusedElementTree.parse(doc_xml)
                    root = tree.getroot()
                    if root is None:
                        logger.warning("DOCX document.xml has no root element")
                        return ""

                    # Word uses namespaces
                    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

                    text_parts = []
                    for para in root.findall(".//w:p", ns):
                        para_text = "".join(
                            node.text or ""
                            for node in para.findall(".//w:t", ns)
                        )
                        if para_text:
                            text_parts.append(para_text)

                    return "\n\n".join(text_parts)
        except Exception as e:
            logger.warning(f"Failed to extract docx text: {e}")
            return ""


def _get_oauth_config(session: Any) -> tuple[str, str, str]:
    """Get OAuth client credentials from database or settings."""
    from memory.common.db.models.sources import GoogleOAuthConfig

    config = session.query(GoogleOAuthConfig).filter(GoogleOAuthConfig.name == "default").first()
    if config:
        return config.client_id, config.client_secret, config.token_uri

    # Fall back to environment variables
    return (
        settings.GOOGLE_CLIENT_ID,
        settings.GOOGLE_CLIENT_SECRET,
        "https://oauth2.googleapis.com/token",
    )


def refresh_credentials(account: Any, session: Any) -> Any:
    """Refresh OAuth2 credentials if expired."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    client_id, client_secret, token_uri = _get_oauth_config(session)

    credentials = Credentials(
        token=account.access_token,
        refresh_token=account.refresh_token,
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=account.scopes or [],
    )

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

        # Update stored tokens
        account.access_token = credentials.token
        account.token_expires_at = credentials.expiry
        session.commit()

    return credentials
