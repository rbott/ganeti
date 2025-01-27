#
#

# Copyright (C) 2025, Ganeti Project
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


"""Abstraction for the microvm machine type of Qemu

"""

import os
import re
import logging


from ganeti import constants
from ganeti import errors
from ganeti import objects
from ganeti import utils
from ganeti import pathutils
from ganeti.hypervisor import hv_base

class KVMMicroHypervisor(hv_base.BaseHypervisor):
  """microvm implementation for Ganeti

  """

  _ROOT_DIR = pathutils.RUN_DIR + "/kvmmicro-hypervisor"
  _PIDS_DIR = _ROOT_DIR + "/pid"
  _CONF_DIR = _ROOT_DIR + "/conf"
  _CTRL_DIR = _ROOT_DIR + "/ctrl"
  _DIRS = [_ROOT_DIR, _PIDS_DIR, _CONF_DIR, _CTRL_DIR]

  # Supported kvm options to get output from
  _KVMOPT_HELP = "help"

  # Command to execute to get the output from kvm, and whether to
  # accept the output even on failure.
  _KVMOPTS_CMDS = {
    _KVMOPT_HELP: (["--help"], False),
  }

  _VERSION_RE = re.compile(r"\b(\d+)\.(\d+)(\.(\d+))?\b")

  PARAMETERS = {
    constants.HV_KVM_MICRO_PATH: hv_base.REQ_FILE_CHECK,
    constants.HV_KERNEL_PATH: hv_base.OPT_FILE_CHECK,
    constants.HV_INITRD_PATH: hv_base.OPT_FILE_CHECK,
    constants.HV_ROOT_PATH: hv_base.NO_CHECK,
    constants.HV_KERNEL_ARGS: hv_base.NO_CHECK,
    constants.HV_ACPI: hv_base.NO_CHECK,
    constants.HV_KVM_MICRO_SERIAL_TYPE: hv_base.NO_CHECK
  }

  @classmethod
  def _InstancePidFile(cls, instance_name):
    """Returns the instance pidfile.

    """
    return utils.PathJoin(cls._PIDS_DIR, instance_name)

  @classmethod
  def _InstancePidInfo(cls, pid):
    """Check pid file for instance information.

    Check that a pid file is associated with an instance, and retrieve
    information from its command line.

    @type pid: string or int
    @param pid: process id of the instance to check
    @rtype: tuple
    @return: (instance_name, memory, vcpus)
    @raise errors.HypervisorError: when an instance cannot be found

    """
    alive = utils.IsProcessAlive(pid)
    if not alive:
      raise errors.HypervisorError("Cannot get info for pid %s" % pid)

    cmdline_file = utils.PathJoin("/proc", str(pid), "cmdline")
    try:
      cmdline = utils.ReadFile(cmdline_file)
    except EnvironmentError as err:
      raise errors.HypervisorError("Can't open cmdline file for pid %s: %s" %
                                   (pid, err))

    instance = None
    memory = 0
    vcpus = 0

    arg_list = cmdline.split("\x00")
    while arg_list:
      arg = arg_list.pop(0)
      if arg == "-name":
        instance = arg_list.pop(0).split(",")[0]
      elif arg == "-m":
        memory = int(arg_list.pop(0))
      elif arg == "-smp":
        vcpus = int(arg_list.pop(0).split(",")[0])

    if instance is None:
      raise errors.HypervisorError("Pid %s doesn't contain a ganeti kvm"
                                   " instance" % pid)

    return (instance, memory, vcpus)

  @classmethod
  def _InstancePidAlive(cls, instance_name):
    """Returns the instance pidfile, pid, and liveness.

    @type instance_name: string
    @param instance_name: instance name
    @rtype: tuple
    @return: (pid file name, pid, liveness)

    """
    pidfile = cls._InstancePidFile(instance_name)
    pid = utils.ReadPidFile(pidfile)

    alive = False
    try:
      cmd_instance = cls._InstancePidInfo(pid)[0]
      alive = (cmd_instance == instance_name)
    except errors.HypervisorError:
      pass

    return (pidfile, pid, alive)

  def __init__(self):
    hv_base.BaseHypervisor.__init__(self)
    # Let's make sure the directories we need exist, even if the RUN_DIR lives
    # in a tmpfs filesystem or has been otherwise wiped out.
    dirs = [(dname, constants.RUN_DIRS_MODE) for dname in self._DIRS]
    utils.EnsureDirs(dirs)


  def StartInstance(self, instance, block_devices, startup_paused):
    """Start an instance.

    @type instance: L{objects.Instance}
    @param instance: instance to start
    @type block_devices: list of tuples (disk_object, link_name, drive_uri)
    @param block_devices: blockdevices assigned to this instance
    @type startup_paused: bool
    @param startup_paused: if instance should be paused at startup
    """

    hvp = instance.hvparams

    kvmpath = hvp[constants.HV_KVM_MICRO_PATH]
    pidfile = self._InstancePidFile(instance.name)

    """
    -device virtio-blk-pci,id=disk-07883d63-d0cb-41cb,bus=pci.0,addr=0xc,drive=disk-07883d63-d0cb-41cb,write-cache=on
    -blockdev driver=raw,node-name=disk-07883d63-d0cb-41cb,discard=ignore,cache.direct=on,cache.no-flush=off,file.driver=host_device,file.filename=/var/run/ganeti/instance-disks/kvm-test-instance02.staging.ganeti.org:0,file.aio=threads,auto-read-only=off
    """

    """
    -t file
    disks = [
      (
        {
          'dev_type': 'file',
          'logical_id': ('loop', '/ganeti-disks/kvm-test-instance2/c6960994-b876-4ee2-b4b5-0b2ba1d213c4.file.disk0'),
          'children': [],
          'nodes': ['d253a43f-f52e-463e-b538-3b13d4287a77'],
          'iv_name': 'disk/0',
          'size': 2560,
          'mode': 'rw',
          'params': {},
          'serial_no': 1,
          'uuid': '2ea16b4a-a2d6-48ee-aaf0-31a3e4a64d29',
          'ctime': 1738104030.8579557,
          'mtime': 1738104030.8579545
        },
        '/var/run/ganeti/instance-disks/kvm-test-instance2:0',
        None)
    ]
    """

    """
    -t plain
    disks = [
      (
        {
          'dev_type': 'plain',
          'logical_id': ('gnt', '4006bd9f-3e81-4e09-97c9-caa97b3c8dfc.disk0'),
          'children': [],
          'nodes': ['d253a43f-f52e-463e-b538-3b13d4287a77'],
          'iv_name': 'disk/0',
          'size': 2560,
          'mode': 'rw',
          'params': {'stripes': 1},
          'serial_no': 1,
          'uuid': 'dd4c431c-6474-4506-9840-39a76af6e6a6',
          'ctime': 1738104117.3526156,
          'mtime': 1738104117.3526125
        },
        '/var/run/ganeti/instance-disks/kvm-test-instance2:0',
        None
      )
    ]
    """

    kvm_cmd = []
    kvm_cmd.append(kvmpath)
    kvm_cmd.extend(["-name", instance.name])
    kvm_cmd.extend(["-pidfile", pidfile])
    machine_opt_list = [
      "x-option-roms=off",
      "pit=off",
      "pic=off",
      "rtc=off"
    ]
    if hvp[constants.HV_ACPI]:
      machine_opt_list.append("acpi=on")
    else:
      machine_opt_list.append("acpi=off")

    kvm_cmd.extend(["-cpu", "host"])
    kvm_cmd.extend(["-m", instance.beparams[constants.BE_MAXMEM]])
    kvm_cmd.extend(["-smp", instance.beparams[constants.BE_VCPUS]])

    serial_dev = ("socket,path=%s,server=on,wait=off,id=char0" %
                  self._InstanceSerial(instance.name))
    if hvp[constants.HV_KVM_MICRO_SERIAL_TYPE] == constants.HT_KVM_MICRO_SERIAL_VIRT_CONSOLE:
      machine_opt_list.append("isa-serial=off")
      kvm_cmd.extend(["-device", "virtio-serial-device",
                      "-device", "virtconsole,chardev=char0,name=console0",
                      "-chardev", serial_dev])
      append_params = "earlyprintk=hvc0 console=hvc0"
    else:
      kvm_cmd.extend(["-device", "isa-serial,chardev=char0",
                      "-chardev", serial_dev])
      append_params = "earlyprintk=ttyS0 console=ttyS0"

    machine_opts = "microvm," + ",".join(machine_opt_list)
    kvm_cmd.extend(["-M", machine_opts])

    kvm_cmd.extend(["-kernel", hvp[constants.HV_KERNEL_PATH]])
    kvm_cmd.extend(["-initrd", hvp[constants.HV_INITRD_PATH]])
    if hvp[constants.HV_KERNEL_ARGS]:
      append_params += " " + hvp[constants.HV_KERNEL_ARGS]
    kvm_cmd.extend(["-append", append_params])

    kvm_cmd.extend(["-daemonize", "-nodefaults", "-no-user-config", "-nographic"])
    qemu_logfile = utils.PathJoin(pathutils.LOG_KVM_DIR,
                                  "%s.log" % instance.name)
    kvm_cmd.extend(["-D", qemu_logfile])

    result = utils.RunCmd(kvm_cmd)

    if result.failed:
      raise errors.HypervisorError("Failed to start instance %s: %s (%s)" %
                                   (instance.name, result.fail_reason, result.output))

  def StopInstance(self, instance, force=False, retry=False, name=None,
                   timeout=None):
    """Stop an instance

    @type instance: L{objects.Instance}
    @param instance: instance to stop
    @type force: boolean
    @param force: whether to do a "hard" stop (destroy)
    @type retry: boolean
    @param retry: whether this is just a retry call
    @type name: string or None
    @param name: if this parameter is passed, the the instance object
        should not be used (will be passed as None), and the shutdown
        must be done by name only
    @type timeout: int or None
    @param timeout: if the parameter is not None, a soft shutdown operation will
        be killed after the specified number of seconds. A hard (forced)
        shutdown cannot have a timeout
    @raise errors.HypervisorError: when a parameter is not valid or
        the instance failed to be stopped

    """
    _, pid, alive = self._InstancePidAlive(instance.name)
    if pid > 0 and alive:
      utils.KillProcess(pid)

  def ListInstances(self, hvparams=None):
    """Get the list of running instances.

    We can do this by listing our live instances directory and
    checking whether the associated kvm process is still alive.

    """
    result = []
    for name in os.listdir(self._PIDS_DIR):
      if self._InstancePidAlive(name)[2]:
        result.append(name)
    return result

  def GetInstanceInfo(self, instance_name, hvparams=None):
    """Get instance properties.

    @type instance_name: string
    @param instance_name: the instance name
    @type hvparams: dict of strings
    @param hvparams: hypervisor parameters to be used with this instance
    @rtype: tuple of strings
    @return: (name, id, memory, vcpus, stat, times)

    """
    _, pid, alive = self._InstancePidAlive(instance_name)
    if not alive:
        return None

    _, memory, vcpus = self._InstancePidInfo(pid)
    istat = hv_base.HvInstanceState.RUNNING
    times = 0

    return (instance_name, pid, memory, vcpus, istat, times)

  def GetAllInstancesInfo(self, hvparams=None):
    """Get properties of all instances.

    @type hvparams: dict of strings
    @param hvparams: hypervisor parameter

    @rtype: (string, string, int, int, HvInstanceState, int)
    @return: list of tuples (name, id, memory, vcpus, state, times)

    """
    data = []
    for name in os.listdir(self._PIDS_DIR):
      try:
        info = self.GetInstanceInfo(name)
      except errors.HypervisorError:
        # Ignore exceptions due to instances being shut down
        continue
      if info:
        data.append(info)
    return data

  @classmethod
  def _ParseKVMVersion(cls, text):
    """Parse the KVM version from the --help output.

    @type text: string
    @param text: output of kvm --help
    @return: (version, v_maj, v_min, v_rev)
    @raise errors.HypervisorError: when the KVM version cannot be retrieved

    """
    match = cls._VERSION_RE.search(text.splitlines()[0])
    if not match:
      raise errors.HypervisorError("Unable to get KVM version")

    v_all = match.group(0)
    v_maj = int(match.group(1))
    v_min = int(match.group(2))
    if match.group(4):
      v_rev = int(match.group(4))
    else:
      v_rev = 0
    return (v_all, v_maj, v_min, v_rev)

  @classmethod
  def _GetKVMOutput(cls, kvm_path, option):
    """Return the output of a kvm invocation

    @type kvm_path: string
    @param kvm_path: path to the kvm executable
    @type option: a key of _KVMOPTS_CMDS
    @param option: kvm option to fetch the output from
    @return: output a supported kvm invocation
    @raise errors.HypervisorError: when the KVM help output cannot be retrieved

    """
    assert option in cls._KVMOPTS_CMDS, "Invalid output option"

    optlist, can_fail = cls._KVMOPTS_CMDS[option]

    result = utils.RunCmd([kvm_path] + optlist)
    if result.failed and not can_fail:
      raise errors.HypervisorError("Unable to get KVM %s output" %
                                   " ".join(optlist))
    return result.output

  @classmethod
  def _GetKVMVersion(cls, kvm_path):
    """Return the installed KVM version.

    @return: (version, v_maj, v_min, v_rev)
    @raise errors.HypervisorError: when the KVM version cannot be retrieved

    """
    return cls._ParseKVMVersion(cls._GetKVMOutput(kvm_path, cls._KVMOPT_HELP))

  def GetNodeInfo(self, hvparams=None):
    """Return information about the node.

    @type hvparams: dict of strings
    @param hvparams: hypervisor parameters

    @return: a dict with at least the following keys (memory values in MiB):
          - memory_total: the total memory size on the node
          - memory_free: the available memory on the node for instances
          - memory_dom0: the memory used by the node itself, if available
          - cpu_total: total number of CPUs
          - cpu_dom0: number of CPUs used by the node OS
          - cpu_nodes: number of NUMA domains
          - cpu_sockets: number of physical CPU sockets

    """
    result = self.GetLinuxNodeInfo()
    kvmpath = constants.KVM_PATH
    if hvparams is not None:
      kvmpath = hvparams.get(constants.HV_KVM_MICRO_PATH, constants.KVM_PATH)
    _, v_major, v_min, v_rev = self._GetKVMVersion(kvmpath)
    result[constants.HV_NODEINFO_KEY_VERSION] = (v_major, v_min, v_rev)
    return result

  @classmethod
  def _InstanceMonitor(cls, instance_name):
    """Returns the instance monitor socket name

    """
    return utils.PathJoin(cls._CTRL_DIR, "%s.monitor" % instance_name)

  @staticmethod
  def _SocatUnixConsoleParams():
    """Returns the correct parameters for socat

    If we have a new-enough socat we can use raw mode with an escape character.

    """
    if constants.SOCAT_USE_ESCAPE:
      return "raw,echo=0,escape=%s" % constants.SOCAT_ESCAPE_CODE
    else:
      return "echo=0,icanon=0"

  @classmethod
  def _InstanceSerial(cls, instance_name):
    """Returns the instance serial socket name

    """
    return utils.PathJoin(cls._CTRL_DIR, "%s.serial" % instance_name)

  @classmethod
  def GetInstanceConsole(cls, instance, primary_node, node_group,
                         hvparams, beparams):
    """Return information for connecting to the console of an instance.

    """
    cmd = [pathutils.KVM_CONSOLE_WRAPPER,
           constants.SOCAT_PATH, utils.ShellQuote(instance.name),
           utils.ShellQuote(cls._InstanceMonitor(instance.name)),
           "STDIO,%s" % cls._SocatUnixConsoleParams(),
           "UNIX-CONNECT:%s" % cls._InstanceSerial(instance.name)]
    ndparams = node_group.FillND(primary_node)
    return objects.InstanceConsole(instance=instance.name,
                                   kind=constants.CONS_SSH,
                                   host=primary_node.name,
                                   port=ndparams.get(constants.ND_SSH_PORT),
                                   user=constants.SSH_CONSOLE_USER,
                                   command=cmd)



