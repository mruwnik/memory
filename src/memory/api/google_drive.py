"""API endpoints for Google Drive configuration."""

import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import cast

# Allow Google to return additional scopes (like 'openid') without raising an error
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from memory.common import settings
from memory.common.db.connection import get_session, make_session
from memory.common.db.models import User
from memory.common.db.models.sources import GoogleAccount, GoogleFolder, GoogleOAuthConfig
from memory.api.auth import get_current_user

router = APIRouter(prefix="/google-drive", tags=["google-drive"])


def get_oauth_config(session: Session) -> GoogleOAuthConfig:
    """Get the OAuth config from database, falling back to env vars if not found."""
    config = session.query(GoogleOAuthConfig).filter(GoogleOAuthConfig.name == "default").first()
    if config:
        return config

    # Fall back to environment variables for backwards compatibility
    if settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET:
        return GoogleOAuthConfig(
            name="default",
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            redirect_uris=[settings.GOOGLE_REDIRECT_URI],
        )

    raise HTTPException(
        status_code=500,
        detail="Google OAuth not configured. Upload credentials JSON or set GOOGLE_CLIENT_ID/SECRET.",
    )


class FolderCreate(BaseModel):
    folder_id: str  # Google Drive folder ID
    folder_name: str
    recursive: bool = True
    include_shared: bool = False
    tags: list[str] = []
    check_interval: int = 60  # Minutes


class FolderUpdate(BaseModel):
    folder_name: str | None = None
    recursive: bool | None = None
    include_shared: bool | None = None
    tags: list[str] | None = None
    check_interval: int | None = None
    active: bool | None = None


class FolderResponse(BaseModel):
    id: int
    folder_id: str
    folder_name: str
    folder_path: str | None
    recursive: bool
    include_shared: bool
    tags: list[str]
    check_interval: int
    last_sync_at: str | None
    active: bool


class AccountResponse(BaseModel):
    id: int
    name: str
    email: str
    active: bool
    last_sync_at: str | None
    sync_error: str | None
    folders: list[FolderResponse]


# OAuth State storage (temporary, for OAuth flow)
class GoogleOAuthState:
    """In-memory OAuth state storage. In production, use the database."""

    _states: dict[str, int] = {}  # state -> user_id

    @classmethod
    def create(cls, user_id: int) -> str:
        state = secrets.token_urlsafe(32)
        cls._states[state] = user_id
        return state

    @classmethod
    def validate(cls, state: str) -> int | None:
        return cls._states.pop(state, None)


class OAuthConfigResponse(BaseModel):
    id: int
    name: str
    client_id: str
    project_id: str | None
    redirect_uris: list[str]
    created_at: str


# Browse endpoint models
class DriveItem(BaseModel):
    """A file or folder in Google Drive."""
    id: str
    name: str
    mime_type: str
    is_folder: bool
    size: int | None = None
    modified_at: str | None = None


class BrowseResponse(BaseModel):
    """Response from browsing a Google Drive folder."""
    folder_id: str
    folder_name: str
    parent_id: str | None = None
    items: list[DriveItem]
    next_page_token: str | None = None


@router.post("/config")
async def upload_oauth_config(
    file: UploadFile,
    name: str = "default",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> OAuthConfigResponse:
    """Upload Google OAuth credentials JSON file."""
    if not file.filename or not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="File must be a JSON file")

    try:
        content = await file.read()
        json_data = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON file: {e}")

    # Check if config already exists
    existing = db.query(GoogleOAuthConfig).filter(GoogleOAuthConfig.name == name).first()
    if existing:
        # Update existing config
        creds = json_data.get("web") or json_data.get("installed") or json_data
        existing.client_id = creds["client_id"]
        existing.client_secret = creds["client_secret"]
        existing.project_id = creds.get("project_id")
        existing.auth_uri = creds.get("auth_uri", "https://accounts.google.com/o/oauth2/auth")
        existing.token_uri = creds.get("token_uri", "https://oauth2.googleapis.com/token")
        existing.redirect_uris = creds.get("redirect_uris", [])
        existing.javascript_origins = creds.get("javascript_origins", [])
        db.commit()
        db.refresh(existing)
        config = existing
    else:
        # Create new config
        config = GoogleOAuthConfig.from_json(json_data, name=name)
        db.add(config)
        db.commit()
        db.refresh(config)

    return OAuthConfigResponse(
        id=cast(int, config.id),
        name=cast(str, config.name),
        client_id=cast(str, config.client_id),
        project_id=cast(str | None, config.project_id),
        redirect_uris=list(config.redirect_uris or []),
        created_at=config.created_at.isoformat() if config.created_at else "",
    )


@router.get("/config")
def get_config(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> OAuthConfigResponse | None:
    """Get current OAuth configuration (without secrets)."""
    config = db.query(GoogleOAuthConfig).filter(GoogleOAuthConfig.name == "default").first()
    if not config:
        return None

    return OAuthConfigResponse(
        id=cast(int, config.id),
        name=cast(str, config.name),
        client_id=cast(str, config.client_id),
        project_id=cast(str | None, config.project_id),
        redirect_uris=list(config.redirect_uris or []),
        created_at=config.created_at.isoformat() if config.created_at else "",
    )


@router.delete("/config")
def delete_config(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Delete OAuth configuration."""
    config = db.query(GoogleOAuthConfig).filter(GoogleOAuthConfig.name == "default").first()
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")

    db.delete(config)
    db.commit()
    return {"status": "deleted"}


@router.get("/authorize")
def google_authorize(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    """Initiate Google OAuth2 flow. Returns the authorization URL."""
    oauth_config = get_oauth_config(db)

    from google_auth_oauthlib.flow import Flow

    # Determine redirect URI - prefer one from config, fall back to settings
    redirect_uri = (
        oauth_config.redirect_uris[0]
        if oauth_config.redirect_uris
        else settings.GOOGLE_REDIRECT_URI
    )

    flow = Flow.from_client_config(
        oauth_config.to_client_config(),
        scopes=settings.GOOGLE_SCOPES,
        redirect_uri=redirect_uri,
    )

    # Generate state token with user ID
    state = GoogleOAuthState.create(user.id)

    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=state,
        prompt="consent",  # Force consent to get refresh token
    )

    return {"authorization_url": authorization_url}


@router.get("/callback", response_class=HTMLResponse)
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Handle Google OAuth2 callback."""
    if error:
        return HTMLResponse(
            content=f"<html><body><h1>Authorization Failed</h1><p>{error}</p></body></html>",
            status_code=400,
        )

    if not code or not state:
        return HTMLResponse(
            content="<html><body><h1>Missing Parameters</h1></body></html>",
            status_code=400,
        )

    # Validate state
    user_id = GoogleOAuthState.validate(state)
    if not user_id:
        return HTMLResponse(
            content="<html><body><h1>Invalid or Expired State</h1></body></html>",
            status_code=400,
        )

    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build

    with make_session() as session:
        oauth_config = get_oauth_config(session)
        redirect_uri = (
            oauth_config.redirect_uris[0]
            if oauth_config.redirect_uris
            else settings.GOOGLE_REDIRECT_URI
        )

        flow = Flow.from_client_config(
            oauth_config.to_client_config(),
            scopes=settings.GOOGLE_SCOPES,
            redirect_uri=redirect_uri,
        )
        flow.fetch_token(code=code)
        credentials = flow.credentials

        # Get user info from Google
        service = build("oauth2", "v2", credentials=credentials)
        user_info = service.userinfo().get().execute()

        # Create or update GoogleAccount
        account = (
            session.query(GoogleAccount)
            .filter(GoogleAccount.email == user_info["email"])
            .first()
        )

        if not account:
            account = GoogleAccount(
                name=user_info.get("name", user_info["email"]),
                email=user_info["email"],
            )
            session.add(account)

        account.access_token = credentials.token
        account.refresh_token = credentials.refresh_token
        account.token_expires_at = credentials.expiry
        account.scopes = list(credentials.scopes or [])
        account.active = True
        account.sync_error = None

        session.commit()

    return HTMLResponse(
        content="""
        <html>
            <body>
                <h1>Google Drive Connected Successfully!</h1>
                <p>You can close this window and configure folders to sync.</p>
                <script>window.close();</script>
            </body>
        </html>
        """,
        status_code=200,
    )


@router.get("/accounts")
def list_accounts(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[AccountResponse]:
    """List all connected Google accounts with their folders."""
    accounts = db.query(GoogleAccount).all()
    return [
        AccountResponse(
            id=cast(int, account.id),
            name=cast(str, account.name),
            email=cast(str, account.email),
            active=cast(bool, account.active),
            last_sync_at=(
                account.last_sync_at.isoformat() if account.last_sync_at else None
            ),
            sync_error=cast(str | None, account.sync_error),
            folders=[
                FolderResponse(
                    id=cast(int, folder.id),
                    folder_id=cast(str, folder.folder_id),
                    folder_name=cast(str, folder.folder_name),
                    folder_path=cast(str | None, folder.folder_path),
                    recursive=cast(bool, folder.recursive),
                    include_shared=cast(bool, folder.include_shared),
                    tags=cast(list[str], folder.tags) or [],
                    check_interval=cast(int, folder.check_interval),
                    last_sync_at=(
                        folder.last_sync_at.isoformat() if folder.last_sync_at else None
                    ),
                    active=cast(bool, folder.active),
                )
                for folder in account.folders
            ],
        )
        for account in accounts
    ]


@router.get("/accounts/{account_id}/browse")
def browse_folder(
    account_id: int,
    folder_id: str = "root",
    page_size: int = 100,
    page_token: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> BrowseResponse:
    """Browse a Google Drive folder to list its contents.

    Special folder_id values:
    - "root": User's My Drive root
    - "shared": Files shared with the user (Shared with me)
    """
    from googleapiclient.discovery import build

    from memory.parsers.google_drive import refresh_credentials

    account = db.get(GoogleAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if not account.active:
        raise HTTPException(status_code=400, detail="Account is not active")

    # Refresh credentials if needed
    credentials = refresh_credentials(account, db)

    # Build the Drive service
    service = build("drive", "v3", credentials=credentials)

    # Handle special folder IDs
    if folder_id == "shared":
        # List files shared with the user
        folder_name = "Shared with me"
        parent_id = "root"  # Allow navigating back to root
        query = "sharedWithMe=true and trashed=false"
    elif folder_id == "root":
        folder_name = "My Drive"
        parent_id = None
        query = "'root' in parents and trashed=false"
    else:
        # Get folder info for a specific folder
        try:
            folder_info = service.files().get(
                fileId=folder_id,
                fields="name, parents",
                supportsAllDrives=True,
            ).execute()
            folder_name = folder_info.get("name", folder_id)
            parents = folder_info.get("parents", [])
            parent_id = parents[0] if parents else None
        except Exception:
            raise HTTPException(status_code=404, detail="Folder not found")
        query = f"'{folder_id}' in parents and trashed=false"

    try:
        response = service.files().list(
            q=query,
            spaces="drive",
            fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)",
            pageToken=page_token,
            pageSize=page_size,
            orderBy="folder,name",  # Folders first, then by name
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list folder: {e}")

    # Convert to response items
    items = []

    # Add "Shared with me" as a virtual folder when at root
    if folder_id == "root":
        items.append(DriveItem(
            id="shared",
            name="Shared with me",
            mime_type="application/vnd.google-apps.folder",
            is_folder=True,
            size=None,
            modified_at=None,
        ))

    for file in response.get("files", []):
        is_folder = file["mimeType"] == "application/vnd.google-apps.folder"
        items.append(DriveItem(
            id=file["id"],
            name=file["name"],
            mime_type=file["mimeType"],
            is_folder=is_folder,
            size=file.get("size"),
            modified_at=file.get("modifiedTime"),
        ))

    return BrowseResponse(
        folder_id=folder_id,
        folder_name=folder_name,
        parent_id=parent_id,
        items=items,
        next_page_token=response.get("nextPageToken"),
    )


@router.post("/accounts/{account_id}/folders")
def add_folder(
    account_id: int,
    folder: FolderCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FolderResponse:
    """Add a folder to sync for a Google account."""
    account = db.get(GoogleAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Check for duplicate
    existing = (
        db.query(GoogleFolder)
        .filter(
            GoogleFolder.account_id == account_id,
            GoogleFolder.folder_id == folder.folder_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Folder already added")

    new_folder = GoogleFolder(
        account_id=account_id,
        folder_id=folder.folder_id,
        folder_name=folder.folder_name,
        recursive=folder.recursive,
        include_shared=folder.include_shared,
        tags=folder.tags,
        check_interval=folder.check_interval,
    )
    db.add(new_folder)
    db.commit()
    db.refresh(new_folder)

    return FolderResponse(
        id=cast(int, new_folder.id),
        folder_id=cast(str, new_folder.folder_id),
        folder_name=cast(str, new_folder.folder_name),
        folder_path=None,
        recursive=cast(bool, new_folder.recursive),
        include_shared=cast(bool, new_folder.include_shared),
        tags=cast(list[str], new_folder.tags) or [],
        check_interval=cast(int, new_folder.check_interval),
        last_sync_at=None,
        active=cast(bool, new_folder.active),
    )


@router.patch("/accounts/{account_id}/folders/{folder_id}")
def update_folder(
    account_id: int,
    folder_id: int,
    updates: FolderUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FolderResponse:
    """Update a folder's configuration."""
    folder = (
        db.query(GoogleFolder)
        .filter(
            GoogleFolder.account_id == account_id,
            GoogleFolder.id == folder_id,
        )
        .first()
    )

    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if updates.folder_name is not None:
        folder.folder_name = updates.folder_name
    if updates.recursive is not None:
        folder.recursive = updates.recursive
    if updates.include_shared is not None:
        folder.include_shared = updates.include_shared
    if updates.tags is not None:
        folder.tags = updates.tags
    if updates.check_interval is not None:
        folder.check_interval = updates.check_interval
    if updates.active is not None:
        folder.active = updates.active

    db.commit()
    db.refresh(folder)

    return FolderResponse(
        id=cast(int, folder.id),
        folder_id=cast(str, folder.folder_id),
        folder_name=cast(str, folder.folder_name),
        folder_path=cast(str | None, folder.folder_path),
        recursive=cast(bool, folder.recursive),
        include_shared=cast(bool, folder.include_shared),
        tags=cast(list[str], folder.tags) or [],
        check_interval=cast(int, folder.check_interval),
        last_sync_at=(
            folder.last_sync_at.isoformat() if folder.last_sync_at else None
        ),
        active=cast(bool, folder.active),
    )


@router.delete("/accounts/{account_id}/folders/{folder_id}")
def remove_folder(
    account_id: int,
    folder_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Remove a folder from sync."""
    folder = (
        db.query(GoogleFolder)
        .filter(
            GoogleFolder.account_id == account_id,
            GoogleFolder.id == folder_id,
        )
        .first()
    )

    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    db.delete(folder)
    db.commit()

    return {"status": "deleted"}


@router.post("/accounts/{account_id}/folders/{folder_id}/sync")
def trigger_sync(
    account_id: int,
    folder_id: int,
    force_full: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Manually trigger a sync for a folder."""
    from memory.workers.tasks.google_drive import sync_google_folder

    folder = (
        db.query(GoogleFolder)
        .filter(
            GoogleFolder.account_id == account_id,
            GoogleFolder.id == folder_id,
        )
        .first()
    )

    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    task = sync_google_folder.delay(folder.id, force_full=force_full)

    return {"task_id": task.id, "status": "scheduled"}


@router.delete("/accounts/{account_id}")
def disconnect_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Disconnect a Google account (removes account and all folders)."""
    account = db.get(GoogleAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    db.delete(account)
    db.commit()

    return {"status": "disconnected"}
