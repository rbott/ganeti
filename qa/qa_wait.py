#
#

# Copyright (C) 2007, 2011, 2012, 2013, 2024 Google Inc.
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


"""Smart waiting utilities with exponential backoff for QA tests.

This module provides utilities to replace fixed time.sleep() calls with
intelligent waiting that uses exponential backoff and condition checking.

"""

import time

from qa import qa_error


def ExponentialBackoff(initial=0.5, maximum=5.0, multiplier=1.5):
  """Generator for exponential backoff intervals.

  @type initial: float
  @param initial: Initial wait interval in seconds
  @type maximum: float
  @param maximum: Maximum wait interval in seconds
  @type multiplier: float
  @param multiplier: Multiplier for each backoff step
  @rtype: generator of floats
  @return: Generator yielding wait intervals

  Example:
    >>> list(itertools.islice(ExponentialBackoff(0.5, 5.0, 2.0), 6))
    [0.5, 1.0, 2.0, 4.0, 5.0, 5.0]

  """
  current = initial
  while True:
    yield current
    current = min(current * multiplier, maximum)


def WaitWithBackoff(condition_fn, timeout=30.0, initial_wait=0.5, max_wait=5.0,
                    multiplier=1.5, error_msg="Condition not met within timeout"):
  """Wait for a condition to become true using exponential backoff.

  @type condition_fn: callable
  @param condition_fn: Function that returns True when condition is met
  @type timeout: float
  @param timeout: Maximum time to wait in seconds
  @type initial_wait: float
  @param initial_wait: Initial wait interval in seconds
  @type max_wait: float
  @param max_wait: Maximum wait interval in seconds
  @type multiplier: float
  @param multiplier: Multiplier for exponential backoff
  @type error_msg: string
  @param error_msg: Error message if timeout is reached
  @raise qa_error.Error: If condition is not met within timeout

  """
  start_time = time.time()
  backoff = ExponentialBackoff(initial_wait, max_wait, multiplier)

  while time.time() - start_time < timeout:
    if condition_fn():
      return

    wait_time = next(backoff)
    # Don't wait past the timeout
    remaining = timeout - (time.time() - start_time)
    if remaining <= 0:
      break
    time.sleep(min(wait_time, remaining))

  # Final check
  if condition_fn():
    return

  raise qa_error.Error("%s (waited %.1f seconds)" %
                       (error_msg, time.time() - start_time))


def WaitForCondition(condition_fn, timeout=30.0, initial_wait=0.5, max_wait=5.0,
                     error_msg="Condition not met within timeout"):
  """Wait for a condition to become true using exponential backoff.

  Similar to WaitWithBackoff but with a default multiplier of 1.5.
  This is the recommended function for most use cases.

  @type condition_fn: callable
  @param condition_fn: Function that returns True when condition is met
  @type timeout: float
  @param timeout: Maximum time to wait in seconds
  @type initial_wait: float
  @param initial_wait: Initial wait interval in seconds
  @type max_wait: float
  @param max_wait: Maximum wait interval in seconds
  @type error_msg: string
  @param error_msg: Error message if timeout is reached
  @raise qa_error.Error: If condition is not met within timeout

  """
  WaitWithBackoff(condition_fn, timeout=timeout, initial_wait=initial_wait,
                  max_wait=max_wait, multiplier=1.5, error_msg=error_msg)


def SmartSleep(duration, check_fn=None, check_interval=0.5):
  """Sleep for a duration, optionally checking a condition periodically.

  If check_fn is provided and returns True, sleep is interrupted early.
  This is useful for cases where we need to wait a certain time but can
  exit early if a condition is met.

  @type duration: float
  @param duration: Time to sleep in seconds
  @type check_fn: callable or None
  @param check_fn: Optional function to check during sleep; if returns True,
                   sleep is interrupted
  @type check_interval: float
  @param check_interval: How often to check the condition (seconds)

  """
  if check_fn is None:
    time.sleep(duration)
    return

  start_time = time.time()
  while time.time() - start_time < duration:
    if check_fn():
      return
    remaining = duration - (time.time() - start_time)
    time.sleep(min(check_interval, remaining))


def WaitForProcessState(process_name, node, running=True, timeout=10.0,
                        initial_wait=0.3, max_wait=2.0):
  """Wait for a process to be running or stopped on a node.

  Uses exponential backoff to efficiently wait for process state changes.

  @type process_name: string
  @param process_name: Name of the process to check (for pgrep)
  @type node: string or qa_config._QaNode
  @param node: Node to check the process on
  @type running: bool
  @param running: If True, wait for process to be running; if False, wait for
                  it to be stopped
  @type timeout: float
  @param timeout: Maximum time to wait in seconds
  @type initial_wait: float
  @param initial_wait: Initial wait interval in seconds
  @type max_wait: float
  @param max_wait: Maximum wait interval in seconds
  @raise qa_error.Error: If process doesn't reach desired state within timeout

  """
  from qa import qa_utils  # Avoid circular import

  if isinstance(node, str):
    node_name = node
  else:
    node_name = node.primary

  def check_process():
    cmd = "pgrep %s" % process_name
    popen = qa_utils.StartSSH(node_name, cmd, log_cmd=False)
    popen.communicate()
    is_running = (popen.returncode == 0)
    return is_running == running

  state_name = "running" if running else "stopped"
  error_msg = ("Process '%s' on node %s did not reach state '%s'" %
               (process_name, node_name, state_name))

  WaitWithBackoff(check_process, timeout=timeout, initial_wait=initial_wait,
                  max_wait=max_wait, error_msg=error_msg)


def WaitForJobsToFinish(timeout=60.0, initial_wait=0.5, max_wait=2.0):
  """Wait for all running jobs to finish using exponential backoff.

  @type timeout: float
  @param timeout: Maximum time to wait in seconds
  @type initial_wait: float
  @param initial_wait: Initial wait interval in seconds
  @type max_wait: float
  @param max_wait: Maximum wait interval in seconds
  @raise qa_error.Error: If jobs don't finish within timeout

  """
  from qa_filters import stdout_of  # Avoid circular import

  def no_jobs_running():
    return stdout_of(["gnt-job", "list", "--no-header", "--running"]) == ""

  WaitWithBackoff(no_jobs_running, timeout=timeout, initial_wait=initial_wait,
                  max_wait=max_wait,
                  error_msg="Jobs did not finish within timeout")


def WaitForInstanceState(instance_name, running=True, timeout=15.0,
                         initial_wait=0.5, max_wait=3.0):
  """Wait for an instance to be running or stopped.

  @type instance_name: string
  @param instance_name: Full name of the instance
  @type running: bool
  @param running: If True, wait for instance to be running; if False, wait for
                  it to be stopped
  @type timeout: float
  @param timeout: Maximum time to wait in seconds
  @type initial_wait: float
  @param initial_wait: Initial wait interval in seconds
  @type max_wait: float
  @param max_wait: Maximum wait interval in seconds
  @raise qa_error.Error: If instance doesn't reach desired state within timeout

  """
  from qa import qa_config, qa_utils  # Avoid circular import
  from ganeti import utils

  master = qa_config.GetMasterNode()

  def check_instance():
    cmd = (utils.ShellQuoteArgs(["gnt-instance", "list", "-o", "status",
                                  instance_name]) + ' | grep running')
    popen = qa_utils.StartSSH(master.primary, cmd, log_cmd=False)
    popen.communicate()
    is_running = (popen.returncode == 0)
    return is_running == running

  state_name = "running" if running else "stopped"
  error_msg = ("Instance '%s' did not reach state '%s'" %
               (instance_name, state_name))

  WaitWithBackoff(check_instance, timeout=timeout, initial_wait=initial_wait,
                  max_wait=max_wait, error_msg=error_msg)


def WaitForJobStatus(job_id, expected_status, timeout=20.0,
                     initial_wait=0.5, max_wait=2.0):
  """Wait for a job to reach a specific status using exponential backoff.

  @type job_id: string or int
  @param job_id: Job ID to check
  @type expected_status: string
  @param expected_status: Expected job status (e.g., "success", "running")
  @type timeout: float
  @param timeout: Maximum time to wait in seconds
  @type initial_wait: float
  @param initial_wait: Initial wait interval in seconds
  @type max_wait: float
  @param max_wait: Maximum wait interval in seconds
  @raise qa_error.Error: If job doesn't reach status within timeout

  """
  from qa import qa_job_utils  # Avoid circular import

  def check_status():
    current_status = qa_job_utils.GetJobStatus(str(job_id))
    return current_status == expected_status

  error_msg = ("Job %s did not reach status '%s'" % (job_id, expected_status))

  WaitWithBackoff(check_status, timeout=timeout, initial_wait=initial_wait,
                  max_wait=max_wait, error_msg=error_msg)


# Convenience functions with sensible defaults for common scenarios

def WaitForDaemonCycle(daemon_name="ganeti-kvmd", nodes=None, running=True,
                       timeout=8.0):
  """Wait for a Ganeti daemon to start or stop on multiple nodes.

  Uses optimized exponential backoff (0.3s -> 2.0s) which is faster than
  the previous fixed 5-second waits while still being reliable.

  @type daemon_name: string
  @param daemon_name: Name of the daemon process
  @type nodes: list of nodes or None
  @param nodes: List of nodes to check; if None, checks are skipped
  @type running: bool
  @param running: Expected state (True for running, False for stopped)
  @type timeout: float
  @param timeout: Maximum time to wait per node in seconds

  """
  if nodes is None:
    return

  for node in nodes:
    WaitForProcessState(daemon_name, node, running=running, timeout=timeout,
                        initial_wait=0.3, max_wait=2.0)


def WaitForWatcherAction(timeout=8.0):
  """Wait for watcher to perform an action (e.g., restart instance).

  Uses exponential backoff (0.5s -> 3.0s) which is typically faster than
  the previous fixed 5-second waits.

  @type timeout: float
  @param timeout: Maximum time to wait in seconds

  """
  # Wait a bit for watcher to complete its action
  # Start checking quickly as watcher actions are usually fast
  start_time = time.time()
  backoff = ExponentialBackoff(initial=0.5, maximum=3.0, multiplier=1.8)

  while time.time() - start_time < timeout:
    wait_time = next(backoff)
    remaining = timeout - (time.time() - start_time)
    if remaining <= 0:
      break
    time.sleep(min(wait_time, remaining))


def WaitForFilterEffect(timeout=6.0):
  """Wait for filter/scheduler to process pending jobs.

  Uses exponential backoff (0.3s -> 2.0s) which is faster than
  the previous fixed 5-second waits for filter effects to take place.

  @type timeout: float
  @param timeout: Maximum time to wait in seconds

  """
  # Filters and scheduler effects are usually quick
  start_time = time.time()
  backoff = ExponentialBackoff(initial=0.3, maximum=2.0, multiplier=2.0)

  while time.time() - start_time < timeout:
    wait_time = next(backoff)
    remaining = timeout - (time.time() - start_time)
    if remaining <= 0:
      break
    time.sleep(min(wait_time, remaining))


def WaitForJobQueuePolling(pending_check_fn, timeout=120.0, initial_wait=0.5,
                           max_wait=3.0):
  """Wait for job queue with optimized polling for performance tests.

  Uses exponential backoff (0.5s -> 3.0s) which reduces polling overhead
  compared to fixed 2-second intervals.

  @type pending_check_fn: callable
  @param pending_check_fn: Function that returns True if jobs are still pending
  @type timeout: float
  @param timeout: Maximum time to wait in seconds
  @type initial_wait: float
  @param initial_wait: Initial wait interval in seconds
  @type max_wait: float
  @param max_wait: Maximum wait interval in seconds

  """
  def jobs_completed():
    return not pending_check_fn()

  WaitWithBackoff(jobs_completed, timeout=timeout, initial_wait=initial_wait,
                  max_wait=max_wait,
                  error_msg="Jobs did not complete within timeout")
