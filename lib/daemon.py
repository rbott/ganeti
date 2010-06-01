#
#

# Copyright (C) 2006, 2007, 2008 Google Inc.
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


"""Module with helper classes and functions for daemons"""


import asyncore
import os
import signal
import logging
import sched
import time
import socket
import select
import sys

from ganeti import utils
from ganeti import constants
from ganeti import errors


class SchedulerBreakout(Exception):
  """Exception used to get out of the scheduler loop

  """


def AsyncoreDelayFunction(timeout):
  """Asyncore-compatible scheduler delay function.

  This is a delay function for sched that, rather than actually sleeping,
  executes asyncore events happening in the meantime.

  After an event has occurred, rather than returning, it raises a
  SchedulerBreakout exception, which will force the current scheduler.run()
  invocation to terminate, so that we can also check for signals. The main loop
  will then call the scheduler run again, which will allow it to actually
  process any due events.

  This is needed because scheduler.run() doesn't support a count=..., as
  asyncore loop, and the scheduler module documents throwing exceptions from
  inside the delay function as an allowed usage model.

  """
  asyncore.loop(timeout=timeout, count=1, use_poll=True)
  raise SchedulerBreakout()


class AsyncoreScheduler(sched.scheduler):
  """Event scheduler integrated with asyncore

  """
  def __init__(self, timefunc):
    sched.scheduler.__init__(self, timefunc, AsyncoreDelayFunction)


class GanetiBaseAsyncoreDispatcher(asyncore.dispatcher):
  """Base Ganeti Asyncore Dispacher

  """
  # this method is overriding an asyncore.dispatcher method
  def handle_error(self):
    """Log an error in handling any request, and proceed.

    """
    logging.exception("Error while handling asyncore request")

  # this method is overriding an asyncore.dispatcher method
  def writable(self):
    """Most of the time we don't want to check for writability.

    """
    return False


class AsyncUDPSocket(GanetiBaseAsyncoreDispatcher):
  """An improved asyncore udp socket.

  """
  def __init__(self):
    """Constructor for AsyncUDPSocket

    """
    GanetiBaseAsyncoreDispatcher.__init__(self)
    self._out_queue = []
    self.create_socket(socket.AF_INET, socket.SOCK_DGRAM)

  # this method is overriding an asyncore.dispatcher method
  def handle_connect(self):
    # Python thinks that the first udp message from a source qualifies as a
    # "connect" and further ones are part of the same connection. We beg to
    # differ and treat all messages equally.
    pass

  # this method is overriding an asyncore.dispatcher method
  def handle_read(self):
    recv_result = utils.IgnoreSignals(self.recvfrom,
                                      constants.MAX_UDP_DATA_SIZE)
    if recv_result is not None:
      payload, address = recv_result
      ip, port = address
      self.handle_datagram(payload, ip, port)

  def handle_datagram(self, payload, ip, port):
    """Handle an already read udp datagram

    """
    raise NotImplementedError

  # this method is overriding an asyncore.dispatcher method
  def writable(self):
    # We should check whether we can write to the socket only if we have
    # something scheduled to be written
    return bool(self._out_queue)

  # this method is overriding an asyncore.dispatcher method
  def handle_write(self):
    if not self._out_queue:
      logging.error("handle_write called with empty output queue")
      return
    (ip, port, payload) = self._out_queue[0]
    utils.IgnoreSignals(self.sendto, payload, 0, (ip, port))
    self._out_queue.pop(0)

  def enqueue_send(self, ip, port, payload):
    """Enqueue a datagram to be sent when possible

    """
    if len(payload) > constants.MAX_UDP_DATA_SIZE:
      raise errors.UdpDataSizeError('Packet too big: %s > %s' % (len(payload),
                                    constants.MAX_UDP_DATA_SIZE))
    self._out_queue.append((ip, port, payload))

  def process_next_packet(self, timeout=0):
    """Process the next datagram, waiting for it if necessary.

    @type timeout: float
    @param timeout: how long to wait for data
    @rtype: boolean
    @return: True if some data has been handled, False otherwise

    """
    result = utils.WaitForFdCondition(self, select.POLLIN, timeout)
    if result is not None and result & select.POLLIN:
      self.handle_read()
      return True
    else:
      return False


class Mainloop(object):
  """Generic mainloop for daemons

  @ivar scheduler: A sched.scheduler object, which can be used to register
    timed events

  """
  def __init__(self):
    """Constructs a new Mainloop instance.

    """
    self._signal_wait = []
    self.scheduler = AsyncoreScheduler(time.time)

  @utils.SignalHandled([signal.SIGCHLD])
  @utils.SignalHandled([signal.SIGTERM])
  @utils.SignalHandled([signal.SIGINT])
  def Run(self, signal_handlers=None):
    """Runs the mainloop.

    @type signal_handlers: dict
    @param signal_handlers: signal->L{utils.SignalHandler} passed by decorator

    """
    assert isinstance(signal_handlers, dict) and \
           len(signal_handlers) > 0, \
           "Broken SignalHandled decorator"
    running = True
    # Start actual main loop
    while running:
      if not self.scheduler.empty():
        try:
          self.scheduler.run()
        except SchedulerBreakout:
          pass
      else:
        asyncore.loop(count=1, use_poll=True)

      # Check whether a signal was raised
      for sig in signal_handlers:
        handler = signal_handlers[sig]
        if handler.called:
          self._CallSignalWaiters(sig)
          running = sig not in (signal.SIGTERM, signal.SIGINT)
          handler.Clear()

  def _CallSignalWaiters(self, signum):
    """Calls all signal waiters for a certain signal.

    @type signum: int
    @param signum: Signal number

    """
    for owner in self._signal_wait:
      owner.OnSignal(signum)

  def RegisterSignal(self, owner):
    """Registers a receiver for signal notifications

    The receiver must support a "OnSignal(self, signum)" function.

    @type owner: instance
    @param owner: Receiver

    """
    self._signal_wait.append(owner)


def GenericMain(daemon_name, optionparser, dirs, check_fn, exec_fn,
                multithreaded=False, console_logging=False,
                default_ssl_cert=None, default_ssl_key=None):
  """Shared main function for daemons.

  @type daemon_name: string
  @param daemon_name: daemon name
  @type optionparser: optparse.OptionParser
  @param optionparser: initialized optionparser with daemon-specific options
                       (common -f -d options will be handled by this module)
  @type dirs: list of (string, integer)
  @param dirs: list of directories that must be created if they don't exist,
               and the permissions to be used to create them
  @type check_fn: function which accepts (options, args)
  @param check_fn: function that checks start conditions and exits if they're
                   not met
  @type exec_fn: function which accepts (options, args)
  @param exec_fn: function that's executed with the daemon's pid file held, and
                  runs the daemon itself.
  @type multithreaded: bool
  @param multithreaded: Whether the daemon uses threads
  @type console_logging: boolean
  @param console_logging: if True, the daemon will fall back to the system
                          console if logging fails
  @type default_ssl_cert: string
  @param default_ssl_cert: Default SSL certificate path
  @type default_ssl_key: string
  @param default_ssl_key: Default SSL key path

  """
  optionparser.add_option("-f", "--foreground", dest="fork",
                          help="Don't detach from the current terminal",
                          default=True, action="store_false")
  optionparser.add_option("-d", "--debug", dest="debug",
                          help="Enable some debug messages",
                          default=False, action="store_true")
  optionparser.add_option("--syslog", dest="syslog",
                          help="Enable logging to syslog (except debug"
                          " messages); one of 'no', 'yes' or 'only' [%s]" %
                          constants.SYSLOG_USAGE,
                          default=constants.SYSLOG_USAGE,
                          choices=["no", "yes", "only"])

  if daemon_name in constants.DAEMONS_PORTS:
    default_bind_address = "0.0.0.0"
    default_port = utils.GetDaemonPort(daemon_name)

    # For networked daemons we allow choosing the port and bind address
    optionparser.add_option("-p", "--port", dest="port",
                            help="Network port (default: %s)" % default_port,
                            default=default_port, type="int")
    optionparser.add_option("-b", "--bind", dest="bind_address",
                            help=("Bind address (default: %s)" %
                                  default_bind_address),
                            default=default_bind_address, metavar="ADDRESS")

  if default_ssl_key is not None and default_ssl_cert is not None:
    optionparser.add_option("--no-ssl", dest="ssl",
                            help="Do not secure HTTP protocol with SSL",
                            default=True, action="store_false")
    optionparser.add_option("-K", "--ssl-key", dest="ssl_key",
                            help=("SSL key path (default: %s)" %
                                  default_ssl_key),
                            default=default_ssl_key, type="string",
                            metavar="SSL_KEY_PATH")
    optionparser.add_option("-C", "--ssl-cert", dest="ssl_cert",
                            help=("SSL certificate path (default: %s)" %
                                  default_ssl_cert),
                            default=default_ssl_cert, type="string",
                            metavar="SSL_CERT_PATH")

  # Disable the use of fork(2) if the daemon uses threads
  utils.no_fork = multithreaded

  options, args = optionparser.parse_args()

  if getattr(options, "ssl", False):
    ssl_paths = {
      "certificate": options.ssl_cert,
      "key": options.ssl_key,
      }

    for name, path in ssl_paths.iteritems():
      if not os.path.isfile(path):
        print >> sys.stderr, "SSL %s file '%s' was not found" % (name, path)
        sys.exit(constants.EXIT_FAILURE)

    # TODO: By initiating http.HttpSslParams here we would only read the files
    # once and have a proper validation (isfile returns False on directories)
    # at the same time.

  if check_fn is not None:
    check_fn(options, args)

  utils.EnsureDirs(dirs)

  if options.fork:
    utils.CloseFDs()
    utils.Daemonize(logfile=constants.DAEMONS_LOGFILES[daemon_name])

  utils.WritePidFile(daemon_name)
  try:
    utils.SetupLogging(logfile=constants.DAEMONS_LOGFILES[daemon_name],
                       debug=options.debug,
                       stderr_logging=not options.fork,
                       multithreaded=multithreaded,
                       program=daemon_name,
                       syslog=options.syslog,
                       console_logging=console_logging)
    logging.info("%s daemon startup", daemon_name)
    exec_fn(options, args)
  finally:
    utils.RemovePidFile(daemon_name)
