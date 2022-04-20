==========================
Ganeti daemons refactoring
==========================

:Created: 2022-Apr-20
:Status: Implemented
:Ganeti-Version: 3.1

.. contents:: :depth: 2

This is a design document explaining the changes inside Ganeti while
transitioning to blockdev backend of QEMU, making the currently used -drive
parameter obsolete.


Current state and shortcomings
==============================

Ganeti's KVM/QEMU code currently uses the `-drive` parameter to add virtual
hard-disks, floppy or CD drives to instances. This approach has been deprecated
and superseded by the newer `-blockdev` parameter since QEMU version 2.1.

Furthermore, the use of `-drive` blocks the transition from QEMU's human
monitor to QMP, as the latter has never seen an implementation of the relevant
methods to hot-add/hot-remove storage devices configured through `-drive`.

Currently, Ganeti QEMU/KVM instances support the following storage devices:

``Virtual Hard-disks``
  An instance can have none to many disks which are represented to guests as the
  selected `disk_type`. Ganeti supports various device types like `paravirtual`
  (VirtIO), `scsi-hd` (along with an emulated SCSI controller), `ide` and the
  like. The disk type can only be set per instance, not per disk.

  A disk's backing storage may be file- or block-based, depending on the
  available disk templates on the node.

  Disks can be hot-added to or hot-removed from running instances.

  An instance may boot off the first disk when not using direct kernel boot and
  specified through the `boot_order` parameter accordingly.

  Ganeti supports various I/O or caching related parameters which may have great
  effect on the instance's disk performance. Recent versions of QEMU (5.0+)
  also support the `io_uring` asynchronous I/O mode which is currently not
  configurable through Ganeti.

``Virtual CD Drives``
  Ganeti allows for up to two virtual CD drives to be attached to an instance.
  The backing storage to a CD drive must be an ISO image, which needs to be
  either a file accessible locally on the node or remotely through a HTTP(S)
  URL. Different bus types (e.g. `ide`, `scsi`, or `paravirtual`) are supported.

  CD drives can not be hot-added to or hot-removed from running instances.

  Instances not using direct kernel boot may boot from the first CD drive, when
  specified through the `boot_order` parameter. If the `boot_order` is set to
  CD, the bus type will silently be overridden to `ide`.

``Virtual Floppy Drive``
  An instance can be configured to provide access to a virtual floppy drive
  using an image file present on the node.

  Floppy drives can not be hot-added to or hot-removed from running instances.

  Instances not using direct kernel boot may boot from the floppy drive, when
  specified through the `boot_order` parameter.


Proposed changes
================

We have to eliminate all uses of the `-drive` parameter from the Ganeti codebase
to ensure compatibility with future versions fo QEMU. With that, we can also
finally cut the last ties to the slow human monitor interface.

Ganeti should support `io_uring` (as long as the QEMU on the node supports it)
and also use the following table (taken from `man 1 qemu-system-x86_64`) to
translate the current values of the `disk_cache` parameter into

Implementation
==============

Hot-Removing Disks
++++++++++++++++++

Hot-adding a disk consists of two steps:

- removing the virtual device (or rather: ask the guest to release it)
- releasing the storage backend

The first step always returns immediately (QMP request `device_del`) but signals
its `actual` result asynchronously through the QMP event `DEVICE_DELETED`.
Ganeti currently does not support receiving QMP events - implementing this will
be out of scope for this change.

In Ganeti releases up to 3.0 the human monitor was used to delete the device.
Executing commands through that interface was very slow (500ms to 1s) and
seemingly slow enough to let the following request to remove the drive succeed
as the guest had enough time to actually release the device.
With the switch to QMP, both requests will fire in direct succession. Since QEMU
cannot release a block device (`blockdev-del` through QMP) which is still in use
by a device inside the guest, hot-removing disks will always fail.

Without support for QMP events, the only feasible way will be to mimic the slow
human monitor interface and block for one second after sending the `device_del`
request to the guest.

I/O Methods
+++++++++++

With QEMU 5.0, support for `io_uring` has been introduced. This should be
supported by Ganeti as well, given a recent enough QEMU version is present on
the node.

Disk Cache
++++++++++

Using the following table found in `man 1 qemu-system-x86_64` we can translate
the disk cache modes known to Ganeti into the settings required by `-blockdev`:

  .. code-block::

    ┌─────────────┬─────────────────┬──────────────┬────────────────┐
    │             │ cache.writeback │ cache.direct │ cache.no-flush │
    ├─────────────┼─────────────────┼──────────────┼────────────────┤
    │writeback    │ on              │ off          │ off            │
    ├─────────────┼─────────────────┼──────────────┼────────────────┤
    │none         │ on              │ on           │ off            │
    ├─────────────┼─────────────────┼──────────────┼────────────────┤
    │writethrough │ off             │ off          │ off            │
    ├─────────────┼─────────────────┼──────────────┼────────────────┤
    │directsync   │ off             │ on           │ off            │
    ├─────────────┼─────────────────┼──────────────┼────────────────┤
    │unsafe       │ on              │ off          │ on             │
    └─────────────┴─────────────────┴──────────────┴────────────────┘

The table also shows `directsync` and `unsafe`, which are currently not
implemented by Ganeti and may be addressed in future changes. The Ganeti value
of `default` should internally be mapped to `writeback`, as that reflects the
values which QEMU assumes when not given explicitly according to the
documentation.


.. vim: set textwidth=72 :
.. Local Variables:
.. mode: rst
.. fill-column: 72
.. End:
