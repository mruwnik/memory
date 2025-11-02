#!/usr/bin/env python3
"""
Script to run Celery tasks on the Docker Compose setup from your local machine.

This script connects to the RabbitMQ broker running in Docker and sends tasks
to the workers. It requires the same dependencies as the workers to import
the task definitions.

Usage:
    python run_celery_task.py --help
    python run_celery_task.py email sync-all-accounts
    python run_celery_task.py email sync-account --account-id 1
    python run_celery_task.py ebook sync-book --file-path "/path/to/book.epub" --tags "fiction,scifi"
    python run_celery_task.py maintenance clean-all-collections
    python run_celery_task.py blogs sync-webpage --url "https://example.com"
    python run_celery_task.py comic sync-all-comics
    python run_celery_task.py forums sync-lesswrong --since-date "2025-01-01" --min-karma 10 --limit 50 --cooldown 0.5 --max-items 1000
"""

import json
import sys
from typing import Any

import click
from celery import Celery
from memory.common import settings
from memory.common.celery_app import (
    SYNC_ALL_ARTICLE_FEEDS,
    SYNC_ARTICLE_FEED,
    ADD_ARTICLE_FEED,
    SYNC_WEBPAGE,
    SYNC_WEBSITE_ARCHIVE,
    SYNC_ALL_COMICS,
    SYNC_COMIC,
    SYNC_SMBC,
    SYNC_XKCD,
    SYNC_BOOK,
    PROCESS_EMAIL,
    SYNC_ACCOUNT,
    SYNC_ALL_ACCOUNTS,
    SYNC_LESSWRONG,
    SYNC_LESSWRONG_POST,
    CLEAN_ALL_COLLECTIONS,
    CLEAN_COLLECTION,
    REINGEST_CHUNK,
    REINGEST_EMPTY_SOURCE_ITEMS,
    REINGEST_ALL_EMPTY_SOURCE_ITEMS,
    REINGEST_ITEM,
    REINGEST_MISSING_CHUNKS,
    UPDATE_METADATA_FOR_ITEM,
    UPDATE_METADATA_FOR_SOURCE_ITEMS,
    SETUP_GIT_NOTES,
    TRACK_GIT_CHANGES,
    BACKUP_TO_S3_DIRECTORY,
    BACKUP_ALL,
    app,
)


TASK_MAPPINGS = {
    "email": {
        "sync_all_accounts": SYNC_ALL_ACCOUNTS,
        "sync_account": SYNC_ACCOUNT,
        "process_message": PROCESS_EMAIL,
    },
    "ebook": {
        "sync_book": SYNC_BOOK,
    },
    "maintenance": {
        "clean_all_collections": CLEAN_ALL_COLLECTIONS,
        "clean_collection": CLEAN_COLLECTION,
        "reingest_missing_chunks": REINGEST_MISSING_CHUNKS,
        "reingest_chunk": REINGEST_CHUNK,
        "reingest_item": REINGEST_ITEM,
        "reingest_empty_source_items": REINGEST_EMPTY_SOURCE_ITEMS,
        "reingest_all_empty_source_items": REINGEST_ALL_EMPTY_SOURCE_ITEMS,
        "update_metadata_for_item": UPDATE_METADATA_FOR_ITEM,
        "update_metadata_for_source_items": UPDATE_METADATA_FOR_SOURCE_ITEMS,
    },
    "blogs": {
        "sync_webpage": SYNC_WEBPAGE,
        "sync_article_feed": SYNC_ARTICLE_FEED,
        "sync_all_article_feeds": SYNC_ALL_ARTICLE_FEEDS,
        "sync_website_archive": SYNC_WEBSITE_ARCHIVE,
        "add_article_feed": ADD_ARTICLE_FEED,
    },
    "comic": {
        "sync_all_comics": SYNC_ALL_COMICS,
        "sync_smbc": SYNC_SMBC,
        "sync_xkcd": SYNC_XKCD,
        "sync_comic": SYNC_COMIC,
        "full_sync_comics": "memory.workers.tasks.comic.full_sync_comic",
    },
    "forums": {
        "sync_lesswrong": SYNC_LESSWRONG,
        "sync_lesswrong_post": SYNC_LESSWRONG_POST,
    },
    "notes": {
        "setup_git_notes": SETUP_GIT_NOTES,
        "track_git_changes": TRACK_GIT_CHANGES,
    },
    "backup": {
        "backup_to_s3_directory": BACKUP_TO_S3_DIRECTORY,
        "backup_all": BACKUP_ALL,
    },
}
QUEUE_MAPPINGS = {
    "email": "email",
    "ebook": "ebooks",
    "photo": "photo_embed",
}


def run_task(app: Celery, category: str, task_name: str, **kwargs) -> str:
    """Run a task using the task mappings."""
    if category not in TASK_MAPPINGS:
        raise ValueError(f"Unknown category: {category}")

    if task_name not in TASK_MAPPINGS[category]:
        raise ValueError(f"Unknown {category} task: {task_name}")

    task_path = TASK_MAPPINGS[category][task_name]
    queue_name = QUEUE_MAPPINGS.get(category) or category

    result = app.send_task(
        task_path, kwargs=kwargs, queue=f"{settings.CELERY_QUEUE_PREFIX}-{queue_name}"
    )
    return result.id


def get_task_result(app: Celery, task_id: str, timeout: int = 300) -> Any:
    """Get the result of a task."""
    result = app.AsyncResult(task_id)
    try:
        return result.get(timeout=timeout)
    except Exception as e:
        return {"error": str(e), "status": result.status}


@click.group()
@click.option("--wait", is_flag=True, help="Wait for task completion and show result")
@click.option(
    "--timeout", default=300, help="Timeout in seconds when waiting for result"
)
@click.pass_context
def cli(ctx, wait, timeout):
    """Run Celery tasks on Docker Compose setup."""
    ctx.ensure_object(dict)
    ctx.obj["wait"] = wait
    ctx.obj["timeout"] = timeout

    try:
        ctx.obj["app"] = app
    except Exception as e:
        click.echo(f"Error connecting to Celery broker: {e}")
        click.echo(
            "Make sure Docker Compose is running and RabbitMQ is accessible on localhost:15673"
        )
        sys.exit(1)


def execute_task(ctx, category: str, task_name: str, **kwargs):
    """Helper to execute a task and handle results."""
    app = ctx.obj["app"]
    wait = ctx.obj["wait"]
    timeout = ctx.obj["timeout"]

    # Filter out None values
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    try:
        task_id = run_task(app, category, task_name, **kwargs)
        click.echo("Task submitted successfully!")
        click.echo(f"Task ID: {task_id}")

        if wait:
            click.echo(f"Waiting for task completion (timeout: {timeout}s)...")
            result = get_task_result(app, task_id, timeout)
            click.echo("Task result:")
            click.echo(json.dumps(result, indent=2, default=str))
    except Exception as e:
        click.echo(f"Error running task: {e}")
        sys.exit(1)


@cli.group()
@click.pass_context
def backup(ctx):
    """Backup-related tasks."""
    pass


@backup.command("all")
@click.pass_context
def backup_all(ctx):
    """Backup all directories."""
    execute_task(ctx, "backup", "backup_all")


@backup.command("path")
@click.option("--path", required=True, help="Path to backup")
@click.pass_context
def backup_to_s3_directory(ctx, path):
    """Backup a specific path."""
    execute_task(ctx, "backup", "backup_to_s3_directory", path=path)


@cli.group()
@click.pass_context
def email(ctx):
    """Email-related tasks."""
    pass


@email.command("sync-all-accounts")
@click.option("--since-date", help="Sync items since this date (ISO format)")
@click.pass_context
def email_sync_all_accounts(ctx, since_date):
    """Sync all email accounts."""
    execute_task(ctx, "email", "sync_all_accounts", since_date=since_date)


@email.command("sync-account")
@click.option("--account-id", type=int, required=True, help="Email account ID")
@click.option("--since-date", help="Sync items since this date (ISO format)")
@click.pass_context
def email_sync_account(ctx, account_id, since_date):
    """Sync a specific email account."""
    execute_task(
        ctx, "email", "sync_account", account_id=account_id, since_date=since_date
    )


@email.command("process-message")
@click.option("--message-id", required=True, help="Email message ID")
@click.option("--folder", help="Email folder name")
@click.option("--raw-email", help="Raw email content")
@click.pass_context
def email_process_message(ctx, message_id, folder, raw_email):
    """Process a specific email message."""
    execute_task(
        ctx,
        "email",
        "process_message",
        message_id=message_id,
        folder=folder,
        raw_email=raw_email,
    )


@cli.group()
@click.pass_context
def ebook(ctx):
    """Ebook-related tasks."""
    pass


@ebook.command("sync-book")
@click.option("--file-path", required=True, help="Path to ebook file")
@click.option("--tags", help="Comma-separated tags")
@click.pass_context
def ebook_sync_book(ctx, file_path, tags):
    """Sync an ebook."""
    execute_task(ctx, "ebook", "sync_book", file_path=file_path, tags=tags)


@cli.group()
@click.pass_context
def notes(ctx):
    """Notes-related tasks."""
    pass


@notes.command("setup-git-notes")
@click.option("--origin", required=True, help="Git origin")
@click.option("--email", required=True, help="Git email")
@click.option("--name", required=True, help="Git name")
@click.pass_context
def notes_setup_git_notes(ctx, origin, email, name):
    """Setup git notes."""
    execute_task(ctx, "notes", "setup_git_notes", origin=origin, email=email, name=name)


@notes.command("track-git-changes")
@click.pass_context
def notes_track_git_changes(ctx):
    """Track git changes."""
    execute_task(ctx, "notes", "track_git_changes")


@cli.group()
@click.pass_context
def maintenance(ctx):
    """Maintenance tasks."""
    pass


@maintenance.command("clean-all-collections")
@click.pass_context
def maintenance_clean_all_collections(ctx):
    """Clean all collections."""
    execute_task(ctx, "maintenance", "clean_all_collections")


@maintenance.command("clean-collection")
@click.option("--collection", required=True, help="Collection name to clean")
@click.pass_context
def maintenance_clean_collection(ctx, collection):
    """Clean a specific collection."""
    execute_task(ctx, "maintenance", "clean_collection", collection=collection)


@maintenance.command("reingest-missing-chunks")
@click.option("--minutes-ago", type=int, help="Minutes ago to reingest chunks")
@click.pass_context
def maintenance_reingest_missing_chunks(ctx, minutes_ago):
    """Reingest missing chunks."""
    execute_task(ctx, "maintenance", "reingest_missing_chunks", minutes_ago=minutes_ago)


@maintenance.command("reingest-item")
@click.option("--item-id", required=True, help="Item ID to reingest")
@click.option("--item-type", required=True, help="Item type to reingest")
@click.pass_context
def maintenance_reingest_item(ctx, item_id, item_type):
    """Reingest a specific item."""
    execute_task(
        ctx, "maintenance", "reingest_item", item_id=item_id, item_type=item_type
    )


@maintenance.command("update-metadata-for-item")
@click.option("--item-id", required=True, help="Item ID to update metadata for")
@click.option("--item-type", required=True, help="Item type to update metadata for")
@click.pass_context
def maintenance_update_metadata_for_item(ctx, item_id, item_type):
    """Update metadata for a specific item."""
    execute_task(
        ctx,
        "maintenance",
        "update_metadata_for_item",
        item_id=item_id,
        item_type=item_type,
    )


@maintenance.command("update-metadata-for-source-items")
@click.option("--item-type", required=True, help="Item type to update metadata for")
@click.pass_context
def maintenance_update_metadata_for_source_items(ctx, item_type):
    """Update metadata for all items of a specific type."""
    execute_task(
        ctx, "maintenance", "update_metadata_for_source_items", item_type=item_type
    )


@maintenance.command("reingest-empty-source-items")
@click.option("--item-type", required=True, help="Item type to reingest")
@click.pass_context
def maintenance_reingest_empty_source_items(ctx, item_type):
    """Reingest empty source items."""
    execute_task(ctx, "maintenance", "reingest_empty_source_items", item_type=item_type)


@maintenance.command("reingest-all-empty-source-items")
@click.pass_context
def maintenance_reingest_all_empty_source_items(ctx):
    """Reingest all empty source items."""
    execute_task(ctx, "maintenance", "reingest_all_empty_source_items")


@maintenance.command("reingest-chunk")
@click.option("--chunk-id", required=True, help="Chunk ID to reingest")
@click.pass_context
def maintenance_reingest_chunk(ctx, chunk_id):
    """Reingest a specific chunk."""
    execute_task(ctx, "maintenance", "reingest_chunk", chunk_id=chunk_id)


@cli.group()
@click.pass_context
def blogs(ctx):
    """Blog-related tasks."""
    pass


@blogs.command("sync-webpage")
@click.option("--url", required=True, help="URL to sync")
@click.pass_context
def blogs_sync_webpage(ctx, url):
    """Sync a webpage."""
    execute_task(ctx, "blogs", "sync_webpage", url=url)


@blogs.command("sync-article-feed")
@click.option("--feed-id", type=int, required=True, help="Feed ID to sync")
@click.pass_context
def blogs_sync_article_feed(ctx, feed_id):
    """Sync an article feed."""
    execute_task(ctx, "blogs", "sync_article_feed", feed_id=feed_id)


@blogs.command("sync-all-article-feeds")
@click.pass_context
def blogs_sync_all_article_feeds(ctx):
    """Sync all article feeds."""
    execute_task(ctx, "blogs", "sync_all_article_feeds")


@blogs.command("sync-website-archive")
@click.option("--url", required=True, help="URL to sync")
@click.pass_context
def blogs_sync_website_archive(ctx, url):
    """Sync a website archive."""
    execute_task(ctx, "blogs", "sync_website_archive", url=url)


@blogs.command("add-article-feed")
@click.option("--url", required=True, help="URL of the feed")
@click.option("--title", help="Title of the feed")
@click.option("--description", help="Description of the feed")
@click.option("--tags", help="Comma-separated tags to apply to the feed", default="")
@click.option("--active", is_flag=True, help="Whether the feed is active")
@click.option(
    "--check-interval",
    type=int,
    help="Interval in minutes to check the feed",
    default=60 * 24,  # 24 hours
)
@click.pass_context
def blogs_add_article_feed(ctx, url, title, description, tags, active, check_interval):
    """Add a new article feed."""
    execute_task(
        ctx,
        "blogs",
        "add_article_feed",
        url=url,
        title=title,
        description=description,
        tags=tags.split(","),
        active=active,
        check_interval=check_interval,
    )


@cli.group()
@click.pass_context
def comic(ctx):
    """Comic-related tasks."""
    pass


@comic.command("sync-all-comics")
@click.pass_context
def comic_sync_all_comics(ctx):
    """Sync all comics."""
    execute_task(ctx, "comic", "sync_all_comics")


@comic.command("sync-smbc")
@click.pass_context
def comic_sync_smbc(ctx):
    """Sync SMBC comics."""
    execute_task(ctx, "comic", "sync_smbc")


@comic.command("sync-xkcd")
@click.pass_context
def comic_sync_xkcd(ctx):
    """Sync XKCD comics."""
    execute_task(ctx, "comic", "sync_xkcd")


@comic.command("sync-comic")
@click.option("--image-url", required=True, help="Image URL to sync")
@click.option("--title", help="Comic title")
@click.option("--author", help="Comic author")
@click.option("--published-date", help="Comic published date")
@click.pass_context
def comic_sync_comic(ctx, image_url, title, author, published_date):
    """Sync a specific comic."""
    execute_task(
        ctx,
        "comic",
        "sync_comic",
        image_url=image_url,
        title=title,
        author=author,
        published_date=published_date,
    )


@comic.command("full-sync-comics")
@click.pass_context
def comic_full_sync_comics(ctx):
    """Full sync comics."""
    execute_task(ctx, "comic", "full_sync_comics")


@cli.group()
@click.pass_context
def forums(ctx):
    """Forum-related tasks."""
    pass


@forums.command("sync-lesswrong")
@click.option("--since-date", help="Sync items since this date (ISO format)")
@click.option("--min-karma", type=int, help="Minimum karma to sync")
@click.option("--limit", type=int, help="Limit the number of posts to sync")
@click.option("--cooldown", type=float, help="Cooldown between posts")
@click.option("--max-items", type=int, help="Maximum number of posts to sync")
@click.pass_context
def forums_sync_lesswrong(ctx, since_date, min_karma, limit, cooldown, max_items):
    """Sync LessWrong posts."""
    execute_task(
        ctx,
        "forums",
        "sync_lesswrong",
        since=since_date,
        min_karma=min_karma,
        limit=limit,
        cooldown=cooldown,
        max_items=max_items,
    )


@forums.command("sync-lesswrong-post")
@click.option("--url", required=True, help="LessWrong post URL")
@click.pass_context
def forums_sync_lesswrong_post(ctx, url):
    """Sync a specific LessWrong post."""
    execute_task(ctx, "forums", "sync_lesswrong_post", url=url)


if __name__ == "__main__":
    cli()
