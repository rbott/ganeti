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

"""Tests for cfgupgrade downgrade of new KVM migration parameters."""

import copy

import pytest

from ganeti import constants
from ganeti.tools.cfgupgrade import CfgUpgrade


def _make_upgrade(config_data):
  """Build a minimal CfgUpgrade for testing downgrade methods."""
  obj = CfgUpgrade.__new__(CfgUpgrade)
  obj.config_data = config_data
  obj.errors = []
  return obj


class TestDowngradeNewKvmMigrationParameters:

  def test_strips_from_cluster_hvparams(self):
    """Downgrade strips both keys from cluster hvparams."""
    config = {
      "cluster": {
        "hvparams": {
          constants.HT_KVM: {
            "migration_downtime": 30,
            "migration_downtime_max": 500,
            "migration_cancel_threshold": 400,
          },
        },
      },
      "instances": {},
      "nodegroups": {},
    }
    up = _make_upgrade(config)
    up.DowngradeNewKvmMigrationParameters()
    kvm_hvp = config["cluster"]["hvparams"][constants.HT_KVM]
    assert "migration_downtime_max" not in kvm_hvp
    assert "migration_cancel_threshold" not in kvm_hvp
    assert kvm_hvp["migration_downtime"] == 30

  def test_strips_from_instance_hvparams(self):
    """Downgrade strips both keys from per-instance hvparams."""
    config = {
      "cluster": {"hvparams": {}},
      "instances": {
        "inst-uuid": {
          "hvparams": {
            "migration_downtime_max": 1000,
            "migration_cancel_threshold": 200,
          },
        },
      },
      "nodegroups": {},
    }
    up = _make_upgrade(config)
    up.DowngradeNewKvmMigrationParameters()
    hvp = config["instances"]["inst-uuid"]["hvparams"]
    assert "migration_downtime_max" not in hvp
    assert "migration_cancel_threshold" not in hvp

  def test_noop_when_absent(self):
    """Downgrade is a no-op when neither key is present."""
    config = {
      "cluster": {
        "hvparams": {
          constants.HT_KVM: {
            "migration_downtime": 30,
          },
        },
      },
      "instances": {},
      "nodegroups": {},
    }
    original = copy.deepcopy(config)
    up = _make_upgrade(config)
    up.DowngradeNewKvmMigrationParameters()
    assert config == original

  def test_partial_keys_removed(self):
    """Only one of the two keys present -- it still gets removed."""
    config = {
      "cluster": {
        "hvparams": {
          constants.HT_KVM: {
            "migration_downtime_max": 500,
          },
        },
      },
      "instances": {},
      "nodegroups": {},
    }
    up = _make_upgrade(config)
    up.DowngradeNewKvmMigrationParameters()
    kvm_hvp = config["cluster"]["hvparams"][constants.HT_KVM]
    assert "migration_downtime_max" not in kvm_hvp

  def test_strips_from_nodegroups(self):
    """Downgrade strips from nodegroup hvparams defensively."""
    config = {
      "cluster": {"hvparams": {}},
      "instances": {},
      "nodegroups": {
        "grp-uuid": {
          "hvparams": {
            constants.HT_KVM: {
              "migration_downtime_max": 500,
              "migration_cancel_threshold": 300,
            },
          },
        },
      },
    }
    up = _make_upgrade(config)
    up.DowngradeNewKvmMigrationParameters()
    kvm_hvp = config["nodegroups"]["grp-uuid"]["hvparams"][constants.HT_KVM]
    assert "migration_downtime_max" not in kvm_hvp
    assert "migration_cancel_threshold" not in kvm_hvp
