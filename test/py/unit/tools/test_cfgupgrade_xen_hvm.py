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

"""Tests for the xen-hvm deprecation helpers in lib/tools/cfgupgrade.py."""

import pytest

from ganeti.tools.cfgupgrade import CfgUpgrade
from ganeti import constants


LEGACY_KERNEL_PATHS = [
  "/usr/lib/xen/boot/hvmloader",
  "/usr/lib/xen-default/boot/hvmloader",
]

LEGACY_DEVICE_MODELS = [
  "/usr/lib/xen/bin/qemu-dm",
  "/usr/lib/xen-default/bin/qemu-dm",
]


@pytest.mark.parametrize("path", LEGACY_KERNEL_PATHS)
def test_reset_legacy_kernel_path(path):
  hvp = {constants.HV_KERNEL_PATH: path}
  CfgUpgrade._ResetDeprecatedXenHvmParams(hvp, scope="cluster")
  assert hvp[constants.HV_KERNEL_PATH] == ""


@pytest.mark.parametrize("path", LEGACY_DEVICE_MODELS)
def test_reset_legacy_device_model(path):
  hvp = {constants.HV_DEVICE_MODEL: path}
  CfgUpgrade._ResetDeprecatedXenHvmParams(hvp, scope="cluster")
  assert hvp[constants.HV_DEVICE_MODEL] == ""


def test_custom_kernel_path_preserved():
  custom = "/opt/xen/boot/custom-hvmloader"
  hvp = {constants.HV_KERNEL_PATH: custom}
  CfgUpgrade._ResetDeprecatedXenHvmParams(hvp, scope="cluster")
  assert hvp[constants.HV_KERNEL_PATH] == custom


def test_custom_device_model_preserved():
  custom = "/usr/bin/qemu-system-x86_64"
  hvp = {constants.HV_DEVICE_MODEL: custom}
  CfgUpgrade._ResetDeprecatedXenHvmParams(hvp, scope="cluster")
  assert hvp[constants.HV_DEVICE_MODEL] == custom


def test_empty_values_unchanged():
  hvp = {constants.HV_KERNEL_PATH: "", constants.HV_DEVICE_MODEL: ""}
  CfgUpgrade._ResetDeprecatedXenHvmParams(hvp, scope="cluster")
  assert hvp[constants.HV_KERNEL_PATH] == ""
  assert hvp[constants.HV_DEVICE_MODEL] == ""


def test_missing_keys_no_error():
  hvp = {}
  CfgUpgrade._ResetDeprecatedXenHvmParams(hvp, scope="cluster")
  assert hvp == {}


def test_both_legacy_defaults_reset_together():
  hvp = {
    constants.HV_KERNEL_PATH: "/usr/lib/xen/boot/hvmloader",
    constants.HV_DEVICE_MODEL: "/usr/lib/xen/bin/qemu-dm",
  }
  CfgUpgrade._ResetDeprecatedXenHvmParams(hvp, scope="instance test.example")
  assert hvp[constants.HV_KERNEL_PATH] == ""
  assert hvp[constants.HV_DEVICE_MODEL] == ""


def test_one_legacy_one_custom():
  hvp = {
    constants.HV_KERNEL_PATH: "/usr/lib/xen/boot/hvmloader",
    constants.HV_DEVICE_MODEL: "/usr/bin/qemu-system-x86_64",
  }
  CfgUpgrade._ResetDeprecatedXenHvmParams(hvp, scope="cluster")
  assert hvp[constants.HV_KERNEL_PATH] == ""
  assert hvp[constants.HV_DEVICE_MODEL] == "/usr/bin/qemu-system-x86_64"
