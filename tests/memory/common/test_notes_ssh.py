"""Host probes pin validated addresses and return keys without saving trust."""

import ipaddress
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from memory.common.notes_ssh import probe_host_keys
from memory.common.safe_http import NetworkPolicy
from memory.common.ssrf import UnsafeURLError


@pytest.mark.asyncio
async def test_probe_uses_validated_ip_and_custom_port():
    public = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    ).decode()
    process = AsyncMock()
    process.returncode = 0
    process.communicate.return_value = (f"[93.184.216.34]:2222 {public}\n".encode(), None)
    with (
        patch('memory.common.notes_ssh.default_policy', return_value=NetworkPolicy()),
        patch('memory.common.notes_ssh.resolve', AsyncMock(return_value=[ipaddress.ip_address('93.184.216.34')])) as resolve,
        patch('memory.common.notes_ssh.asyncio.create_subprocess_exec', AsyncMock(return_value=process)) as execute,
    ):
        result = await probe_host_keys('ssh://git@example.com:2222/notes.git')
    resolve.assert_awaited_once_with('example.com')
    assert execute.call_args.args == ('ssh-keyscan', '-T', '5', '-p', '2222', '-t', 'ed25519,ecdsa,rsa', '93.184.216.34')
    assert result['host'] == 'example.com'
    assert result['port'] == 2222
    assert result['keys'][0]['public_key'] == public
    assert result['keys'][0]['fingerprint'].startswith('SHA256:')


@pytest.mark.asyncio
async def test_probe_blocks_destination_before_starting_keyscan():
    with (
        patch('memory.common.notes_ssh.default_policy', return_value=NetworkPolicy()),
        patch('memory.common.notes_ssh.resolve', AsyncMock(return_value=[ipaddress.ip_address('127.0.0.1')])),
        patch('memory.common.notes_ssh.asyncio.create_subprocess_exec', AsyncMock()) as execute,
    ):
        with pytest.raises(UnsafeURLError):
            await probe_host_keys('git@example.com:notes.git')
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_rejects_empty_response():
    process = AsyncMock()
    process.returncode = 1
    process.communicate.return_value = (b'', None)
    with (
        patch('memory.common.notes_ssh.default_policy', return_value=NetworkPolicy()),
        patch('memory.common.notes_ssh.resolve', AsyncMock(return_value=[ipaddress.ip_address('93.184.216.34')])),
        patch('memory.common.notes_ssh.asyncio.create_subprocess_exec', AsyncMock(return_value=process)),
    ):
        with pytest.raises(ValueError, match='No SSH host keys'):
            await probe_host_keys('git@example.com:notes.git')


@pytest.mark.asyncio
async def test_probe_terminates_keyscan_on_timeout():
    from unittest.mock import Mock

    process = Mock()
    process.returncode = None
    process.communicate = AsyncMock(side_effect=TimeoutError)
    process.wait = AsyncMock()
    with (
        patch('memory.common.notes_ssh.default_policy', return_value=NetworkPolicy()),
        patch('memory.common.notes_ssh.resolve', AsyncMock(return_value=[ipaddress.ip_address('93.184.216.34')])),
        patch('memory.common.notes_ssh.asyncio.create_subprocess_exec', AsyncMock(return_value=process)),
    ):
        with pytest.raises(TimeoutError):
            await probe_host_keys('git@example.com:notes.git')
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
