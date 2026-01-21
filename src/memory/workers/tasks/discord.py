"""
Celery tasks for Discord message processing.

This module provides tasks for:
- Storing Discord messages in the database
- Downloading and saving image attachments
- Queueing messages for embedding
"""

import hashlib
import logging
import pathlib
from datetime import datetime
from typing import Any, cast

import requests
from sqlalchemy import exc as sqlalchemy_exc

from memory.common import settings
from memory.common.celery_app import (
    ADD_DISCORD_MESSAGE,
    EDIT_DISCORD_MESSAGE,
    UPDATE_REACTIONS,
    app,
)
from memory.common.db.connection import make_session
from memory.common.db.models import DiscordMessage
from memory.common.content_processing import (
    check_content_exists,
    create_task_result,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


def download_and_save_images(image_urls: list[str], message_id: int) -> list[str]:
    """Download images from URLs and save to disk. Returns relative file paths."""
    image_dir = settings.DISCORD_STORAGE_DIR / str(message_id)
    image_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    for url in image_urls:
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            # Generate filename from URL hash
            url_hash = hashlib.md5(url.encode()).hexdigest()
            ext = pathlib.Path(url).suffix or ".jpg"
            ext = ext.split("?")[0]
            filename = f"{url_hash}{ext}"
            local_path = image_dir / filename

            # Save image
            local_path.write_bytes(response.content)

            # Store relative path from FILE_STORAGE_DIR
            relative_path = local_path.relative_to(settings.FILE_STORAGE_DIR)
            saved_paths.append(str(relative_path))

        except Exception as e:
            logger.error(f"Failed to download/save image from {url}: {e}")

    return saved_paths


@app.task(name=ADD_DISCORD_MESSAGE)
@safe_task_execution
def add_discord_message(
    bot_id: int,
    message_id: int,
    channel_id: int,
    author_id: int,
    content: str,
    sent_at: str,
    server_id: int | None = None,
    edited_at: str | None = None,
    reply_to_message_id: int | None = None,
    thread_id: int | None = None,
    message_type: str = "default",
    is_pinned: bool = False,
    images: list[str] | None = None,
    embeds: list[dict] | None = None,
    attachments: list[dict] | None = None,
    reactions: list[dict] | None = None,
    is_edit: bool = False,
) -> dict[str, Any]:
    """
    Add a Discord message to the database and queue for embedding.

    This task is queued by the Discord collector when messages are received.
    """
    logger.info(f"Adding Discord message {message_id}")

    # Include message_id in hash to ensure uniqueness
    content_hash = hashlib.sha256(f"{message_id}:{content}".encode()).digest()
    sent_at_dt = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
    edited_at_dt = (
        datetime.fromisoformat(edited_at.replace("Z", "+00:00"))
        if edited_at
        else None
    )

    # Download and save images to disk
    saved_image_paths = []
    if images:
        saved_image_paths = download_and_save_images(images, message_id)

    with make_session() as session:
        # Check if message already exists
        existing = check_content_exists(
            session, DiscordMessage, message_id=message_id
        )

        if existing and not is_edit:
            existing_msg = cast(DiscordMessage, existing)
            logger.info(f"Discord message already exists: {existing_msg.message_id}")
            return create_task_result(
                existing_msg, "already_exists", message_id=message_id
            )

        if existing and is_edit:
            # Update existing message
            existing_msg = cast(DiscordMessage, existing)
            existing_msg.content = content  # type: ignore
            existing_msg.edited_at = edited_at_dt
            if saved_image_paths:
                existing_msg.images = saved_image_paths  # type: ignore
            if embeds is not None:
                existing_msg.embeds = embeds  # type: ignore
            if attachments is not None:
                existing_msg.attachments = attachments  # type: ignore
            if reactions is not None:
                existing_msg.reactions = reactions  # type: ignore

            try:
                result = process_content_item(existing_msg, session)
            except sqlalchemy_exc.IntegrityError as e:
                logger.error(f"Integrity error editing Discord message {message_id}: {e}")
                return {
                    "status": "error",
                    "error": "Integrity error",
                    "message_id": message_id,
                }
            return result

        # Create new message
        discord_message = DiscordMessage(
            modality="text",
            sha256=content_hash,
            content=content,
            bot_id=bot_id,
            message_id=message_id,
            channel_id=channel_id,
            server_id=server_id,
            author_id=author_id,
            sent_at=sent_at_dt,
            edited_at=edited_at_dt,
            reply_to_message_id=reply_to_message_id,
            thread_id=thread_id,
            message_type=message_type,
            is_pinned=is_pinned,
            images=saved_image_paths or None,
            embeds=embeds,
            attachments=attachments,
            reactions=reactions,
        )

        try:
            result = process_content_item(discord_message, session)
        except sqlalchemy_exc.IntegrityError as e:
            logger.error(f"Integrity error adding Discord message {message_id}: {e}")
            return {
                "status": "error",
                "error": "Integrity error",
                "message_id": message_id,
            }

        return result


@app.task(name=EDIT_DISCORD_MESSAGE)
@safe_task_execution
def edit_discord_message(
    message_id: int,
    content: str,
    edited_at: str,
) -> dict[str, Any]:
    """
    Edit a Discord message in the database.

    This task is queued by the Discord collector when messages are edited.
    """
    logger.info(f"Editing Discord message {message_id}")

    with make_session() as session:
        existing = check_content_exists(
            session, DiscordMessage, message_id=message_id
        )
        if not existing:
            return {
                "status": "error",
                "error": "Message not found",
                "message_id": message_id,
            }

        existing_msg = cast(DiscordMessage, existing)
        existing_msg.content = content  # type: ignore
        existing_msg.edited_at = datetime.fromisoformat(
            edited_at.replace("Z", "+00:00")
        )

        try:
            result = process_content_item(existing_msg, session)
        except sqlalchemy_exc.IntegrityError as e:
            logger.error(f"Integrity error editing Discord message {message_id}: {e}")
            return {
                "status": "error",
                "error": "Integrity error",
                "message_id": message_id,
            }

        return result


@app.task(name=UPDATE_REACTIONS)
@safe_task_execution
def update_reactions(
    message_id: int,
    reactions: list[dict],
) -> dict[str, Any]:
    """
    Update reactions on a Discord message.

    This task is queued when reactions are added or removed.
    """
    logger.info(f"Updating reactions on Discord message {message_id}")

    with make_session() as session:
        existing = check_content_exists(
            session, DiscordMessage, message_id=message_id
        )
        if not existing:
            return {
                "status": "error",
                "error": "Message not found",
                "message_id": message_id,
            }

        existing_msg = cast(DiscordMessage, existing)
        existing_msg.reactions = reactions  # type: ignore
        session.commit()

        return {
            "status": "updated",
            "message_id": message_id,
        }
