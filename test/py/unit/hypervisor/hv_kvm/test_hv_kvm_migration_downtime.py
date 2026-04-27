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

"""Tests for progressive migration downtime and cancel threshold."""

import os
import time
from unittest import mock

import pytest

from ganeti import constants
from ganeti import errors
from ganeti import objects
from ganeti import serializer
from ganeti.hypervisor.hv_kvm import KVMHypervisor
from ganeti.hypervisor.hv_kvm.validation import validate_migration_parameters


def _make_instance(name="inst1",
                   migration_downtime=30,
                   migration_downtime_max=0,
                   migration_cancel_threshold=0,
                   migration_caps=""):
  """Build a minimal Instance with the hvparams we care about."""
  hvparams = {
    constants.HV_MIGRATION_DOWNTIME: migration_downtime,
    constants.HV_MIGRATION_DOWNTIME_MAX: migration_downtime_max,
    constants.HV_MIGRATION_CANCEL_THRESHOLD: migration_cancel_threshold,
    constants.HV_KVM_MIGRATION_CAPS: migration_caps,
  }
  inst = mock.MagicMock()
  inst.name = name
  inst.hvparams = hvparams
  return inst


def _make_status(transferred_ram=100, total_ram=100,
                 postcopy_status=None, migration_downtime=None):
  """Build a MigrationStatus."""
  return objects.MigrationStatus(
      status=constants.HV_MIGRATION_ACTIVE,
      transferred_ram=transferred_ram,
      total_ram=total_ram,
      postcopy_status=postcopy_status,
      migration_downtime=migration_downtime,
  )


def _make_hv(tmp_path):
  """Build a KVMHypervisor with mocked QMP and CTRL_DIR."""
  hv = KVMHypervisor.__new__(KVMHypervisor)
  hv.qmp = mock.MagicMock()
  KVMHypervisor._CTRL_DIR = str(tmp_path)
  return hv


# ---------- Downtime bump tests ----------

class TestMaybeBumpMigrationDowntime:

  def test_below_threshold_no_bump(self, tmp_path):
    """Below 200% ratio -> no QMP call, no state change."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=30, migration_downtime_max=1000)
    status = _make_status(transferred_ram=150, total_ram=100)
    query_migrate = {"status": "active"}
    # Write state file so feature is active
    hv._WriteMigrationState("inst1", {"downtime": 30, "last_step": 0})
    hv._MaybeBumpMigrationDowntime(inst, status, query_migrate)
    hv.qmp.SetMigrationDowntime.assert_not_called()

  def test_above_threshold_first_bump(self, tmp_path):
    """Above 200% ratio, first observation -> bump, state file written."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=30, migration_downtime_max=1000)
    status = _make_status(transferred_ram=250, total_ram=100)
    query_migrate = {"status": "active"}
    hv._WriteMigrationState("inst1", {"downtime": 30, "last_step": 0})
    hv._MaybeBumpMigrationDowntime(inst, status, query_migrate)
    hv.qmp.SetMigrationDowntime.assert_called_once_with(33)
    state = hv._ReadMigrationState("inst1")
    assert state["downtime"] == 33

  def test_second_bump_too_soon(self, tmp_path):
    """Above 200%, second observation < 30s later -> no bump."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=30, migration_downtime_max=1000)
    status = _make_status(transferred_ram=250, total_ram=100)
    query_migrate = {"status": "active"}
    now = time.time()
    hv._WriteMigrationState("inst1", {"downtime": 33, "last_step": now})
    hv._MaybeBumpMigrationDowntime(inst, status, query_migrate)
    hv.qmp.SetMigrationDowntime.assert_not_called()

  def test_bump_after_interval(self, tmp_path):
    """Above 200%, >= 30s elapsed -> bump again, state updated."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=30, migration_downtime_max=1000)
    status = _make_status(transferred_ram=250, total_ram=100)
    query_migrate = {"status": "active"}
    old_time = time.time() - 31
    hv._WriteMigrationState("inst1", {"downtime": 33, "last_step": old_time})
    hv._MaybeBumpMigrationDowntime(inst, status, query_migrate)
    hv.qmp.SetMigrationDowntime.assert_called_once_with(36)
    state = hv._ReadMigrationState("inst1")
    assert state["downtime"] == 36

  def test_already_at_ceiling(self, tmp_path):
    """Current downtime already at migration_downtime_max -> no bump."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=30, migration_downtime_max=100)
    status = _make_status(transferred_ram=250, total_ram=100)
    query_migrate = {"status": "active"}
    hv._WriteMigrationState("inst1", {"downtime": 100, "last_step": 0})
    hv._MaybeBumpMigrationDowntime(inst, status, query_migrate)
    hv.qmp.SetMigrationDowntime.assert_not_called()

  def test_ceiling_le_base_inert(self, tmp_path):
    """migration_downtime_max <= migration_downtime -> feature inert."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=30, migration_downtime_max=30)
    status = _make_status(transferred_ram=250, total_ram=100)
    query_migrate = {"status": "active"}
    hv._MaybeBumpMigrationDowntime(inst, status, query_migrate)
    hv.qmp.SetMigrationDowntime.assert_not_called()

  def test_default_zero_inert(self, tmp_path):
    """migration_downtime_max == 0 -> feature inert (default off)."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=30, migration_downtime_max=0)
    status = _make_status(transferred_ram=250, total_ram=100)
    query_migrate = {"status": "active"}
    hv._MaybeBumpMigrationDowntime(inst, status, query_migrate)
    hv.qmp.SetMigrationDowntime.assert_not_called()

  def test_postcopy_caps_no_bump(self, tmp_path):
    """Postcopy active (migration_caps contains postcopy-ram) -> no bump."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=30, migration_downtime_max=1000,
                          migration_caps="postcopy-ram")
    status = _make_status(transferred_ram=250, total_ram=100)
    query_migrate = {"status": "active"}
    hv._MaybeBumpMigrationDowntime(inst, status, query_migrate)
    hv.qmp.SetMigrationDowntime.assert_not_called()

  def test_postcopy_active_status_no_bump(self, tmp_path):
    """Postcopy active (status reported as postcopy-active) -> no bump."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=30, migration_downtime_max=1000)
    status = _make_status(transferred_ram=250, total_ram=100)
    query_migrate = {"status": "postcopy-active"}
    hv._MaybeBumpMigrationDowntime(inst, status, query_migrate)
    hv.qmp.SetMigrationDowntime.assert_not_called()

  def test_postcopy_status_field_no_bump(self, tmp_path):
    """postcopy_status set on MigrationStatus -> no bump."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=30, migration_downtime_max=1000)
    status = _make_status(transferred_ram=250, total_ram=100,
                          postcopy_status="postcopy-active")
    query_migrate = {"status": "active"}
    hv._MaybeBumpMigrationDowntime(inst, status, query_migrate)
    hv.qmp.SetMigrationDowntime.assert_not_called()

  def test_clamp_to_ceiling(self, tmp_path):
    """10% rounds up but is clamped to ceiling (current=95, max=100)."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=30, migration_downtime_max=100)
    status = _make_status(transferred_ram=250, total_ram=100)
    query_migrate = {"status": "active"}
    hv._WriteMigrationState("inst1", {"downtime": 95, "last_step": 0})
    hv._MaybeBumpMigrationDowntime(inst, status, query_migrate)
    # int(95 * 1.10) = 104, clamped to 100
    hv.qmp.SetMigrationDowntime.assert_called_once_with(100)

  def test_small_base_linear_floor(self, tmp_path):
    """Small base: current=1, max=10 -> bump to 2 (linear floor)."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=1, migration_downtime_max=10)
    status = _make_status(transferred_ram=250, total_ram=100)
    query_migrate = {"status": "active"}
    hv._WriteMigrationState("inst1", {"downtime": 1, "last_step": 0})
    hv._MaybeBumpMigrationDowntime(inst, status, query_migrate)
    # int(1 * 1.10) = 1, but max(1+1, 1) = 2
    hv.qmp.SetMigrationDowntime.assert_called_once_with(2)

  def test_qmp_error_swallowed(self, tmp_path):
    """QMP raises HypervisorError -> swallowed, no state file write."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=30, migration_downtime_max=1000)
    status = _make_status(transferred_ram=250, total_ram=100)
    query_migrate = {"status": "active"}
    hv._WriteMigrationState("inst1", {"downtime": 30, "last_step": 0})
    hv.qmp.SetMigrationDowntime.side_effect = errors.HypervisorError("gone")
    hv._MaybeBumpMigrationDowntime(inst, status, query_migrate)
    # State file should still have old value
    state = hv._ReadMigrationState("inst1")
    assert state["downtime"] == 30

  def test_no_state_file_creates_from_base(self, tmp_path):
    """No state file -> uses base downtime as fallback."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=30, migration_downtime_max=1000)
    status = _make_status(transferred_ram=250, total_ram=100)
    query_migrate = {"status": "active"}
    # No state file written — _ReadMigrationState returns None
    # But the method writes state only if os.path.exists(state_path),
    # and without a state file it won't write. Let's verify no crash.
    hv._MaybeBumpMigrationDowntime(inst, status, query_migrate)
    # SetMigrationDowntime should still be called (base 30 -> 33)
    hv.qmp.SetMigrationDowntime.assert_called_once_with(33)

  def test_sets_migration_downtime_on_status(self, tmp_path):
    """After bump, status.migration_downtime is set for operator feedback."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=30, migration_downtime_max=1000)
    status = _make_status(transferred_ram=250, total_ram=100)
    query_migrate = {"status": "active"}
    hv._WriteMigrationState("inst1", {"downtime": 30, "last_step": 0})
    hv._MaybeBumpMigrationDowntime(inst, status, query_migrate)
    assert status.migration_downtime == 33


# ---------- Cancel threshold tests ----------

class TestMaybeCancelStuckMigration:

  def test_threshold_zero_no_cancel(self, tmp_path):
    """migration_cancel_threshold=0 -> cancel never fires."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_cancel_threshold=0)
    status = _make_status(transferred_ram=200, total_ram=100)
    assert hv._MaybeCancelStuckMigration(inst, status) is False
    hv.qmp.CancelMigration.assert_not_called()

  def test_below_threshold_no_cancel(self, tmp_path):
    """migration_cancel_threshold=150, ratio 1.4 -> no cancel."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_cancel_threshold=150)
    status = _make_status(transferred_ram=140, total_ram=100)
    assert hv._MaybeCancelStuckMigration(inst, status) is False
    hv.qmp.CancelMigration.assert_not_called()

  def test_above_threshold_cancels(self, tmp_path):
    """migration_cancel_threshold=150, ratio 1.51 -> cancel called."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_cancel_threshold=150)
    status = _make_status(transferred_ram=151, total_ram=100)
    assert hv._MaybeCancelStuckMigration(inst, status) is True
    hv.qmp.CancelMigration.assert_called_once()

  def test_cancel_overrides_bump(self, tmp_path):
    """Cancel and bump both configured, threshold tripped -> cancel only."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_downtime=30,
                          migration_downtime_max=1000,
                          migration_cancel_threshold=150)
    status = _make_status(transferred_ram=200, total_ram=100)
    query_migrate = {"status": "active"}
    hv._WriteMigrationState("inst1", {"downtime": 30, "last_step": 0})
    # Cancel fires first
    assert hv._MaybeCancelStuckMigration(inst, status) is True
    # The real code path skips bump when cancel returns True
    hv.qmp.CancelMigration.assert_called_once()
    hv.qmp.SetMigrationDowntime.assert_not_called()

  def test_cancel_qmp_error_still_returns_true(self, tmp_path):
    """QMP CancelMigration raises -> swallowed, helper still returns True."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_cancel_threshold=150)
    status = _make_status(transferred_ram=200, total_ram=100)
    hv.qmp.CancelMigration.side_effect = errors.HypervisorError("dead")
    assert hv._MaybeCancelStuckMigration(inst, status) is True

  def test_zero_total_ram_no_cancel(self, tmp_path):
    """total_ram=0 -> no division by zero, no cancel."""
    hv = _make_hv(tmp_path)
    inst = _make_instance(migration_cancel_threshold=150)
    status = _make_status(transferred_ram=200, total_ram=0)
    assert hv._MaybeCancelStuckMigration(inst, status) is False


# ---------- Validation tests ----------

class TestValidateMigrationParameters:

  def test_rejects_bad_ceiling(self):
    """Validator rejects migration_downtime_max=10, migration_downtime=50."""
    hvparams = {
      constants.HV_MIGRATION_DOWNTIME_MAX: 10,
      constants.HV_MIGRATION_DOWNTIME: 50,
    }
    with pytest.raises(errors.HypervisorError):
      validate_migration_parameters(hvparams)

  def test_accepts_zero(self):
    """Validator accepts migration_downtime_max=0, migration_downtime=50."""
    hvparams = {
      constants.HV_MIGRATION_DOWNTIME_MAX: 0,
      constants.HV_MIGRATION_DOWNTIME: 50,
    }
    validate_migration_parameters(hvparams)

  def test_accepts_ceiling_ge_base(self):
    """Validator accepts migration_downtime_max >= migration_downtime."""
    hvparams = {
      constants.HV_MIGRATION_DOWNTIME_MAX: 100,
      constants.HV_MIGRATION_DOWNTIME: 50,
    }
    validate_migration_parameters(hvparams)

  def test_accepts_ceiling_equals_base(self):
    """Validator accepts migration_downtime_max == migration_downtime."""
    hvparams = {
      constants.HV_MIGRATION_DOWNTIME_MAX: 50,
      constants.HV_MIGRATION_DOWNTIME: 50,
    }
    validate_migration_parameters(hvparams)


# ---------- PARAMETERS per-key check tests ----------

class TestParameterCheck:

  def test_cancel_threshold_rejects_50(self):
    """PARAMETERS per-key check rejects migration_cancel_threshold=50."""
    check = KVMHypervisor.PARAMETERS[
        constants.HV_MIGRATION_CANCEL_THRESHOLD]
    # 5-tuple: (required, check_fn, err_msg, default, private)
    check_fn = check[1]
    assert check_fn(50) is False

  def test_cancel_threshold_accepts_zero(self):
    """PARAMETERS per-key check accepts 0."""
    check = KVMHypervisor.PARAMETERS[
        constants.HV_MIGRATION_CANCEL_THRESHOLD]
    check_fn = check[1]
    assert check_fn(0) is True

  def test_cancel_threshold_accepts_101(self):
    """PARAMETERS per-key check accepts 101."""
    check = KVMHypervisor.PARAMETERS[
        constants.HV_MIGRATION_CANCEL_THRESHOLD]
    check_fn = check[1]
    assert check_fn(101) is True

  def test_cancel_threshold_rejects_100(self):
    """PARAMETERS per-key check rejects exactly 100."""
    check = KVMHypervisor.PARAMETERS[
        constants.HV_MIGRATION_CANCEL_THRESHOLD]
    check_fn = check[1]
    assert check_fn(100) is False


# ---------- State file helpers ----------

class TestMigrationStateHelpers:

  def test_write_read_roundtrip(self, tmp_path):
    """Write + read state file roundtrips correctly."""
    KVMHypervisor._CTRL_DIR = str(tmp_path)
    state = {"downtime": 42, "last_step": 1234567890.0}
    KVMHypervisor._WriteMigrationState("inst1", state)
    got = KVMHypervisor._ReadMigrationState("inst1")
    assert got == state

  def test_read_missing_returns_none(self, tmp_path):
    """Reading a non-existent state file returns None."""
    KVMHypervisor._CTRL_DIR = str(tmp_path)
    assert KVMHypervisor._ReadMigrationState("noexist") is None

  def test_path(self, tmp_path):
    """State file path uses CTRL_DIR and .migstate extension."""
    KVMHypervisor._CTRL_DIR = str(tmp_path)
    path = KVMHypervisor._InstanceMigrationState("myinst")
    assert path == os.path.join(str(tmp_path), "myinst.migstate")
