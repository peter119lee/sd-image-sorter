"""
Application metadata shared by runtime services and release tooling.
"""

APP_NAME = "SD Image Sorter"
APP_VERSION = "3.5.0-beta.4"

GITHUB_OWNER = "Rinne414"
GITHUB_REPO = "sd-image-sorter"
GITHUB_RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
GITHUB_LATEST_RELEASE_API_URL = f"{GITHUB_RELEASES_API_URL}/latest"
GITHUB_REPOSITORY_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"

PATCH_ASSET_TEMPLATE = "sd-image-sorter-v{version}-app-patch.zip"
WINDOWS_FULL_ASSET_TEMPLATE = "sd-image-sorter-v{version}-windows-portable.zip"
LINUX_FULL_ASSET_TEMPLATE = "sd-image-sorter-v{version}-linux.tar.gz"
# Linux portable bundle: app + python-build-standalone cpython-3.13. Source
# Linux users keep using LINUX_FULL_ASSET_TEMPLATE; this asset family is for
# users on distros without Python 3.12+ in the package manager, or on
# Python 3.14 systems where heavy AI wheels are not yet ready.
#
# Phase 2 ships both x86_64 (Phase 1 default) and aarch64 (Raspberry Pi 5,
# AWS Graviton, ARM Linux servers). The ``{arch}`` slot is filled with the
# values in ``LINUX_PORTABLE_ASSET_ARCHES`` so the in-app updater can pick
# the right tarball for the running machine.
LINUX_PORTABLE_ASSET_TEMPLATE = "sd-image-sorter-v{version}-linux-portable-{arch}.tar.gz"
LINUX_PORTABLE_ASSET_ARCHES = ("x86_64", "aarch64")
PACKAGE_MANIFEST_RELATIVE_PATH = "update/package-manifest.json"
INSTALLED_MANIFEST_RELATIVE_PATH = "update/installed-manifest.json"
