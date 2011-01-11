#
#

# Copyright (C) 2006, 2007, 2010, 2011 Google Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA.


"""Ganeti utility module.

This module holds functions that can be used in both daemons (all) and
the command line scripts.

"""


import os
import sys
import time
import subprocess
import re
import socket
import tempfile
import shutil
import errno
import pwd
import itertools
import select
import fcntl
import resource
import logging
import signal
import datetime
import calendar

from cStringIO import StringIO

from ganeti import errors
from ganeti import constants
from ganeti import compat

from ganeti.utils.algo import * # pylint: disable-msg=W0401
from ganeti.utils.retry import * # pylint: disable-msg=W0401
from ganeti.utils.text import * # pylint: disable-msg=W0401
from ganeti.utils.mlock import * # pylint: disable-msg=W0401
from ganeti.utils.log import * # pylint: disable-msg=W0401
from ganeti.utils.hash import * # pylint: disable-msg=W0401
from ganeti.utils.wrapper import * # pylint: disable-msg=W0401
from ganeti.utils.filelock import * # pylint: disable-msg=W0401
from ganeti.utils.io import * # pylint: disable-msg=W0401
from ganeti.utils.x509 import * # pylint: disable-msg=W0401
from ganeti.utils.nodesetup import * # pylint: disable-msg=W0401
from ganeti.utils.process import * # pylint: disable-msg=W0401


_RANDOM_UUID_FILE = "/proc/sys/kernel/random/uuid"

_VALID_SERVICE_NAME_RE = re.compile("^[-_.a-zA-Z0-9]{1,128}$")

UUID_RE = re.compile('^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-'
                     '[a-f0-9]{4}-[a-f0-9]{12}$')


def ForceDictType(target, key_types, allowed_values=None):
  """Force the values of a dict to have certain types.

  @type target: dict
  @param target: the dict to update
  @type key_types: dict
  @param key_types: dict mapping target dict keys to types
                    in constants.ENFORCEABLE_TYPES
  @type allowed_values: list
  @keyword allowed_values: list of specially allowed values

  """
  if allowed_values is None:
    allowed_values = []

  if not isinstance(target, dict):
    msg = "Expected dictionary, got '%s'" % target
    raise errors.TypeEnforcementError(msg)

  for key in target:
    if key not in key_types:
      msg = "Unknown key '%s'" % key
      raise errors.TypeEnforcementError(msg)

    if target[key] in allowed_values:
      continue

    ktype = key_types[key]
    if ktype not in constants.ENFORCEABLE_TYPES:
      msg = "'%s' has non-enforceable type %s" % (key, ktype)
      raise errors.ProgrammerError(msg)

    if ktype in (constants.VTYPE_STRING, constants.VTYPE_MAYBE_STRING):
      if target[key] is None and ktype == constants.VTYPE_MAYBE_STRING:
        pass
      elif not isinstance(target[key], basestring):
        if isinstance(target[key], bool) and not target[key]:
          target[key] = ''
        else:
          msg = "'%s' (value %s) is not a valid string" % (key, target[key])
          raise errors.TypeEnforcementError(msg)
    elif ktype == constants.VTYPE_BOOL:
      if isinstance(target[key], basestring) and target[key]:
        if target[key].lower() == constants.VALUE_FALSE:
          target[key] = False
        elif target[key].lower() == constants.VALUE_TRUE:
          target[key] = True
        else:
          msg = "'%s' (value %s) is not a valid boolean" % (key, target[key])
          raise errors.TypeEnforcementError(msg)
      elif target[key]:
        target[key] = True
      else:
        target[key] = False
    elif ktype == constants.VTYPE_SIZE:
      try:
        target[key] = ParseUnit(target[key])
      except errors.UnitParseError, err:
        msg = "'%s' (value %s) is not a valid size. error: %s" % \
              (key, target[key], err)
        raise errors.TypeEnforcementError(msg)
    elif ktype == constants.VTYPE_INT:
      try:
        target[key] = int(target[key])
      except (ValueError, TypeError):
        msg = "'%s' (value %s) is not a valid integer" % (key, target[key])
        raise errors.TypeEnforcementError(msg)


def ValidateServiceName(name):
  """Validate the given service name.

  @type name: number or string
  @param name: Service name or port specification

  """
  try:
    numport = int(name)
  except (ValueError, TypeError):
    # Non-numeric service name
    valid = _VALID_SERVICE_NAME_RE.match(name)
  else:
    # Numeric port (protocols other than TCP or UDP might need adjustments
    # here)
    valid = (numport >= 0 and numport < (1 << 16))

  if not valid:
    raise errors.OpPrereqError("Invalid service name '%s'" % name,
                               errors.ECODE_INVAL)

  return name


def ListVolumeGroups():
  """List volume groups and their size

  @rtype: dict
  @return:
       Dictionary with keys volume name and values
       the size of the volume

  """
  command = "vgs --noheadings --units m --nosuffix -o name,size"
  result = RunCmd(command)
  retval = {}
  if result.failed:
    return retval

  for line in result.stdout.splitlines():
    try:
      name, size = line.split()
      size = int(float(size))
    except (IndexError, ValueError), err:
      logging.error("Invalid output from vgs (%s): %s", err, line)
      continue

    retval[name] = size

  return retval


def BridgeExists(bridge):
  """Check whether the given bridge exists in the system

  @type bridge: str
  @param bridge: the bridge name to check
  @rtype: boolean
  @return: True if it does

  """
  return os.path.isdir("/sys/class/net/%s/bridge" % bridge)


def TryConvert(fn, val):
  """Try to convert a value ignoring errors.

  This function tries to apply function I{fn} to I{val}. If no
  C{ValueError} or C{TypeError} exceptions are raised, it will return
  the result, else it will return the original value. Any other
  exceptions are propagated to the caller.

  @type fn: callable
  @param fn: function to apply to the value
  @param val: the value to be converted
  @return: The converted value if the conversion was successful,
      otherwise the original value.

  """
  try:
    nv = fn(val)
  except (ValueError, TypeError):
    nv = val
  return nv


def ParseCpuMask(cpu_mask):
  """Parse a CPU mask definition and return the list of CPU IDs.

  CPU mask format: comma-separated list of CPU IDs
  or dash-separated ID ranges
  Example: "0-2,5" -> "0,1,2,5"

  @type cpu_mask: str
  @param cpu_mask: CPU mask definition
  @rtype: list of int
  @return: list of CPU IDs

  """
  if not cpu_mask:
    return []
  cpu_list = []
  for range_def in cpu_mask.split(","):
    boundaries = range_def.split("-")
    n_elements = len(boundaries)
    if n_elements > 2:
      raise errors.ParseError("Invalid CPU ID range definition"
                              " (only one hyphen allowed): %s" % range_def)
    try:
      lower = int(boundaries[0])
    except (ValueError, TypeError), err:
      raise errors.ParseError("Invalid CPU ID value for lower boundary of"
                              " CPU ID range: %s" % str(err))
    try:
      higher = int(boundaries[-1])
    except (ValueError, TypeError), err:
      raise errors.ParseError("Invalid CPU ID value for higher boundary of"
                              " CPU ID range: %s" % str(err))
    if lower > higher:
      raise errors.ParseError("Invalid CPU ID range definition"
                              " (%d > %d): %s" % (lower, higher, range_def))
    cpu_list.extend(range(lower, higher + 1))
  return cpu_list


def GetHomeDir(user, default=None):
  """Try to get the homedir of the given user.

  The user can be passed either as a string (denoting the name) or as
  an integer (denoting the user id). If the user is not found, the
  'default' argument is returned, which defaults to None.

  """
  try:
    if isinstance(user, basestring):
      result = pwd.getpwnam(user)
    elif isinstance(user, (int, long)):
      result = pwd.getpwuid(user)
    else:
      raise errors.ProgrammerError("Invalid type passed to GetHomeDir (%s)" %
                                   type(user))
  except KeyError:
    return default
  return result.pw_dir


def NewUUID():
  """Returns a random UUID.

  @note: This is a Linux-specific method as it uses the /proc
      filesystem.
  @rtype: str

  """
  return ReadFile(_RANDOM_UUID_FILE, size=128).rstrip("\n")


def FirstFree(seq, base=0):
  """Returns the first non-existing integer from seq.

  The seq argument should be a sorted list of positive integers. The
  first time the index of an element is smaller than the element
  value, the index will be returned.

  The base argument is used to start at a different offset,
  i.e. C{[3, 4, 6]} with I{offset=3} will return 5.

  Example: C{[0, 1, 3]} will return I{2}.

  @type seq: sequence
  @param seq: the sequence to be analyzed.
  @type base: int
  @param base: use this value as the base index of the sequence
  @rtype: int
  @return: the first non-used index in the sequence

  """
  for idx, elem in enumerate(seq):
    assert elem >= base, "Passed element is higher than base offset"
    if elem > idx + base:
      # idx is not used
      return idx + base
  return None


def SingleWaitForFdCondition(fdobj, event, timeout):
  """Waits for a condition to occur on the socket.

  Immediately returns at the first interruption.

  @type fdobj: integer or object supporting a fileno() method
  @param fdobj: entity to wait for events on
  @type event: integer
  @param event: ORed condition (see select module)
  @type timeout: float or None
  @param timeout: Timeout in seconds
  @rtype: int or None
  @return: None for timeout, otherwise occured conditions

  """
  check = (event | select.POLLPRI |
           select.POLLNVAL | select.POLLHUP | select.POLLERR)

  if timeout is not None:
    # Poller object expects milliseconds
    timeout *= 1000

  poller = select.poll()
  poller.register(fdobj, event)
  try:
    # TODO: If the main thread receives a signal and we have no timeout, we
    # could wait forever. This should check a global "quit" flag or something
    # every so often.
    io_events = poller.poll(timeout)
  except select.error, err:
    if err[0] != errno.EINTR:
      raise
    io_events = []
  if io_events and io_events[0][1] & check:
    return io_events[0][1]
  else:
    return None


class FdConditionWaiterHelper(object):
  """Retry helper for WaitForFdCondition.

  This class contains the retried and wait functions that make sure
  WaitForFdCondition can continue waiting until the timeout is actually
  expired.

  """

  def __init__(self, timeout):
    self.timeout = timeout

  def Poll(self, fdobj, event):
    result = SingleWaitForFdCondition(fdobj, event, self.timeout)
    if result is None:
      raise RetryAgain()
    else:
      return result

  def UpdateTimeout(self, timeout):
    self.timeout = timeout


def WaitForFdCondition(fdobj, event, timeout):
  """Waits for a condition to occur on the socket.

  Retries until the timeout is expired, even if interrupted.

  @type fdobj: integer or object supporting a fileno() method
  @param fdobj: entity to wait for events on
  @type event: integer
  @param event: ORed condition (see select module)
  @type timeout: float or None
  @param timeout: Timeout in seconds
  @rtype: int or None
  @return: None for timeout, otherwise occured conditions

  """
  if timeout is not None:
    retrywaiter = FdConditionWaiterHelper(timeout)
    try:
      result = Retry(retrywaiter.Poll, RETRY_REMAINING_TIME, timeout,
                     args=(fdobj, event), wait_fn=retrywaiter.UpdateTimeout)
    except RetryTimeout:
      result = None
  else:
    result = None
    while result is None:
      result = SingleWaitForFdCondition(fdobj, event, timeout)
  return result


def EnsureDaemon(name):
  """Check for and start daemon if not alive.

  """
  result = RunCmd([constants.DAEMON_UTIL, "check-and-start", name])
  if result.failed:
    logging.error("Can't start daemon '%s', failure %s, output: %s",
                  name, result.fail_reason, result.output)
    return False

  return True


def StopDaemon(name):
  """Stop daemon

  """
  result = RunCmd([constants.DAEMON_UTIL, "stop", name])
  if result.failed:
    logging.error("Can't stop daemon '%s', failure %s, output: %s",
                  name, result.fail_reason, result.output)
    return False

  return True


def CheckVolumeGroupSize(vglist, vgname, minsize):
  """Checks if the volume group list is valid.

  The function will check if a given volume group is in the list of
  volume groups and has a minimum size.

  @type vglist: dict
  @param vglist: dictionary of volume group names and their size
  @type vgname: str
  @param vgname: the volume group we should check
  @type minsize: int
  @param minsize: the minimum size we accept
  @rtype: None or str
  @return: None for success, otherwise the error message

  """
  vgsize = vglist.get(vgname, None)
  if vgsize is None:
    return "volume group '%s' missing" % vgname
  elif vgsize < minsize:
    return ("volume group '%s' too small (%s MiB required, %d MiB found)" %
            (vgname, minsize, vgsize))
  return None


def SplitTime(value):
  """Splits time as floating point number into a tuple.

  @param value: Time in seconds
  @type value: int or float
  @return: Tuple containing (seconds, microseconds)

  """
  (seconds, microseconds) = divmod(int(value * 1000000), 1000000)

  assert 0 <= seconds, \
    "Seconds must be larger than or equal to 0, but are %s" % seconds
  assert 0 <= microseconds <= 999999, \
    "Microseconds must be 0-999999, but are %s" % microseconds

  return (int(seconds), int(microseconds))


def MergeTime(timetuple):
  """Merges a tuple into time as a floating point number.

  @param timetuple: Time as tuple, (seconds, microseconds)
  @type timetuple: tuple
  @return: Time as a floating point number expressed in seconds

  """
  (seconds, microseconds) = timetuple

  assert 0 <= seconds, \
    "Seconds must be larger than or equal to 0, but are %s" % seconds
  assert 0 <= microseconds <= 999999, \
    "Microseconds must be 0-999999, but are %s" % microseconds

  return float(seconds) + (float(microseconds) * 0.000001)


def FindMatch(data, name):
  """Tries to find an item in a dictionary matching a name.

  Callers have to ensure the data names aren't contradictory (e.g. a regexp
  that matches a string). If the name isn't a direct key, all regular
  expression objects in the dictionary are matched against it.

  @type data: dict
  @param data: Dictionary containing data
  @type name: string
  @param name: Name to look for
  @rtype: tuple; (value in dictionary, matched groups as list)

  """
  if name in data:
    return (data[name], [])

  for key, value in data.items():
    # Regex objects
    if hasattr(key, "match"):
      m = key.match(name)
      if m:
        return (value, list(m.groups()))

  return None


def GetMounts(filename=constants.PROC_MOUNTS):
  """Returns the list of mounted filesystems.

  This function is Linux-specific.

  @param filename: path of mounts file (/proc/mounts by default)
  @rtype: list of tuples
  @return: list of mount entries (device, mountpoint, fstype, options)

  """
  # TODO(iustin): investigate non-Linux options (e.g. via mount output)
  data = []
  mountlines = ReadFile(filename).splitlines()
  for line in mountlines:
    device, mountpoint, fstype, options, _ = line.split(None, 4)
    data.append((device, mountpoint, fstype, options))

  return data


def SignalHandled(signums):
  """Signal Handled decoration.

  This special decorator installs a signal handler and then calls the target
  function. The function must accept a 'signal_handlers' keyword argument,
  which will contain a dict indexed by signal number, with SignalHandler
  objects as values.

  The decorator can be safely stacked with iself, to handle multiple signals
  with different handlers.

  @type signums: list
  @param signums: signals to intercept

  """
  def wrap(fn):
    def sig_function(*args, **kwargs):
      assert 'signal_handlers' not in kwargs or \
             kwargs['signal_handlers'] is None or \
             isinstance(kwargs['signal_handlers'], dict), \
             "Wrong signal_handlers parameter in original function call"
      if 'signal_handlers' in kwargs and kwargs['signal_handlers'] is not None:
        signal_handlers = kwargs['signal_handlers']
      else:
        signal_handlers = {}
        kwargs['signal_handlers'] = signal_handlers
      sighandler = SignalHandler(signums)
      try:
        for sig in signums:
          signal_handlers[sig] = sighandler
        return fn(*args, **kwargs)
      finally:
        sighandler.Reset()
    return sig_function
  return wrap


class SignalWakeupFd(object):
  try:
    # This is only supported in Python 2.5 and above (some distributions
    # backported it to Python 2.4)
    _set_wakeup_fd_fn = signal.set_wakeup_fd
  except AttributeError:
    # Not supported
    def _SetWakeupFd(self, _): # pylint: disable-msg=R0201
      return -1
  else:
    def _SetWakeupFd(self, fd):
      return self._set_wakeup_fd_fn(fd)

  def __init__(self):
    """Initializes this class.

    """
    (read_fd, write_fd) = os.pipe()

    # Once these succeeded, the file descriptors will be closed automatically.
    # Buffer size 0 is important, otherwise .read() with a specified length
    # might buffer data and the file descriptors won't be marked readable.
    self._read_fh = os.fdopen(read_fd, "r", 0)
    self._write_fh = os.fdopen(write_fd, "w", 0)

    self._previous = self._SetWakeupFd(self._write_fh.fileno())

    # Utility functions
    self.fileno = self._read_fh.fileno
    self.read = self._read_fh.read

  def Reset(self):
    """Restores the previous wakeup file descriptor.

    """
    if hasattr(self, "_previous") and self._previous is not None:
      self._SetWakeupFd(self._previous)
      self._previous = None

  def Notify(self):
    """Notifies the wakeup file descriptor.

    """
    self._write_fh.write("\0")

  def __del__(self):
    """Called before object deletion.

    """
    self.Reset()


class SignalHandler(object):
  """Generic signal handler class.

  It automatically restores the original handler when deconstructed or
  when L{Reset} is called. You can either pass your own handler
  function in or query the L{called} attribute to detect whether the
  signal was sent.

  @type signum: list
  @ivar signum: the signals we handle
  @type called: boolean
  @ivar called: tracks whether any of the signals have been raised

  """
  def __init__(self, signum, handler_fn=None, wakeup=None):
    """Constructs a new SignalHandler instance.

    @type signum: int or list of ints
    @param signum: Single signal number or set of signal numbers
    @type handler_fn: callable
    @param handler_fn: Signal handling function

    """
    assert handler_fn is None or callable(handler_fn)

    self.signum = set(signum)
    self.called = False

    self._handler_fn = handler_fn
    self._wakeup = wakeup

    self._previous = {}
    try:
      for signum in self.signum:
        # Setup handler
        prev_handler = signal.signal(signum, self._HandleSignal)
        try:
          self._previous[signum] = prev_handler
        except:
          # Restore previous handler
          signal.signal(signum, prev_handler)
          raise
    except:
      # Reset all handlers
      self.Reset()
      # Here we have a race condition: a handler may have already been called,
      # but there's not much we can do about it at this point.
      raise

  def __del__(self):
    self.Reset()

  def Reset(self):
    """Restore previous handler.

    This will reset all the signals to their previous handlers.

    """
    for signum, prev_handler in self._previous.items():
      signal.signal(signum, prev_handler)
      # If successful, remove from dict
      del self._previous[signum]

  def Clear(self):
    """Unsets the L{called} flag.

    This function can be used in case a signal may arrive several times.

    """
    self.called = False

  def _HandleSignal(self, signum, frame):
    """Actual signal handling function.

    """
    # This is not nice and not absolutely atomic, but it appears to be the only
    # solution in Python -- there are no atomic types.
    self.called = True

    if self._wakeup:
      # Notify whoever is interested in signals
      self._wakeup.Notify()

    if self._handler_fn:
      self._handler_fn(signum, frame)


class FieldSet(object):
  """A simple field set.

  Among the features are:
    - checking if a string is among a list of static string or regex objects
    - checking if a whole list of string matches
    - returning the matching groups from a regex match

  Internally, all fields are held as regular expression objects.

  """
  def __init__(self, *items):
    self.items = [re.compile("^%s$" % value) for value in items]

  def Extend(self, other_set):
    """Extend the field set with the items from another one"""
    self.items.extend(other_set.items)

  def Matches(self, field):
    """Checks if a field matches the current set

    @type field: str
    @param field: the string to match
    @return: either None or a regular expression match object

    """
    for m in itertools.ifilter(None, (val.match(field) for val in self.items)):
      return m
    return None

  def NonMatching(self, items):
    """Returns the list of fields not matching the current set

    @type items: list
    @param items: the list of fields to check
    @rtype: list
    @return: list of non-matching fields

    """
    return [val for val in items if not self.Matches(val)]
