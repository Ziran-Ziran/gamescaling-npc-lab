"""Resolve local machine paths without persisting them or changing model targets."""
import os
from pathlib import Path, PurePosixPath, PureWindowsPath


def required_setting(name, explicit=None):
    value = os.environ.get(name) if explicit is None else explicit
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Missing controller configuration: set {name}')
    if any(character in value for character in ('\0', '\r', '\n')):
        raise ValueError(f'Invalid controller configuration: check {name}')
    return value.strip()


def wsl_configuration(*, distro=None, repo=None, python=None):
    distro = required_setting('GAMESCALING_WSL_DISTRO', distro)
    repo = required_setting('GAMESCALING_WSL_CLIENT_REPO', repo)
    python = required_setting('GAMESCALING_WSL_PYTHON', python)
    if distro.startswith('-'):
        raise ValueError('GAMESCALING_WSL_DISTRO must be a distribution name')
    for name, value in (('GAMESCALING_WSL_CLIENT_REPO', repo),
                        ('GAMESCALING_WSL_PYTHON', python)):
        if not PurePosixPath(value).is_absolute():
            raise ValueError(f'{name} must be an absolute Linux path')
    return distro, repo, python


def windows_client_repository():
    name = 'GAMESCALING_WINDOWS_CLIENT_REPO'
    value = required_setting(name)
    if not PureWindowsPath(value).is_absolute():
        raise ValueError(f'{name} must be an absolute Windows or UNC path')
    return value


def evaluation_repository(explicit=None):
    name = 'GAMESCALING_CLIENT_REPO'
    value = required_setting(name, explicit)
    if not Path(value).is_absolute():
        raise ValueError(f'{name} or --official-repo must be an absolute path on this OS')
    return value
