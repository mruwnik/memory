#!/usr/bin/env python3
"""Restore Fernet-encrypted file backups from S3.

Usage:
    # List available backups
    python restore_files.py --list

    # Restore a specific backup
    python restore_files.py emails.tar.gz.enc --output ./restored_files

    # Restore from local file
    python restore_files.py /path/to/backup.tar.gz.enc --output ./restored_files
"""

import argparse
import base64
import hashlib
import io
import os
import sys
import tarfile
from pathlib import Path

import boto3
from cryptography.fernet import Fernet



def get_cipher(password: str) -> Fernet:
    """Create Fernet cipher from password (same derivation as backup.py)."""
    key_bytes = hashlib.sha256(password.encode()).digest()
    key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(key)


def list_backups(bucket: str, prefix: str, region: str) -> list[str]:
    """List available encrypted file backups in S3."""
    s3 = boto3.client("s3", region_name=region)

    try:
        response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    except Exception as e:
        print(f"Error listing S3 bucket: {e}", file=sys.stderr)
        return []

    backups = []
    for obj in response.get("Contents", []):
        key = obj["Key"]
        if key.endswith(".tar.gz.enc"):
            name = key.split("/")[-1]
            size_mb = obj["Size"] / (1024 * 1024)
            modified = obj["LastModified"].strftime("%Y-%m-%d %H:%M:%S")
            backups.append(f"{name:40} {size_mb:8.2f} MB  {modified}")

    return backups


def download_from_s3(
    bucket: str, prefix: str, filename: str, region: str
) -> bytes | None:
    """Download encrypted backup from S3."""
    s3 = boto3.client("s3", region_name=region)
    key = f"{prefix}/{filename}"

    try:
        print(f"Downloading s3://{bucket}/{key}...")
        response = s3.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()
    except Exception as e:
        print(f"Error downloading from S3: {e}", file=sys.stderr)
        return None


def decrypt_and_extract(encrypted_data: bytes, password: str, output_dir: Path) -> bool:
    """Decrypt Fernet-encrypted tarball and extract contents."""
    cipher = get_cipher(password)

    try:
        print("Decrypting...")
        decrypted = cipher.decrypt(encrypted_data)
    except Exception as e:
        print(f"Decryption failed: {e}", file=sys.stderr)
        print("Check that BACKUP_ENCRYPTION_KEY is correct", file=sys.stderr)
        return False

    print(f"Decrypted {len(decrypted)} bytes")

    try:
        print(f"Extracting to {output_dir}...")
        output_dir.mkdir(parents=True, exist_ok=True)
        tar_buffer = io.BytesIO(decrypted)
        with tarfile.open(fileobj=tar_buffer, mode="r:gz") as tar:
            tar.extractall(output_dir)
        print("Extraction complete")
        return True
    except Exception as e:
        print(f"Extraction failed: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Restore Fernet-encrypted file backups from S3"
    )
    parser.add_argument(
        "backup",
        nargs="?",
        help="Backup filename (e.g., emails.tar.gz.enc) or local path",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("./restored_files"),
        help="Output directory for restored files",
    )
    parser.add_argument(
        "--list", "-l", action="store_true", help="List available backups"
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("S3_BACKUP_BUCKET", "equistamp-memory-backup"),
        help="S3 bucket name",
    )
    parser.add_argument(
        "--prefix",
        default=os.getenv("S3_BACKUP_PREFIX", "Daniel"),
        help="S3 prefix",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("S3_BACKUP_REGION", "eu-central-1"),
        help="AWS region",
    )

    args = parser.parse_args()

    # Get encryption key
    password = os.getenv("BACKUP_ENCRYPTION_KEY")
    if not password and not args.list:
        print(
            "Error: BACKUP_ENCRYPTION_KEY environment variable not set", file=sys.stderr
        )
        sys.exit(1)

    # List mode
    if args.list:
        print(f"Available backups in s3://{args.bucket}/{args.prefix}/:\n")
        backups = list_backups(args.bucket, args.prefix, args.region)
        if backups:
            print("Name                                     Size        Modified")
            print("-" * 70)
            for backup in backups:
                print(backup)
        else:
            print("No encrypted backups found")
        return

    # Restore mode
    if not args.backup:
        parser.print_help()
        sys.exit(1)

    # Check if it's a local file or S3 key
    local_path = Path(args.backup)
    if local_path.exists():
        print(f"Reading local file: {local_path}")
        encrypted_data = local_path.read_bytes()
    else:
        # Download from S3
        encrypted_data = download_from_s3(
            args.bucket, args.prefix, args.backup, args.region
        )
        if not encrypted_data:
            sys.exit(1)

    # Decrypt and extract
    if decrypt_and_extract(encrypted_data, password, args.output):
        print(f"\nFiles restored to: {args.output.absolute()}")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
