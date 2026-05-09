#
#

# Copyright (C) 2026 the Ganeti project
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
# 1. Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
# IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
# TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""Pytest tests for the NFS-TOCTOU-safe file-storage directory helpers.

Targets the behavioural contract of CreateFileStorageDir and
RemoveFileStorageDir in lib/backend.py: idempotent create on an existing
directory (the actual fix), silent rmdir of a missing directory, and
preserved error paths for "not a directory" and non-empty removal.
"""

import os
import stat

import pytest

from ganeti import backend
from ganeti.storage import filestorage


@pytest.fixture(autouse=True)
def _allow_any_storage_path(monkeypatch):
  """Bypass cluster-allowed-paths check so tests can use tmp_path freely.

  The production check reads /etc/ganeti/file-storage-paths and is
  irrelevant to the TOCTOU behaviour under test.
  """
  monkeypatch.setattr(filestorage, "CheckFileStoragePath", lambda _: None)


def test_create_is_idempotent_on_existing_directory(tmp_path):
  """The fix: a pre-existing directory must not raise EEXIST.

  Old code stat'd first and skipped the mkdir; new code calls
  os.makedirs(exist_ok=True), which must silently succeed when the path
  is already a directory.
  """
  target = tmp_path / "shared-file-storage" / "instance-1"
  target.mkdir(parents=True)

  backend.CreateFileStorageDir(str(target))

  assert target.is_dir()


def test_create_fails_when_path_exists_as_non_directory(tmp_path):
  """exist_ok=True must NOT swallow the case where path is a regular file."""
  target = tmp_path / "instance-1"
  target.write_text("not a directory")

  with pytest.raises(backend.RPCFail, match="not a directory"):
    backend.CreateFileStorageDir(str(target))

  # File was not clobbered.
  assert target.is_file()


def test_create_happy_path_creates_with_mode_0750(tmp_path):
  """Brand-new directory: create succeeds and mode honours 0o750."""
  target = tmp_path / "shared-file-storage" / "instance-1"

  backend.CreateFileStorageDir(str(target))

  assert target.is_dir()
  # Mask off file-type bits; check perms only. umask may strip group/other
  # bits in some environments, so assert the upper bound rather than equality.
  mode = stat.S_IMODE(target.stat().st_mode)
  assert mode & ~0o750 == 0


def test_remove_is_silent_when_directory_does_not_exist(tmp_path):
  """The fix: rmdir of a missing path must succeed silently (ENOENT eaten).

  Mirrors the pre-fix behaviour where os.path.exists() returned False and
  the rmdir was skipped — same external contract, race-free implementation.
  """
  target = tmp_path / "never-existed"

  backend.RemoveFileStorageDir(str(target))

  assert not target.exists()


def test_remove_fails_on_non_empty_directory(tmp_path):
  """ENOTEMPTY must surface as an RPCFail, not be swallowed."""
  target = tmp_path / "instance-1"
  target.mkdir()
  (target / "leftover-disk").write_text("payload")

  with pytest.raises(backend.RPCFail):
    backend.RemoveFileStorageDir(str(target))

  # Directory + contents survive a failed remove.
  assert target.is_dir()
  assert (target / "leftover-disk").is_file()


def test_remove_fails_when_path_is_a_regular_file(tmp_path):
  """rmdir on a non-directory raises NotADirectoryError -> 'not a dir'."""
  target = tmp_path / "instance-1"
  target.write_text("not a directory")

  with pytest.raises(backend.RPCFail, match="not a directory"):
    backend.RemoveFileStorageDir(str(target))

  assert target.is_file()


def test_create_then_remove_round_trip(tmp_path):
  """Sanity: happy paths still compose end-to-end."""
  target = tmp_path / "shared-file-storage" / "instance-1"

  backend.CreateFileStorageDir(str(target))
  assert target.is_dir()

  backend.RemoveFileStorageDir(str(target))
  assert not target.exists()
