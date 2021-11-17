#
#

# Copyright (C) 2006, 2007, 2008, 2013 Google Inc.
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


"""Docker hypervisor

"""

import os
import os.path
import logging
import docker

from docker.types import Mount,DriverConfig
from ganeti import utils
from ganeti import constants
from ganeti import objects
from ganeti.hypervisor import hv_base


class DockerHypervisor(hv_base.BaseHypervisor):
  """Docker hypervisor interface.

  Implementation of the Docker container runtime for Ganeti.

  """

  PARAMETERS =  {
    constants.HV_DOCKER_IMAGE: hv_base.NO_CHECK,
    constants.HV_DOCKER_TAG: hv_base.NO_CHECK
  }


  def __init__(self):
    hv_base.BaseHypervisor.__init__(self)
    self.docker = docker.from_env()

  def ListInstances(self, hvparams=None):
    """Get the list of running instances.

    """
    names = []
    for container in self.docker.containers.list():
      names.append(container.attrs['Name'][1:])
    return names

  def GetInstanceInfo(self, instance_name, hvparams=None):
    """Get instance properties.

    @type instance_name: string
    @param instance_name: the instance name
    @type hvparams: dict of strings
    @param hvparams: hvparams to be used with this instance

    @return: tuple of (name, id, memory, vcpus, stat, times)

    """

    memory = 0
    id = -1
    vcpus = 0

    try:
      container = self.docker.containers.get(instance_name)
    except docker.errors.NotFound as e:
      return None

    if container.attrs['State']['Running']:
      id = container.attrs['Id']
      state = hv_base.HvInstanceState.RUNNING
      memory = int(container.attrs['HostConfig']['Memory'] / 1024 / 1024)
      cpu_period = container.attrs['HostConfig']['CpuPeriod']
      cpu_quota = container.attrs['HostConfig']['CpuQuota']

      if cpu_period > 0:
        vcpus = int(cpu_quota / cpu_period)

    else:
      return None

    return (instance_name, id, memory, vcpus, state, 0)

  def GetAllInstancesInfo(self, hvparams=None):
    """Get properties of all instances.

    @type hvparams: dict of strings
    @param hvparams: hypervisor parameter
    @return: list of tuples (name, id, memory, vcpus, stat, times)

    """
    data = []
    for container in self.docker.containers.list():
      data.append(self.GetInstanceInfo(container.attrs['Name'][1:]))
    return data

  def StartInstance(self, instance, block_devices, startup_paused):
    """Start an instance.

    For the fake hypervisor, it just creates a file in the base dir,
    creating an exception if it already exists. We don't actually
    handle race conditions properly, since these are *FAKE* instances.

    """
    hvp = instance.hvparams
    be = instance.beparams

    memory_in_mb = "%dM" % be[constants.BE_MAXMEM]

    cpu_period = 100000
    cpu_quota = be[constants.BE_VCPUS] * 100000
    
    volumes = []
    for bdev in block_devices:
      path = bdev[1]
      mountpoint = "/root"
      volumes.append(Mount(mountpoint, path, type='volume',
                           read_only=False,
                           driver_config=DriverConfig(
                             'local',
                             {'type': 'ext4', 'device': path, })))

    logging.debug("Pulling image '%s:%s'" %(hvp[constants.HV_DOCKER_IMAGE],
                                           hvp[constants.HV_DOCKER_TAG]))
    self.docker.images.pull("%s:%s" % (hvp[constants.HV_DOCKER_IMAGE],
                                       hvp[constants.HV_DOCKER_TAG]))
    self.docker.containers.run("%s:%s" % (hvp[constants.HV_DOCKER_IMAGE],
                                          hvp[constants.HV_DOCKER_TAG]),
                               detach=True, name=instance.name, remove=True,
                               mem_limit=memory_in_mb,
                               cpu_period=cpu_period, cpu_quota=cpu_quota,
                               mounts=volumes)


  def StopInstance(self, instance, force=False, retry=False, name=None,
                   timeout=None):
    """Stop an instance.

    """
    if name is None:
      instance_name = instance.name
    else:
      instance_name = name

    container = self.docker.containers.get(instance_name)

    if container.attrs['State']['Running']:
      container.stop()


  def RebootInstance(self, instance):
    """Reboot an instance.

    """
    container = self.docker.containers.get(instance.name)
    
    container.restart()

  def GetNodeInfo(self, hvparams=None):
    """Return information about the node.

    See L{BaseHypervisor.GetLinuxNodeInfo}.

    """
    result = self.GetLinuxNodeInfo()
    # substract running instances
    all_instances = self.GetAllInstancesInfo()
    result["memory_free"] -= min(result["memory_free"],
                                 sum([row[2] for row in all_instances]))
    return result

  @classmethod
  def GetInstanceConsole(cls, instance, primary_node, node_group,
                         hvparams, beparams):
    """Return information for connecting to the console of an instance.

    """
    return objects.InstanceConsole(instance=instance.name,
                                   kind=constants.CONS_MESSAGE,
                                   message=("Console not available for fake"
                                            " hypervisor"))


  def Verify(self, hvparams=None):
    """Verify the hypervisor.

    Verify that the docker environment is available/functional

    @type hvparams: dict of strings
    @param hvparams: hypervisor parameters to be verified against

    @return: Problem description if something is wrong, C{None} otherwise

    """

    msgs = []
    try:
      self.docker.ping()
    except docker.errors.APIError as e:
      msgs.append("The docker daemon is not responding: %s", e)

    return self._FormatVerifyResults(msgs)
