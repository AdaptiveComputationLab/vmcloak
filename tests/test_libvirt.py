#!/usr/bin/env python
from __future__ import print_function
import libvirt
import sys
import os
import tempfile
import IPython

from vmcloak.constants import VMCLOAK_ROOT
from vmcloak.vm import KVM

QEMU_URI = "qemu:///system"
name = 'test_vm'

def test_create_hd():
    disk_path = tempfile.mkstemp()[1]
    kvm = KVM(name=name)
    kvm.create_hd(disk_path)
    return disk_path

def test_dom():
    conn = libvirt.open(QEMU_URI)
    if conn == None:
        print('Failed to open connection to qemu:///system', file=sys.stderr)
        exit(1)

    qemu_temp = os.path.join(VMCLOAK_ROOT, 'data/template/qemu.xml')
    xmlconfig = open(qemu_temp).read()
    dom = conn.defineXML(xmlconfig)
    IPython.embed()
    if dom == None:
        print('Failed to define a domain from an XML definition.', file=sys.stderr)
        exit(1)

    if dom.isActive():
        dom.destroy()

        xml = """
            <disk type="file" device="disk">
            <driver name="qemu" type="qcow2" cache="none"/>
            <source file="{disk_path}"/>
            <target bus="virtio" dev="vda"/>
            </disk>
            """.format(**{"disk_path": test_create_hd()})

        dom.attachDevice(xml)

        if dom.create() < 0:
            print('Can not boot guest domain.', file=sys.stderr)
            exit(1)

        print('Guest '+dom.name()+' has booted', file=sys.stderr)

    conn.close()
    exit()

if __name__ == "__main__":
    test_dom()
